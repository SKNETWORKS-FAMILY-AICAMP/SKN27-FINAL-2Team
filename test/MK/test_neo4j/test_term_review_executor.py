import sys
import tempfile
import unittest
from json import dumps, loads
from pathlib import Path
from types import SimpleNamespace


class TermReviewExecutorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        cls.neo4j_root = neo4j_root
        sys.path.insert(0, str(neo4j_root))

        from common import load_pipeline_policy
        from entity_resolution.execute_term_review import (
            apply_controlled_decision_fields,
            build_execution_plan,
            execute_term_review_tasks,
            load_json_schema,
            request_term_decision,
            validate_executor_decision,
            validate_structured_output_schema,
        )

        cls.apply_controlled_decision_fields = staticmethod(
            apply_controlled_decision_fields
        )
        cls.build_execution_plan = staticmethod(build_execution_plan)
        cls.execute_term_review_tasks = staticmethod(execute_term_review_tasks)
        cls.load_json_schema = staticmethod(load_json_schema)
        cls.request_term_decision = staticmethod(request_term_decision)
        cls.validate_executor_decision = staticmethod(
            validate_executor_decision
        )
        cls.validate_structured_output_schema = staticmethod(
            validate_structured_output_schema
        )
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def make_task(self, task_number: int) -> dict:
        return {
            "term_review_task_id": f"task-{task_number}",
            "resolution_case_id": f"case-{task_number}",
            "canonical_term": f"용어-{task_number}",
            "entity_type_proposal": "Person",
            "source_candidates": [
                {"source_candidate_id": f"candidate-{task_number}-1"},
                {"source_candidate_id": f"candidate-{task_number}-2"},
            ],
        }

    def make_decision(self, task: dict) -> dict:
        candidate_ids = [
            candidate["source_candidate_id"]
            for candidate in task["source_candidates"]
        ]
        decision = {
            "term_review_task_id": "spoofed-task",
            "resolution_case_id": "spoofed-case",
            "decision_status": "ACCEPTED",
            "review_model": "spoofed-model",
            "prompt_version": "spoofed-prompt",
            "proposed_alternatives": [
                {
                    "display_name": "검수 인물",
                    "entity_type": "Person",
                    "identity_member_source_candidate_ids": [candidate_ids[0]],
                    "reason": "동일 인물 문서다.",
                }
            ],
            "evidence_only_sources": [],
            "rejected_sources": [
                {
                    "source_candidate_id": candidate_ids[1],
                    "reason": "다른 인물이다.",
                }
            ],
            "ambiguous_sources": [],
            "decision_reason": "후보를 구분했다.",
        }
        return self.apply_controlled_decision_fields(
            decision,
            task,
            self.policy,
        )

    def test_controlled_fields_override_model_output(self):
        task = self.make_task(1)
        decision = self.make_decision(task)
        semantic_policy = self.policy["entity_resolution"]["semantic_review"]

        self.assertEqual(decision["term_review_task_id"], "task-1")
        self.assertEqual(decision["resolution_case_id"], "case-1")
        self.assertEqual(decision["decision_status"], "PROPOSED")
        self.assertEqual(
            decision["review_model"],
            semantic_policy["term_model"]["model"],
        )
        self.assertEqual(
            decision["prompt_version"],
            semantic_policy["prompt_version"],
        )

    def test_request_uses_current_review_metadata_without_mutating_task(self):
        task = self.make_task(1)
        task["review_model"] = "previous-model"
        task["prompt_version"] = "previous-prompt"
        semantic_policy = self.policy["entity_resolution"]["semantic_review"]
        captured_arguments: dict = {}

        class FakeResponses:
            def create(self, **request_arguments):
                captured_arguments.update(request_arguments)
                return SimpleNamespace(
                    id="response-1",
                    output_text=dumps(
                        self_test.make_decision(task),
                        ensure_ascii=False,
                    ),
                    usage=None,
                )

        self_test = self
        client = SimpleNamespace(responses=FakeResponses())
        self.request_term_decision(client, task, "prompt", {}, self.policy)
        request_task = loads(captured_arguments["input"])

        self.assertEqual(
            request_task["review_model"],
            semantic_policy["term_model"]["model"],
        )
        self.assertEqual(
            request_task["prompt_version"],
            semantic_policy["prompt_version"],
        )
        self.assertEqual(task["review_model"], "previous-model")
        self.assertEqual(task["prompt_version"], "previous-prompt")

    def test_missing_candidate_is_rejected_before_checkpoint(self):
        task = self.make_task(1)
        decision = self.make_decision(task)
        decision["rejected_sources"] = []

        errors = self.validate_executor_decision(
            decision,
            task,
            self.policy,
        )

        self.assertTrue(
            any("MISSING_CANDIDATE_CLASSIFICATION" in error for error in errors)
        )

    def test_successful_checkpoint_is_reused(self):
        tasks = [self.make_task(1), self.make_task(2)]
        calls: list[str] = []

        def requester(client, task, prompt, schema, policy):
            calls.append(task["term_review_task_id"])
            return self.make_decision(task), {
                "response_id": f"response-{task['term_review_task_id']}",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

        def unexpected_requester(client, task, prompt, schema, policy):
            raise AssertionError("호환 checkpoint task를 다시 호출했습니다.")

        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_path = str(Path(temp_dir) / "checkpoint.jsonl")
            first_result = self.execute_term_review_tasks(
                tasks,
                "prompt",
                {},
                checkpoint_path,
                self.policy,
                object(),
                requester=requester,
            )
            second_result = self.execute_term_review_tasks(
                tasks,
                "prompt",
                {},
                checkpoint_path,
                self.policy,
                object(),
                requester=unexpected_requester,
            )

        self.assertEqual(sorted(calls), ["task-1", "task-2"])
        self.assertEqual(first_result["succeeded_count"], 2)
        self.assertEqual(second_result["attempted_count"], 0)
        self.assertEqual(second_result["reused_checkpoint_count"], 2)
        self.assertEqual(len(second_result["decisions"]), 2)

    def test_failed_attempt_is_retried_and_not_omitted(self):
        task = self.make_task(1)
        attempt_count = 0

        def requester(client, current_task, prompt, schema, policy):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                raise ValueError("temporary failure")
            return self.make_decision(current_task), {}

        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.execute_term_review_tasks(
                [task],
                "prompt",
                {},
                str(Path(temp_dir) / "checkpoint.jsonl"),
                self.policy,
                object(),
                maximum_retries=1,
                requester=requester,
            )

        self.assertEqual(attempt_count, 2)
        self.assertEqual(result["succeeded_count"], 1)
        self.assertEqual(result["failed_count"], 0)

    def test_dry_run_plan_respects_limit(self):
        tasks = [self.make_task(1), self.make_task(2), self.make_task(3)]
        with tempfile.TemporaryDirectory() as temp_dir:
            plan = self.build_execution_plan(
                tasks,
                str(Path(temp_dir) / "checkpoint.jsonl"),
                self.policy,
                2,
            )

        self.assertEqual(plan["selected_task_count"], 2)
        self.assertEqual(plan["pending_task_count"], 2)

    def test_structured_output_schema_is_checked_before_requests(self):
        schema_directory = self.neo4j_root / "config" / "schemas"
        for schema_name in [
            "term_resolution_decision.schema.json",
            "problem_resolution_decision.schema.json",
        ]:
            schema = self.load_json_schema(
                str(schema_directory / schema_name)
            )
            self.assertEqual(
                self.validate_structured_output_schema(schema, self.policy),
                [],
            )

        request_count = 0

        def requester(client, task, prompt, schema, policy):
            nonlocal request_count
            request_count += 1
            return self.make_decision(task), {}

        unsupported_schema = {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "uniqueItems"):
                self.execute_term_review_tasks(
                    [self.make_task(1)],
                    "prompt",
                    unsupported_schema,
                    str(Path(temp_dir) / "checkpoint.jsonl"),
                    self.policy,
                    object(),
                    requester=requester,
                )

        self.assertEqual(request_count, 0)


if __name__ == "__main__":
    unittest.main()

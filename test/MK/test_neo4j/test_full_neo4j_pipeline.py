import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd


class FullNeo4jPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[3]
        cls.neo4j_root = (
            cls.project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(cls.neo4j_root))

        from common import load_pipeline_policy
        from entity_resolution.execute_problem_review import (
            apply_controlled_problem_fields,
            build_problem_execution_plan,
            execute_problem_review_tasks,
        )
        from entity_resolution.load_final_identity import (
            build_final_identity_load_plan,
        )
        from run_full_neo4j_pipeline import (
            resolve_full_pipeline_paths,
            validate_goldset_safety_gate,
        )

        cls.policy = load_pipeline_policy(
            str(cls.neo4j_root / "config" / "resolution_policy.json")
        )
        cls.apply_controlled_problem_fields = staticmethod(
            apply_controlled_problem_fields
        )
        cls.build_problem_execution_plan = staticmethod(
            build_problem_execution_plan
        )
        cls.execute_problem_review_tasks = staticmethod(
            execute_problem_review_tasks
        )
        cls.build_final_identity_load_plan = staticmethod(
            build_final_identity_load_plan
        )
        cls.resolve_full_pipeline_paths = staticmethod(
            resolve_full_pipeline_paths
        )
        cls.validate_goldset_safety_gate = staticmethod(
            validate_goldset_safety_gate
        )

    def build_problem_task(self) -> dict:
        return {
            "problem_review_task_id": "problem-task-1",
            "problem_assignment_id": "assignment-1",
            "problem_id": "problem-1",
            "resolution_case_id": "case-1",
            "canonical_alternatives": [
                {"canonical_alternative_id": "alternative-1"},
                {"canonical_alternative_id": "alternative-2"},
            ],
        }

    def build_final_tables(self) -> dict[str, pd.DataFrame]:
        return {
            "canonical_registry": pd.DataFrame(
                [{"canonical_id": "canonical-1"}]
            ),
            "exam_term_nodes": pd.DataFrame(
                [{"exam_term_id": "exam-term-1"}]
            ),
            "canonical_entity_nodes": pd.DataFrame(
                [
                    {
                        "canonical_id": "canonical-1",
                        "display_name": "이순신",
                        "entity_type": "Person",
                        "lifecycle_status": "ACTIVE",
                        "registry_version": "registry-v1",
                    }
                ]
            ),
            "source_record_nodes": pd.DataFrame(
                [
                    {
                        "source_record_id": "source-1",
                        "source": "AKS",
                        "source_key": "E1",
                        "source_release": "release-1",
                        "source_metadata_json": "{}",
                    }
                ]
            ),
            "entity_name_nodes": pd.DataFrame(
                [
                    {
                        "entity_name_id": "name-1",
                        "name": "이순신",
                        "normalized_name": "이순신",
                        "name_type": "CANONICAL_TERM",
                        "normalization_policy_version": "normalization-v1",
                    }
                ]
            ),
            "source_record_resolutions": pd.DataFrame(
                [
                    {
                        "source_record_id": "source-1",
                        "canonical_id": "canonical-1",
                        "match_status": "ACCEPTED",
                        "method": "verified",
                        "version": "policy-v1",
                        "term_decision_id": "decision-1",
                    }
                ]
            ),
            "entity_name_references": pd.DataFrame(
                [
                    {
                        "entity_name_id": "name-1",
                        "canonical_id": "canonical-1",
                        "match_status": "ACCEPTED",
                        "method": "verified",
                        "version": "policy-v1",
                    }
                ]
            ),
            "exam_term_references": pd.DataFrame(
                [
                    {
                        "exam_term_id": "exam-term-1",
                        "canonical_id": "canonical-1",
                        "match_status": "ACCEPTED",
                    }
                ]
            ),
            "topic_nodes": pd.DataFrame(
                [
                    {
                        "topic_id": "topic:person",
                        "name": "인물",
                        "status": "ACTIVE",
                        "version": "classification-v1",
                    }
                ]
            ),
            "era_nodes": pd.DataFrame(
                [
                    {
                        "era_id": "era:joseon",
                        "name": "조선",
                        "status": "ACTIVE",
                        "version": "classification-v1",
                    }
                ]
            ),
            "canonical_topic_relationships": pd.DataFrame(
                [
                    {
                        "canonical_id": "canonical-1",
                        "topic_id": "topic:person",
                        "verification_status": "VERIFIED",
                    }
                ]
            ),
            "canonical_era_relationships": pd.DataFrame(
                [
                    {
                        "canonical_id": "canonical-1",
                        "era_id": "era:joseon",
                        "verification_status": "VERIFIED",
                    }
                ]
            ),
            "canonical_classification_review": pd.DataFrame(),
            "final_problem_assignments": pd.DataFrame(),
            "canonical_acceptance_review_queue": pd.DataFrame(),
        }

    def test_full_pipeline_paths_use_separate_final_directory(self):
        paths = self.resolve_full_pipeline_paths(
            self.neo4j_root,
            str(self.neo4j_root / "output"),
            self.policy,
        )

        self.assertEqual(
            paths["final_identity_directory"],
            self.neo4j_root / "output" / "final_identity",
        )
        self.assertEqual(
            paths["pipeline_manifest"],
            self.neo4j_root / "output" / "full_pipeline_manifest.json",
        )

    def test_goldset_gate_requires_safe_pair_precision(self):
        with TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "status": "COMPLETED",
                        "resolution_policy_version": self.policy[
                            "policy_version"
                        ],
                        "evaluation_metrics": {
                            "auto_accepted_identity_pair_precision": 0.9,
                            "verified_false_merge_pair_count": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = self.validate_goldset_safety_gate(
                manifest_path,
                self.policy,
            )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(result["errors"]), 1)

    def test_problem_executor_controls_identifiers_and_versions(self):
        task = self.build_problem_task()
        decision = self.apply_controlled_problem_fields(
            {
                "selection_mode": "SINGLE",
                "selected_canonical_alternative_ids": ["alternative-1"],
                "reason": "문맥상 첫 번째 대안",
            },
            task,
            self.policy,
        )

        self.assertEqual(
            decision["problem_review_task_id"],
            task["problem_review_task_id"],
        )
        self.assertEqual(decision["decision_status"], "PROPOSED")
        self.assertEqual(
            decision["review_model"],
            self.policy["entity_resolution"]["semantic_review"][
                "problem_model"
            ]["model"],
        )

    def test_problem_executor_reuses_compatible_checkpoint(self):
        task = self.build_problem_task()

        def requester(
            client: object,
            active_task: dict,
            prompt: str,
            schema: dict,
            policy: dict,
        ) -> tuple[dict, dict]:
            decision = self.apply_controlled_problem_fields(
                {
                    "selection_mode": "SINGLE",
                    "selected_canonical_alternative_ids": [
                        "alternative-1"
                    ],
                    "reason": "문맥상 첫 번째 대안",
                },
                active_task,
                policy,
            )
            return decision, {"response_id": "response-1", "usage": {}}

        with TemporaryDirectory() as temporary_directory:
            checkpoint_path = str(
                Path(temporary_directory) / "checkpoint.jsonl"
            )
            first_result = self.execute_problem_review_tasks(
                [task],
                "prompt",
                {},
                checkpoint_path,
                self.policy,
                object(),
                requester=requester,
            )
            plan = self.build_problem_execution_plan(
                [task],
                checkpoint_path,
                self.policy,
                0,
            )

        self.assertEqual(first_result["succeeded_count"], 1)
        self.assertEqual(plan["reused_checkpoint_count"], 1)
        self.assertEqual(plan["pending_task_count"], 0)

    def test_final_identity_load_plan_blocks_unknown_endpoint(self):
        tables = self.build_final_tables()
        ready_plan = self.build_final_identity_load_plan(tables)
        tables["source_record_resolutions"].loc[
            0,
            "canonical_id",
        ] = "unknown-canonical"
        blocked_plan = self.build_final_identity_load_plan(tables)

        self.assertEqual(ready_plan["status"], "READY")
        self.assertEqual(blocked_plan["status"], "BLOCKED")
        self.assertTrue(blocked_plan["validation_errors"])


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path

import pandas as pd


class ProblemReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))
        sys.path.insert(0, str(neo4j_root / "terms"))

        from common import load_pipeline_policy
        from entity_resolution.problem_review import (
            build_problem_review_inputs,
            validate_problem_decisions,
        )

        cls.build_inputs = staticmethod(build_problem_review_inputs)
        cls.validate_decisions = staticmethod(validate_problem_decisions)
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def build_fixture(self, alternative_count: int = 2):
        resolution_tables = {
            "resolution_cases": pd.DataFrame(
                [
                    {
                        "resolution_case_id": "case-gojong",
                        "canonical_term": "고종",
                        "category": "인물",
                    }
                ]
            ),
            "problem_contexts": pd.DataFrame(
                [
                    {
                        "problem_id": "question-1",
                        "full_text": "강화도 조약을 체결한 고종에 대한 문항",
                    },
                    {
                        "problem_id": "question-2",
                        "full_text": "몽골 침입기 고종에 대한 문항",
                    },
                ]
            ),
            "problem_resolution_assignments": pd.DataFrame(
                [
                    {
                        "problem_assignment_id": "assignment-1",
                        "problem_id": "question-1",
                        "resolution_case_id": "case-gojong",
                    },
                    {
                        "problem_assignment_id": "assignment-2",
                        "problem_id": "question-2",
                        "resolution_case_id": "case-gojong",
                    },
                ]
            ),
        }
        alternatives = [
            {
                "canonical_alternative_id": "alternative-joseon",
                "resolution_case_id": "case-gojong",
                "display_name_proposal": "고종(조선)",
                "entity_type_proposal": "Person",
                "identity_member_source_ids_json": json.dumps(
                    ["AKS:ARTICLE:JOSEON:release"],
                    ensure_ascii=False,
                ),
                "decision_reason": "조선의 고종",
                "verification_status": "VERIFIED",
            },
            {
                "canonical_alternative_id": "alternative-goryeo",
                "resolution_case_id": "case-gojong",
                "display_name_proposal": "고종(고려)",
                "entity_type_proposal": "Person",
                "identity_member_source_ids_json": json.dumps(
                    ["AKS:ARTICLE:GORYEO:release"],
                    ensure_ascii=False,
                ),
                "decision_reason": "고려의 고종",
                "verification_status": "VERIFIED",
            },
        ][:alternative_count]
        term_tables = {
            "term_resolution_decisions": pd.DataFrame(
                [
                    {
                        "resolution_case_id": "case-gojong",
                        "verification_status": "VERIFIED",
                    }
                ]
            ),
            "reviewed_canonical_alternatives": pd.DataFrame(alternatives),
        }
        return resolution_tables, term_tables

    def make_decision(self, task: dict) -> dict:
        return {
            "problem_review_task_id": task["problem_review_task_id"],
            "problem_assignment_id": task["problem_assignment_id"],
            "resolution_case_id": task["resolution_case_id"],
            "decision_status": "PROPOSED",
            "review_model": self.policy["entity_resolution"][
                "semantic_review"
            ]["problem_model"]["model"],
            "prompt_version": self.policy["entity_resolution"][
                "semantic_review"
            ]["problem_prompt_version"],
            "selection_mode": "SINGLE",
            "selected_canonical_alternative_ids": [
                "alternative-joseon"
            ],
            "reason": "강화도 조약 문맥은 조선 고종이다.",
        }

    def test_multiple_alternatives_create_problem_level_tasks(self):
        resolution_tables, term_tables = self.build_fixture()

        tasks, deterministic = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )

        self.assertEqual(len(tasks), 2)
        self.assertTrue(deterministic.empty)
        self.assertEqual(len(tasks[0]["canonical_alternatives"]), 2)
        self.assertTrue(tasks[0]["problem_full_text"])

    def test_single_verified_alternative_is_deterministically_assigned(self):
        resolution_tables, term_tables = self.build_fixture(
            alternative_count=1
        )

        tasks, deterministic = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )

        self.assertFalse(tasks)
        self.assertEqual(len(deterministic), 2)
        self.assertEqual(set(deterministic["selection_mode"]), {"SINGLE"})
        self.assertEqual(
            set(deterministic["resolution_method"]),
            {"structured_rule"},
        )

    def test_valid_problem_selection_is_verified(self):
        resolution_tables, term_tables = self.build_fixture()
        tasks, deterministic = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )
        decision = self.make_decision(tasks[0])

        outputs = self.validate_decisions(
            [decision],
            tasks,
            deterministic,
            self.policy,
        )

        self.assertEqual(
            outputs["problem_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "VERIFIED",
        )
        self.assertEqual(len(outputs["verified_problem_assignments"]), 1)

    def test_unknown_alternative_is_invalid(self):
        resolution_tables, term_tables = self.build_fixture()
        tasks, deterministic = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )
        decision = self.make_decision(tasks[0])
        decision["selected_canonical_alternative_ids"] = ["not-an-option"]

        outputs = self.validate_decisions(
            [decision],
            tasks,
            deterministic,
            self.policy,
        )

        self.assertEqual(
            outputs["problem_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "INVALID",
        )
        self.assertIn(
            "UNKNOWN_CANONICAL_ALTERNATIVE",
            set(outputs["problem_decision_validation_errors"]["error_code"]),
        )

    def test_ambiguous_selection_requires_manual_review(self):
        resolution_tables, term_tables = self.build_fixture()
        tasks, deterministic = self.build_inputs(
            resolution_tables,
            term_tables,
            self.policy,
        )
        decision = self.make_decision(tasks[0])
        decision["selection_mode"] = "AMBIGUOUS"
        decision["selected_canonical_alternative_ids"] = []

        outputs = self.validate_decisions(
            [decision],
            tasks,
            deterministic,
            self.policy,
        )

        self.assertEqual(
            outputs["problem_resolution_decisions"].iloc[0][
                "verification_status"
            ],
            "NEEDS_MANUAL_REVIEW",
        )


if __name__ == "__main__":
    unittest.main()

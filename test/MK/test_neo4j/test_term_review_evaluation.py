import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


class TermReviewEvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))

        from common import load_pipeline_policy
        from entity_resolution.evaluate_term_review import (
            evaluate_term_decisions,
            write_evaluation_outputs,
        )

        cls.evaluate_term_decisions = staticmethod(evaluate_term_decisions)
        cls.write_evaluation_outputs = staticmethod(write_evaluation_outputs)
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def make_task(self) -> dict:
        return {
            "term_review_task_id": "task-1",
            "resolution_case_id": "case-1",
            "canonical_term": "동명 용어",
            "category": "인물",
            "source_candidates": [
                {"source_candidate_id": candidate_id}
                for candidate_id in [
                    "candidate-a",
                    "candidate-b",
                    "candidate-c",
                    "candidate-d",
                ]
            ],
            "gold_set_metadata": {
                "candidate_count_bucket": "C03_05",
                "retrieval_profile": "EXACT_AND_EXPANDED",
                "multi_source_supported": True,
                "conflict_present": False,
            },
        }

    def make_decision(
        self,
        clusters: list[list[str]],
        evidence: list[str],
        rejected: list[str],
        ambiguous: list[str] | None = None,
    ) -> dict:
        ambiguous_ids = ambiguous or []
        return {
            "term_review_task_id": "task-1",
            "resolution_case_id": "case-1",
            "decision_status": "PROPOSED",
            "review_model": "evaluation-fixture",
            "prompt_version": "evaluation-fixture-v1",
            "proposed_alternatives": [
                {
                    "display_name": f"대안 {index}",
                    "entity_type": "Person",
                    "identity_member_source_candidate_ids": member_ids,
                    "reason": "동일 인물로 판정했다.",
                }
                for index, member_ids in enumerate(clusters, start=1)
            ],
            "evidence_only_sources": [
                {
                    "source_candidate_id": candidate_id,
                    "reason": "정체성 원천이 아닌 보조 근거다.",
                }
                for candidate_id in evidence
            ],
            "rejected_sources": [
                {
                    "source_candidate_id": candidate_id,
                    "reason": "다른 대상을 설명한다.",
                }
                for candidate_id in rejected
            ],
            "ambiguous_sources": [
                {
                    "source_candidate_id": candidate_id,
                    "reason": "현재 근거로 확정할 수 없다.",
                }
                for candidate_id in ambiguous_ids
            ],
            "decision_reason": "평가용 판정이다.",
        }

    def make_gold_decision(self) -> dict:
        return self.make_decision(
            [["candidate-a", "candidate-b"]],
            ["candidate-d"],
            ["candidate-c"],
        )

    def make_outcomes(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "term_review_task_id": "task-1",
                    "gold_link_status": "ACCEPTED",
                    "requires_problem_review": "NO",
                }
            ]
        )

    def evaluate(self, predicted_decisions: list[dict]) -> dict[str, object]:
        return self.evaluate_term_decisions(
            [self.make_gold_decision()],
            predicted_decisions,
            [self.make_task()],
            self.make_outcomes(),
            self.policy,
        )

    def test_exact_prediction_scores_one_and_outputs_all_files(self):
        outputs = self.evaluate([self.make_gold_decision()])
        metrics = outputs["metrics"]

        self.assertEqual(metrics["prediction_coverage"], 1.0)
        self.assertEqual(metrics["candidate_role_accuracy"], 1.0)
        self.assertEqual(metrics["candidate_role_macro_f1"], 1.0)
        self.assertEqual(metrics["cluster_exact_case_rate"], 1.0)
        self.assertEqual(metrics["identity_pair_f1"], 1.0)
        self.assertEqual(metrics["link_status_accuracy"], 1.0)
        self.assertEqual(metrics["problem_review_accuracy"], 1.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            written = self.write_evaluation_outputs(
                outputs,
                temp_dir,
                self.policy,
            )
            files_exist = all(
                Path(path).is_file() for path in written.values()
            )
            self.assertTrue(files_exist)

    def test_false_merge_is_counted_as_pair_false_positive(self):
        prediction = self.make_decision(
            [["candidate-a", "candidate-b", "candidate-c"]],
            ["candidate-d"],
            [],
        )
        metrics = self.evaluate([prediction])["metrics"]

        self.assertEqual(metrics["false_merge_pair_count"], 2)
        self.assertEqual(metrics["false_split_pair_count"], 0)
        self.assertAlmostEqual(metrics["identity_pair_precision"], 1 / 3)
        self.assertEqual(metrics["identity_pair_recall"], 1.0)
        self.assertEqual(metrics["candidate_role_accuracy"], 0.75)

    def test_false_split_is_counted_even_when_candidate_roles_match(self):
        prediction = self.make_decision(
            [["candidate-a"], ["candidate-b"]],
            ["candidate-d"],
            ["candidate-c"],
        )
        outputs = self.evaluate([prediction])
        metrics = outputs["metrics"]
        case_result = outputs["case_results"].iloc[0]

        self.assertEqual(metrics["candidate_role_accuracy"], 1.0)
        self.assertEqual(metrics["role_exact_case_rate"], 1.0)
        self.assertEqual(metrics["cluster_exact_case_rate"], 0.0)
        self.assertEqual(metrics["false_split_pair_count"], 1)
        self.assertEqual(metrics["problem_review_accuracy"], 0.0)
        self.assertEqual(case_result["predicted_requires_problem_review"], "YES")

    def test_missing_prediction_is_reported_without_metric_failure(self):
        outputs = self.evaluate([])
        metrics = outputs["metrics"]
        error_codes = set(outputs["evaluation_errors"]["error_code"])

        self.assertEqual(metrics["gold_case_count"], 1)
        self.assertEqual(metrics["evaluable_gold_case_count"], 1)
        self.assertEqual(metrics["valid_prediction_count"], 0)
        self.assertEqual(metrics["prediction_coverage"], 0.0)
        self.assertIn("MISSING_PREDICTED_DECISION", error_codes)

    def test_incomplete_gold_candidate_classification_is_not_evaluated(self):
        invalid_gold = self.make_gold_decision()
        invalid_gold["rejected_sources"] = []

        outputs = self.evaluate_term_decisions(
            [invalid_gold],
            [self.make_gold_decision()],
            [self.make_task()],
            self.make_outcomes(),
            self.policy,
        )
        metrics = outputs["metrics"]
        error_codes = set(outputs["evaluation_errors"]["error_code"])

        self.assertEqual(metrics["gold_case_count"], 1)
        self.assertEqual(metrics["evaluable_gold_case_count"], 0)
        self.assertEqual(metrics["prediction_coverage"], 0.0)
        self.assertIn("INVALID_GOLD_DECISION", error_codes)


if __name__ == "__main__":
    unittest.main()

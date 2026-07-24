import sys
import tempfile
import unittest
from pathlib import Path


class RoleConflictReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))

        from common import load_pipeline_policy
        from entity_resolution.role_conflict_review import (
            build_role_conflict_review_table,
            write_role_conflict_review_table,
        )

        cls.build_role_conflict_review_table = staticmethod(
            build_role_conflict_review_table
        )
        cls.write_role_conflict_review_table = staticmethod(
            write_role_conflict_review_table
        )
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def make_decision(
        self,
        evidence_candidate_id: str,
        rejected_candidate_id: str,
    ) -> dict:
        return {
            "term_review_task_id": "task-1",
            "resolution_case_id": "case-1",
            "decision_status": "PROPOSED",
            "review_model": "review-fixture",
            "prompt_version": "review-fixture-v1",
            "proposed_alternatives": [
                {
                    "display_name": "정답 실체",
                    "entity_type": "Concept",
                    "identity_member_source_candidate_ids": [
                        "candidate-a",
                        "candidate-b",
                    ],
                    "reason": "문제의 대상과 일치한다.",
                }
            ],
            "evidence_only_sources": [
                {
                    "source_candidate_id": evidence_candidate_id,
                    "reason": "대상과의 관계를 설명한다.",
                }
            ],
            "rejected_sources": [
                {
                    "source_candidate_id": rejected_candidate_id,
                    "reason": "문자열만 일치한다.",
                }
            ],
            "ambiguous_sources": [],
            "decision_reason": "역할 충돌 검토용 판정이다.",
        }

    def make_task(self) -> dict:
        candidate_ids = [
            "candidate-a",
            "candidate-b",
            "candidate-c",
            "candidate-d",
        ]
        return {
            "term_review_task_id": "task-1",
            "resolution_case_id": "case-1",
            "canonical_term": "검토 용어",
            "category": "개념",
            "problem_context_samples": [{"question": "검토 문맥"}],
            "source_candidates": [
                {
                    "source_candidate_id": candidate_id,
                    "source_record_id": f"record-{candidate_id}",
                    "source": "AKS",
                    "matched_name": candidate_id,
                    "matched_field": "name",
                    "retrieval_method": "normalized_exact",
                    "category_compatibility": "COMPATIBLE",
                    "source_entity_type_proposal": "Concept",
                    "source_context": {
                        "name": candidate_id,
                        "description": "후보 설명",
                    },
                }
                for candidate_id in candidate_ids
            ],
            "relevant_pair_signals": [],
            "gold_set_metadata": {
                "gold_case_order": 1,
                "gold_case_id": "gold-case-1",
            },
        }

    def test_only_evidence_rejected_swaps_are_written_and_reviews_survive(self):
        gold_decision = self.make_decision(
            "candidate-c",
            "candidate-d",
        )
        prediction = self.make_decision(
            "candidate-d",
            "candidate-c",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            review_path = str(Path(temp_dir) / "role_conflicts.csv")
            table = self.build_role_conflict_review_table(
                [gold_decision],
                [prediction],
                [self.make_task()],
                review_path,
                self.policy,
            )

            self.assertEqual(len(table), 2)
            self.assertEqual(
                set(zip(table["gold_role"], table["model_role"])),
                {
                    ("EVIDENCE_ONLY", "REJECTED"),
                    ("REJECTED", "EVIDENCE_ONLY"),
                },
            )
            self.assertEqual(set(table["review_status"]), {"PENDING"})

            reviewed_candidate_id = table.iloc[0]["source_candidate_id"]
            table.loc[0, "reviewed_role"] = table.iloc[0]["gold_role"]
            table.loc[0, "review_status"] = "COMPLETE"
            table.loc[0, "manual_reason"] = "원천 문맥을 재확인했다."
            table.loc[0, "reviewer"] = "tester"
            self.write_role_conflict_review_table(table, review_path)

            rebuilt = self.build_role_conflict_review_table(
                [gold_decision],
                [prediction],
                [self.make_task()],
                review_path,
                self.policy,
            )
            preserved = rebuilt.loc[
                rebuilt["source_candidate_id"] == reviewed_candidate_id
            ].iloc[0]

            self.assertEqual(preserved["review_status"], "COMPLETE")
            self.assertEqual(
                preserved["manual_reason"],
                "원천 문맥을 재확인했다.",
            )
            self.assertEqual(preserved["reviewer"], "tester")


if __name__ == "__main__":
    unittest.main()

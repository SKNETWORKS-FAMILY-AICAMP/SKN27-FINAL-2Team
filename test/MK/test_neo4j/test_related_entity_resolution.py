import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


class RelatedEntityResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))
        sys.path.insert(0, str(neo4j_root / "terms"))

        from common import load_pipeline_policy
        from entity_resolution.build_resolution_package import (
            build_resolution_tables,
            write_resolution_package,
        )
        from entity_resolution.related_entity_resolution import (
            build_related_entity_tasks,
            build_related_term_table,
            inject_related_entity_seed_candidates,
            select_seed_backed_alternatives,
        )
        from entity_resolution.semantic_review import (
            build_term_review_tasks,
            load_resolution_package,
            validate_term_decisions,
        )

        cls.build_related_entity_tasks = staticmethod(
            build_related_entity_tasks
        )
        cls.build_related_term_table = staticmethod(build_related_term_table)
        cls.inject_related_entity_seed_candidates = staticmethod(
            inject_related_entity_seed_candidates
        )
        cls.select_seed_backed_alternatives = staticmethod(
            select_seed_backed_alternatives
        )
        cls.build_resolution_tables = staticmethod(build_resolution_tables)
        cls.write_resolution_package = staticmethod(write_resolution_package)
        cls.build_term_review_tasks = staticmethod(build_term_review_tasks)
        cls.load_resolution_package = staticmethod(load_resolution_package)
        cls.validate_term_decisions = staticmethod(validate_term_decisions)
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def make_gold_task(self, task_number: int) -> dict:
        candidate_id = f"candidate-{task_number}"
        return {
            "term_review_task_id": f"term-task-{task_number}",
            "resolution_case_id": f"origin-case-{task_number}",
            "canonical_term": f"원래 용어 {task_number}",
            "problem_context_samples": [],
            "source_candidates": [
                {
                    "source_candidate_id": candidate_id,
                    "source_record_id": (
                        f"THESAURUS:TERM:{task_number}:release-1"
                    ),
                    "source": "THESAURUS",
                    "candidate_rank": 1,
                    "matched_name": "관련 인물",
                    "matched_field": "description",
                    "retrieval_method": "description_containment",
                    "retrieval_score": 0.47,
                    "category_compatibility": "UNKNOWN",
                    "normalized_names": ["관련인물"],
                    "hanja": ["關聯人物"],
                    "era_values": ["현대"],
                    "birth_year": "",
                    "death_year": "",
                    "bonkwan": [],
                    "source_entity_type_proposal": "Person",
                    "source_context": {
                        "term_name": "관련 인물",
                        "hanja": "關聯人物",
                        "era": "현대",
                        "thesaurus_category": "인명",
                        "description": "원래 용어와 관계가 있는 인물.",
                    },
                }
            ],
            "gold_set_metadata": {
                "gold_case_id": f"gold-case-{task_number}"
            },
        }

    def make_decision(self, task_number: int) -> dict:
        return {
            "term_review_task_id": f"term-task-{task_number}",
            "prompt_version": "entity-resolution-gold-annotation-v2",
            "proposed_related_entities": [
                {
                    "related_entity_key": "REL_001",
                    "display_name": "관련 인물",
                    "entity_type": "Person",
                    "evidence_source_candidate_ids": [
                        f"candidate-{task_number}"
                    ],
                    "reason": "관계 근거가 있는 별도 인물",
                }
            ],
        }

    def test_same_name_from_different_origins_is_not_automatically_merged(self):
        tasks = self.build_related_entity_tasks(
            [self.make_decision(1), self.make_decision(2)],
            [self.make_gold_task(1), self.make_gold_task(2)],
            self.policy,
        )

        self.assertEqual(len(tasks), 2)
        self.assertEqual({task["canonical_term"] for task in tasks}, {"관련 인물"})
        self.assertEqual(
            len({task["related_resolution_case_id"] for task in tasks}),
            2,
        )
        term_table = self.build_related_term_table(tasks)
        self.assertEqual(len(term_table), 2)
        self.assertEqual(term_table["category"].tolist(), ["", ""])

    def test_seed_source_and_origin_reach_semantic_review_task(self):
        related_tasks = self.build_related_entity_tasks(
            [self.make_decision(1)],
            [self.make_gold_task(1)],
            self.policy,
        )
        term_table = self.build_related_term_table(related_tasks)
        term_record = term_table.iloc[0].to_dict()
        match_results = [
            {
                **term_record,
                "problem_count": 0,
                "problem_ids": [],
                "is_noise": False,
                "encyclopedia": [],
                "thesaurus": [],
                "itkc_people": [],
                "itkc_events": [],
            }
        ]
        self.inject_related_entity_seed_candidates(
            match_results,
            related_tasks,
            self.policy,
        )

        seed = match_results[0]["thesaurus"][0]
        self.assertTrue(seed["human_related_entity_seed"])
        self.assertEqual(
            seed["retrieval_method"],
            "human_related_entity_seed",
        )
        resolution_tables = self.build_resolution_tables(
            match_results,
            [],
            pd.DataFrame(columns=["problem_id", "full_text"]),
            self.policy,
        )
        review_tasks = self.build_term_review_tasks(
            resolution_tables,
            self.policy,
        )

        self.assertEqual(len(review_tasks), 1)
        review_task = review_tasks[0]
        self.assertEqual(review_task["canonical_term"], "관련 인물")
        self.assertEqual(review_task["entity_type_proposal"], "Person")
        self.assertEqual(
            review_task["related_entity_origin"]["origin_canonical_term"],
            "원래 용어 1",
        )
        self.assertEqual(len(review_task["source_candidates"]), 1)
        source_candidate_id = review_task["source_candidates"][0][
            "source_candidate_id"
        ]
        decision = {
            "term_review_task_id": review_task["term_review_task_id"],
            "resolution_case_id": review_task["resolution_case_id"],
            "decision_status": "PROPOSED",
            "review_model": self.policy["entity_resolution"][
                "semantic_review"
            ]["term_model"]["model"],
            "prompt_version": self.policy["entity_resolution"][
                "semantic_review"
            ]["prompt_version"],
            "proposed_alternatives": [
                {
                    "display_name": "관련 인물",
                    "entity_type": "Person",
                    "identity_member_source_candidate_ids": [
                        source_candidate_id
                    ],
                    "reason": "사람이 지정한 seed 원천의 주 대상이다.",
                }
            ],
            "evidence_only_sources": [],
            "rejected_sources": [],
            "ambiguous_sources": [],
            "decision_reason": "관련 인물 한 명으로 판정했다.",
        }
        term_tables = self.validate_term_decisions(
            [decision],
            review_tasks,
            resolution_tables,
            self.policy,
        )
        selections = self.select_seed_backed_alternatives(
            resolution_tables,
            term_tables,
            self.policy,
        )

        self.assertEqual(selections.iloc[0]["selection_status"], "VERIFIED")
        self.assertTrue(selections.iloc[0]["canonical_alternative_id"])
        with tempfile.TemporaryDirectory() as temporary_directory:
            self.write_resolution_package(
                resolution_tables,
                temporary_directory,
                self.policy,
            )
            loaded_tables = self.load_resolution_package(
                temporary_directory,
                self.policy,
            )

        self.assertTrue(loaded_tables["problem_contexts"].empty)
        self.assertTrue(
            loaded_tables["problem_resolution_assignments"].empty
        )
        self.assertIn(
            "problem_id",
            loaded_tables["problem_contexts"].columns,
        )


if __name__ == "__main__":
    unittest.main()

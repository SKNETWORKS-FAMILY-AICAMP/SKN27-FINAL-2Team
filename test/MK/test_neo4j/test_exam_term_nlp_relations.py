import json
import sys
import unittest
from pathlib import Path

from kiwipiepy import Kiwi


class ExamTermNlpRelationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from choice_relation.exam_term_nlp_relations import (
            build_exam_term_nlp_relation_tables,
        )
        from choice_relation.exam_term_raw_relations import (
            load_exam_term_raw_relation_policy,
        )

        cls.build_tables = staticmethod(
            build_exam_term_nlp_relation_tables
        )
        cls.kiwi = Kiwi()
        cls.policy = load_exam_term_raw_relation_policy(
            str(
                neo4j_root
                / "config"
                / "exam_term_raw_relation_eda.json"
            ),
            str(
                neo4j_root
                / "config"
                / "exam_relation_candidates.json"
            ),
            str(
                neo4j_root / "config" / "entity_resolution.json"
            ),
            str(
                neo4j_root
                / "config"
                / "source_first_fact_eda.json"
            ),
        )
        with (
            neo4j_root
            / "config"
            / "exam_term_noun_phrase_eda.json"
        ).open("r", encoding="utf-8") as input_file:
            cls.noun_policy = json.load(input_file)
        with (
            neo4j_root
            / "config"
            / "exam_term_nlp_relations.json"
        ).open("r", encoding="utf-8") as input_file:
            cls.nlp_policy = json.load(input_file)

    def make_exam_groups(self) -> dict:
        return {
            "흥선대원군": {
                "surface_key": "흥선대원군",
                "display_surface": "흥선 대원군",
                "endpoint_count": 1,
                "endpoints": [
                    {
                        "endpoint_id": "CAN-HEUNGSEON",
                        "node_kind": "CANONICAL",
                        "canonical_id": "CAN-HEUNGSEON",
                        "source_record_id": "",
                        "display_name": "흥선 대원군",
                        "entity_type": "Person",
                        "source": "EXAM_TERM",
                        "source_url": "",
                        "is_exam_term": True,
                        "exam_term_id": "EXAM-HEUNGSEON",
                    }
                ],
            }
        }

    def make_document(self, sentence: str) -> dict:
        return {
            "source_dataset": "TEST",
            "source_document_id": "TEST:DOC:1",
            "source_title": "시험",
            "source_url": "https://example.test/doc",
            "source_path": "test.csv",
            "trust_tier": "OFFICIAL_NARRATIVE",
            "supports_linked_entities": False,
            "text_fields": {"body": sentence},
        }

    def test_registered_anchor_can_link_to_open_noun_phrase(self):
        tables, statistics = self.build_tables(
            [
                self.make_document(
                    "흥선 대원군은 별궁을 중건하였다."
                )
            ],
            self.make_exam_groups(),
            {},
            self.policy,
            self.noun_policy,
            self.nlp_policy,
            self.kiwi,
        )

        evidence = tables["evidence"]
        self.assertFalse(evidence.empty)
        correct = evidence[
            evidence["end_display_name"].eq("별궁")
        ]
        self.assertFalse(correct.empty)
        self.assertTrue(
            correct["start_node_id"].eq(
                "CAN-HEUNGSEON"
            ).all()
        )
        self.assertTrue(
            correct["end_node_kind"].eq(
                "OPEN_ENTITY_CANDIDATE"
            ).all()
        )
        self.assertEqual(
            statistics["both_endpoints_unregistered_count"],
            0,
        )
        self.assertEqual(
            statistics["nlp_relation_candidate_term_coverage"],
            1.0,
        )

    def test_registered_counterpart_receives_high_confidence(self):
        target_groups = {
            "경복궁": {
                "endpoint_count": 1,
                "endpoints": [
                    {
                        "endpoint_id": "SRC-GYEONGBOK",
                        "node_kind": "OFFICIAL_SOURCE",
                        "canonical_id": "",
                        "source_record_id": "SRC-GYEONGBOK",
                        "display_name": "경복궁",
                        "entity_type": "Heritage",
                        "source": "THESAURUS",
                        "source_url": "",
                        "is_exam_term": False,
                        "exam_term_id": "",
                    }
                ],
            }
        }
        tables, statistics = self.build_tables(
            [
                self.make_document(
                    "흥선 대원군은 경복궁을 중건하였다."
                )
            ],
            self.make_exam_groups(),
            target_groups,
            self.policy,
            self.noun_policy,
            self.nlp_policy,
            self.kiwi,
        )

        evidence = tables["evidence"]
        correct = evidence[
            evidence["end_node_id"].eq("SRC-GYEONGBOK")
        ]
        self.assertFalse(correct.empty)
        self.assertTrue(
            correct["candidate_status"].eq(
                "NLP_HIGH_CONFIDENCE_REGISTERED"
            ).any()
        )
        self.assertEqual(
            statistics["high_confidence_nlp_term_coverage"],
            1.0,
        )

    def test_relation_outputs_are_never_auto_loaded(self):
        tables, statistics = self.build_tables(
            [
                self.make_document(
                    "흥선 대원군은 별궁을 중건하였다."
                )
            ],
            self.make_exam_groups(),
            {},
            self.policy,
            self.noun_policy,
            self.nlp_policy,
            self.kiwi,
        )

        self.assertFalse(
            tables["evidence"]["auto_load_eligible"].eq(
                True
            ).any()
        )
        self.assertFalse(
            tables["relations"]["neo4j_load"].eq(True).any()
        )
        self.assertEqual(
            statistics["auto_load_eligible_count"],
            0,
        )


if __name__ == "__main__":
    unittest.main()

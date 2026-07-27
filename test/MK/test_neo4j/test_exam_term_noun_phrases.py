import json
import sys
import unittest
from pathlib import Path

from kiwipiepy import Kiwi


class ExamTermNounPhraseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from choice_relation.exam_term_noun_phrases import (
            build_noun_phrase_eda_tables,
            extract_noun_phrase_mentions,
        )
        from choice_relation.exam_term_raw_relations import (
            load_exam_term_raw_relation_policy,
        )

        cls.extract_mentions = staticmethod(
            extract_noun_phrase_mentions
        )
        cls.build_tables = staticmethod(
            build_noun_phrase_eda_tables
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

    def test_extracts_compound_and_multiword_noun_phrases(self):
        mentions = self.extract_mentions(
            "흥선 대원군은 농민항쟁이 단성에서 발생하였다.",
            self.kiwi,
            self.noun_policy,
        )
        surfaces = {
            str(mention["surface"]) for mention in mentions
        }

        self.assertIn("흥선 대원군", surfaces)
        self.assertIn("농민항쟁", surfaces)
        self.assertIn("단성", surfaces)

    def test_collects_all_nouns_without_creating_relations(self):
        exam_groups = {
            "흥선대원군": {
                "endpoint_count": 1,
                "endpoints": [
                    {
                        "endpoint_id": "CAN-HEUNGSEON",
                        "node_kind": "CANONICAL",
                    }
                ],
            }
        }
        target_groups = {
            "경복궁": {
                "endpoint_count": 1,
                "endpoints": [
                    {
                        "endpoint_id": "SRC-GYEONGBOK",
                        "node_kind": "OFFICIAL_SOURCE",
                    }
                ],
            }
        }
        documents = [
            {
                "source_dataset": "TEST",
                "source_document_id": "TEST:DOC:1",
                "source_title": "시험",
                "source_url": "https://example.test/doc",
                "source_path": "test.csv",
                "trust_tier": "OFFICIAL_NARRATIVE",
                "supports_linked_entities": False,
                "text_fields": {
                    "body": (
                        "흥선 대원군은 경복궁을 중건하였다."
                    )
                },
            }
        ]

        tables, statistics = self.build_tables(
            documents,
            exam_groups,
            target_groups,
            self.policy,
            self.noun_policy,
            self.kiwi,
        )

        mentions = tables["mentions"]
        status_by_surface = {
            str(row["normalized_surface"]): str(
                row["registration_status"]
            )
            for row in mentions.to_dict("records")
        }
        self.assertEqual(
            status_by_surface["흥선대원군"],
            "REGISTERED_EXAM_TERM",
        )
        self.assertEqual(
            status_by_surface["경복궁"],
            "REGISTERED_OFFICIAL_OR_CANONICAL",
        )
        self.assertEqual(
            status_by_surface["중건"],
            "UNREGISTERED_NOUN_PHRASE",
        )
        self.assertEqual(statistics["relation_candidate_count"], 0)
        self.assertEqual(
            statistics["node_creation_eligible_count"],
            0,
        )
        self.assertFalse(
            mentions["node_creation_eligible"].eq(True).any()
        )


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path

import pandas as pd


class DescriptionFactPreprocessingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from source_relationships.build import (
            load_source_relationship_policy,
        )
        from source_relationships.description_facts import (
            build_description_fact_tables,
        )

        cls.build_tables = staticmethod(build_description_fact_tables)
        cls.policy = load_source_relationship_policy(
            str(neo4j_root / "config" / "source_relationships.json")
        )

    def build_inputs(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        registry_rows = [
            {
                "canonical_id": "C-WORK",
                "display_name": "훈민정음",
                "entity_type": "Work",
                "identity_member_source_ids_json": json.dumps(
                    ["AKS:WORK"]
                ),
            },
            {
                "canonical_id": "C-SEJONG",
                "display_name": "세종",
                "entity_type": "Person",
                "identity_member_source_ids_json": json.dumps(
                    ["ITKC:SEJONG"]
                ),
            },
            {
                "canonical_id": "C-YIYI",
                "display_name": "이이",
                "entity_type": "Person",
                "identity_member_source_ids_json": json.dumps(
                    ["AKS:YIYI"]
                ),
            },
            {
                "canonical_id": "C-YIH",
                "display_name": "이황",
                "entity_type": "Person",
                "identity_member_source_ids_json": json.dumps(
                    ["ITKC:YIH"]
                ),
            },
            {
                "canonical_id": "C-DOSAN",
                "display_name": "도산서원",
                "entity_type": "Heritage",
                "identity_member_source_ids_json": json.dumps(
                    ["AKS:DOSAN"]
                ),
            },
            {
                "canonical_id": "C-CHEONGUN",
                "display_name": "천군",
                "entity_type": "Concept",
                "identity_member_source_ids_json": "[]",
            },
        ]
        source_rows = [
            {
                "source_record_id": "ITKC:SEJONG",
                "source": "ITKC_PERSON",
                "source_release": "release",
                "source_metadata_json": json.dumps(
                    {"name": "세종"},
                    ensure_ascii=False,
                ),
            },
            {
                "source_record_id": "ITKC:YIH",
                "source": "ITKC_PERSON",
                "source_release": "release",
                "source_metadata_json": json.dumps(
                    {"name": "이황", "origin": "李滉"},
                    ensure_ascii=False,
                ),
            },
            {
                "source_record_id": "AKS:WORK",
                "source": "AKS",
                "source_release": "release",
                "source_metadata_json": json.dumps(
                    {
                        "definition": "세종이 창제한 문자.",
                        "source_url": "https://example.test/work",
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "source_record_id": "AKS:YIYI",
                "source": "AKS",
                "source_release": "release",
                "source_metadata_json": json.dumps(
                    {
                        "definition": "조선시대 이황의 제자.",
                        "source_url": "https://example.test/yiyi",
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "source_record_id": "AKS:DOSAN",
                "source": "AKS",
                "source_release": "release",
                "source_metadata_json": json.dumps(
                    {
                        "definition": (
                            "금천군에 있었으며 이황(李滉)을 배향한 서원."
                        ),
                        "source_url": "https://example.test/dosan",
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        return pd.DataFrame(registry_rows), pd.DataFrame(source_rows)

    def test_explicit_official_sentences_create_direct_facts(self):
        registry, sources = self.build_inputs()

        tables = self.build_tables(
            registry,
            sources,
            self.policy,
        )
        relationships = tables["description_canonical_relationships"]

        self.assertEqual(
            set(relationships["relation_type"]),
            {"CREATED_BY", "HAS_TEACHER", "DEDICATED_TO"},
        )
        self.assertEqual(len(relationships), 3)
        self.assertTrue(
            relationships["evidence_sentences_json"].str.len().gt(2).all()
        )

    def test_word_internal_name_is_not_a_mention(self):
        registry, sources = self.build_inputs()

        tables = self.build_tables(
            registry,
            sources,
            self.policy,
        )
        mentions = tables["description_mention_candidates"]

        self.assertNotIn(
            "C-CHEONGUN",
            set(mentions["object_canonical_id"]),
        )


if __name__ == "__main__":
    unittest.main()

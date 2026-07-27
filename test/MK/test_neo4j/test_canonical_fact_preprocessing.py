import sys
import unittest
from pathlib import Path

import pandas as pd


class CanonicalFactPreprocessingTest(unittest.TestCase):
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
        from source_relationships.canonical_facts import (
            build_canonical_fact_relationships,
        )

        cls.build_facts = staticmethod(
            build_canonical_fact_relationships
        )
        cls.policy = load_source_relationship_policy(
            str(neo4j_root / "config" / "source_relationships.json")
        )

    def test_duplicate_fact_merges_evidence(self):
        registry = pd.DataFrame(
            [
                {"canonical_id": "C-A"},
                {"canonical_id": "C-B"},
                {"canonical_id": "C-C"},
            ]
        )
        structured = pd.DataFrame(
            [
                {
                    "start_canonical_id": "C-A",
                    "end_canonical_id": "C-B",
                    "relation_type": "HAS_TEACHER",
                    "source_relationship_ids_json": '["SR-1"]',
                    "source_datasets_json": '["ITKC"]',
                    "evidence_count": "1",
                    "source_row_count": "1",
                    "verification_status": "SOURCE_ASSERTED",
                }
            ]
        )
        description = pd.DataFrame(
            [
                {
                    "start_canonical_id": "C-A",
                    "end_canonical_id": "C-B",
                    "relation_type": "HAS_TEACHER",
                    "description_mention_ids_json": '["DM-1"]',
                    "evidence_sentences_json": '["공식 설명."]',
                    "evidence_count": "1",
                    "source_row_count": "1",
                    "verification_status": "PATTERN_ASSERTED",
                },
                {
                    "start_canonical_id": "C-A",
                    "end_canonical_id": "C-C",
                    "relation_type": "AUTHORED",
                    "description_mention_ids_json": '["DM-2"]',
                    "evidence_count": "1",
                    "source_row_count": "1",
                    "verification_status": "PATTERN_ASSERTED",
                },
            ]
        )

        facts = self.build_facts(
            structured,
            description,
            registry,
            self.policy,
        )

        self.assertEqual(len(facts), 2)
        merged = facts[facts["relation_type"].eq("HAS_TEACHER")].iloc[0]
        self.assertEqual(
            merged["verification_status"],
            "SOURCE_ASSERTED",
        )
        self.assertEqual(merged["evidence_count"], "2")
        self.assertIn("STRUCTURED_SOURCE", merged["extraction_methods_json"])
        self.assertIn(
            "OFFICIAL_DESCRIPTION_PATTERN",
            merged["extraction_methods_json"],
        )


if __name__ == "__main__":
    unittest.main()

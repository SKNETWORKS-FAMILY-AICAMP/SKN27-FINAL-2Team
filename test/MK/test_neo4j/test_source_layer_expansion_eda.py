import json
import sys
import unittest
from pathlib import Path

import pandas as pd


class SourceLayerExpansionEdaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from choice_relation.source_layer_expansion_eda import (
            build_source_layer_expansion_tables,
            load_source_layer_expansion_policy,
        )

        cls.build_tables = staticmethod(
            build_source_layer_expansion_tables
        )
        cls.policy = load_source_layer_expansion_policy(
            str(
                neo4j_root
                / "config"
                / "source_layer_expansion_eda.json"
            ),
            str(
                neo4j_root / "config" / "fact_retrieval.json"
            ),
            str(
                neo4j_root / "config" / "entity_resolution.json"
            ),
        )

    def make_registry(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-A",
                    "display_name": "인물가",
                    "entity_type": "Person",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "CAN-B",
                    "display_name": "문헌나",
                    "entity_type": "Work",
                    "lifecycle_status": "ACTIVE",
                },
            ]
        )

    def make_anchors(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "anchor_id": "A",
                    "anchor_kind": "CANONICAL",
                    "canonical_id": "CAN-A",
                    "source_record_id": "",
                    "display_name": "인물가",
                    "normalized_name": "인물가",
                    "entity_type": "Person",
                    "resolution_status": "RESOLVED",
                    "source": "",
                    "source_urls_json": "[]",
                    "topic_ids_json": '["topic:person"]',
                    "era_ids_json": '["era:joseon"]',
                },
                {
                    "anchor_id": "B",
                    "anchor_kind": "CANONICAL",
                    "canonical_id": "CAN-B",
                    "source_record_id": "",
                    "display_name": "문헌나",
                    "normalized_name": "문헌나",
                    "entity_type": "Work",
                    "resolution_status": "RESOLVED",
                    "source": "",
                    "source_urls_json": "[]",
                    "topic_ids_json": '["topic:culture"]',
                    "era_ids_json": '["era:joseon"]',
                },
                {
                    "anchor_id": "S",
                    "anchor_kind": "OFFICIAL_SOURCE",
                    "canonical_id": "",
                    "source_record_id": "ITKC:PERSON:P1:release",
                    "display_name": "인물다(人物多)",
                    "normalized_name": "인물다(人物多)",
                    "entity_type": "Person",
                    "resolution_status": (
                        "UNRESOLVED_OFFICIAL_SOURCE"
                    ),
                    "source": "ITKC_PERSON",
                    "source_urls_json": '["https://example.test/p1"]',
                    "topic_ids_json": "[]",
                    "era_ids_json": "[]",
                },
            ]
        )

    def make_anchor_facts(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "start_anchor_id": "A",
                    "end_anchor_id": "B",
                    "relation_type": "AUTHORED",
                    "search_status": "PRIMARY",
                },
                {
                    "start_anchor_id": "S",
                    "end_anchor_id": "A",
                    "relation_type": "AUTHORED",
                    "search_status": "FALLBACK",
                },
            ]
        )

    def test_unresolved_source_is_only_retrieval_candidate(self):
        canonical_facts = pd.DataFrame(
            [
                {
                    "canonical_relationship_id": "CF-1",
                    "start_canonical_id": "CAN-A",
                    "end_canonical_id": "CAN-B",
                    "relation_type": "AUTHORED",
                }
            ]
        )
        source_nodes = pd.DataFrame(
            [
                {
                    "source_record_id": "ITKC:PERSON:P1:release",
                    "source_metadata_json": json.dumps(
                        {
                            "birth_year": "1800",
                            "name": "인물다(人物多)",
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
        )
        exam_links = pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-A",
                    "match_status": "ACCEPTED",
                },
                {
                    "canonical_id": "CAN-B",
                    "match_status": "ACCEPTED",
                },
            ]
        )

        tables, statistics = self.build_tables(
            self.make_registry(),
            canonical_facts,
            self.make_anchors(),
            self.make_anchor_facts(),
            source_nodes,
            exam_links,
            pd.DataFrame(
                columns=["correct_canonical_relationship_id"]
            ),
            self.policy,
        )

        self.assertEqual(len(tables["candidates"]), 1)
        candidate = tables["candidates"].iloc[0]
        self.assertEqual(
            candidate["retrieval_safety_status"],
            "RETRIEVAL_CANDIDATE",
        )
        self.assertTrue(candidate["requires_truth_verification"])
        self.assertFalse(candidate["neo4j_load"])
        self.assertEqual(
            statistics["auto_promoted_canonical_fact_count"],
            0,
        )

    def test_known_true_source_relation_is_blocked(self):
        canonical_facts = pd.DataFrame(
            [
                {
                    "canonical_relationship_id": "CF-1",
                    "start_canonical_id": "CAN-A",
                    "end_canonical_id": "CAN-B",
                    "relation_type": "AUTHORED",
                }
            ]
        )
        anchor_facts = pd.concat(
            [
                self.make_anchor_facts(),
                pd.DataFrame(
                    [
                        {
                            "start_anchor_id": "S",
                            "end_anchor_id": "B",
                            "relation_type": "AUTHORED",
                            "search_status": "FALLBACK",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        source_nodes = pd.DataFrame(
            [
                {
                    "source_record_id": "ITKC:PERSON:P1:release",
                    "source_metadata_json": "{}",
                }
            ]
        )
        exam_links = pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-A",
                    "match_status": "ACCEPTED",
                },
                {
                    "canonical_id": "CAN-B",
                    "match_status": "ACCEPTED",
                },
            ]
        )

        tables, _ = self.build_tables(
            self.make_registry(),
            canonical_facts,
            self.make_anchors(),
            anchor_facts,
            source_nodes,
            exam_links,
            pd.DataFrame(
                columns=["correct_canonical_relationship_id"]
            ),
            self.policy,
        )

        statuses = set(
            tables["candidates"]["retrieval_safety_status"]
        )
        self.assertIn("BLOCKED_KNOWN_TRUE", statuses)


if __name__ == "__main__":
    unittest.main()

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


class SourceFirstFactEdaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from choice_relation.source_first_fact_eda import (
            build_source_first_fact_eda_tables,
            load_source_first_fact_policy,
        )

        cls.build_tables = staticmethod(
            build_source_first_fact_eda_tables
        )
        cls.policy = load_source_first_fact_policy(
            str(
                neo4j_root
                / "config"
                / "source_first_fact_eda.json"
            ),
            str(
                neo4j_root
                / "config"
                / "exam_relation_candidates.json"
            ),
        )

    def make_registry(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-CHOE",
                    "entity_type": "Person",
                    "display_name": "최제우",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000001:release", '
                        '"THESAURUS:TERM:100:release"]'
                    ),
                },
                {
                    "canonical_id": "CAN-DONGHAK",
                    "entity_type": "Concept",
                    "display_name": "동학",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000002:release", '
                        '"THESAURUS:TERM:200:release"]'
                    ),
                },
            ]
        )

    def make_exam_matches(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "projected_source_link_status": "ACCEPTED",
                    "projected_canonical_ids_json": '["CAN-CHOE"]',
                },
                {
                    "projected_source_link_status": "ACCEPTED",
                    "projected_canonical_ids_json": (
                        '["CAN-DONGHAK"]'
                    ),
                },
            ]
        )

    def make_existing_facts(self) -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "start_canonical_id",
                "end_canonical_id",
                "relation_type",
                "source_datasets_json",
            ]
        )

    def write_sources(
        self,
        directory: str,
    ) -> tuple[str, str]:
        aks_path = Path(directory) / "aks.jsonl"
        articles = [
            {
                "eid": "E0000002",
                "url": "https://example.test/donghak",
                "headword": "동학",
                "definition": (
                    "[최제우](E0000001)가 "
                    "[동학](E0000002)을 창시하였다. "
                    "[최제우](E0000001)에 의해 창시된 종교."
                ),
                "summary": "",
                "body": "",
            }
        ]
        aks_path.write_text(
            "\n".join(
                json.dumps(article, ensure_ascii=False)
                for article in articles
            )
            + "\n",
            encoding="utf-8",
        )
        thesaurus_path = Path(directory) / "thesaurus.json"
        thesaurus_path.write_text(
            json.dumps(
                [
                    {
                        "problem_id": "thesaurus_200",
                        "terms": [
                            {
                                "raw_term": "동학",
                                "context": (
                                    "최제우(崔濟愚)가 창시한 종교."
                                ),
                            }
                        ],
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return str(aks_path), str(thesaurus_path)

    def test_source_first_rules_split_auto_and_review_tiers(self):
        with tempfile.TemporaryDirectory() as directory:
            aks_path, thesaurus_path = self.write_sources(directory)
            tables, statistics = self.build_tables(
                self.make_registry(),
                self.make_existing_facts(),
                self.make_exam_matches(),
                aks_path,
                thesaurus_path,
                self.policy,
            )

        evidence = tables["evidence"]
        facts = tables["facts"]

        self.assertEqual(len(evidence), 3)
        self.assertEqual(len(facts), 1)
        self.assertEqual(
            set(evidence["discovery_rule"]),
            {
                "AKS_TWO_LINKED_ROLE_PAIR",
                "AKS_SUBJECT_AND_LINKED_ROLE",
                "THESAURUS_SUBJECT_AND_UNIQUE_NAME_ROLE",
            },
        )
        self.assertTrue(facts.iloc[0]["auto_accept_eligible"])
        self.assertTrue(facts.iloc[0]["both_exam_anchors"])
        self.assertEqual(
            statistics["novel_auto_accept_candidate_fact_count"],
            1,
        )

    def test_negated_aks_sentence_is_excluded(self):
        article = {
            "eid": "E0000002",
            "url": "https://example.test/donghak",
            "headword": "동학",
            "definition": (
                "[최제우](E0000001)가 "
                "[동학](E0000002)을 창시하지 못하였다."
            ),
            "summary": "",
            "body": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            aks_path = Path(directory) / "aks.jsonl"
            aks_path.write_text(
                json.dumps(article, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            thesaurus_path = Path(directory) / "thesaurus.json"
            thesaurus_path.write_text("[]", encoding="utf-8")
            tables, _ = self.build_tables(
                self.make_registry(),
                self.make_existing_facts(),
                self.make_exam_matches(),
                str(aks_path),
                str(thesaurus_path),
                self.policy,
            )

        self.assertTrue(tables["evidence"].empty)
        self.assertTrue(tables["facts"].empty)


if __name__ == "__main__":
    unittest.main()

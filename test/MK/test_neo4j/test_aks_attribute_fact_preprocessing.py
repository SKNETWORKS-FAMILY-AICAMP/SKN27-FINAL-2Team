import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd


class AksAttributeFactPreprocessingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from source_relationships.aks_attributes import (
            build_aks_attribute_tables,
        )
        from source_relationships.build import (
            load_source_relationship_policy,
        )

        cls.build_tables = staticmethod(build_aks_attribute_tables)
        cls.policy = load_source_relationship_policy(
            str(neo4j_root / "config" / "source_relationships.json")
        )

    def build_inputs(
        self,
    ) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
        registry = pd.DataFrame(
            [
                {
                    "canonical_id": "C-PERSON",
                    "display_name": "이황",
                    "entity_type": "Person",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "C-PLACE",
                    "display_name": "안동",
                    "entity_type": "Place",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "C-WORK",
                    "display_name": "성학십도",
                    "entity_type": "Work",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "C-EVENT",
                    "display_name": "을사사화",
                    "entity_type": "Event",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "C-ORG",
                    "display_name": "독립 협회",
                    "entity_type": "Organization",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "C-KIMGU-1",
                    "display_name": "김구",
                    "entity_type": "Person",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "C-KIMGU-2",
                    "display_name": "김구",
                    "entity_type": "Person",
                    "lifecycle_status": "ACTIVE",
                },
            ]
        )
        resolutions = pd.DataFrame(
            [
                {
                    "source_record_id": (
                        "AKS:ARTICLE:E-PERSON:release-1"
                    ),
                    "canonical_id": "C-PERSON",
                    "match_status": "ACCEPTED",
                },
                {
                    "source_record_id": (
                        "AKS:ARTICLE:E-WORK:release-1"
                    ),
                    "canonical_id": "C-WORK",
                    "match_status": "ACCEPTED",
                },
                {
                    "source_record_id": (
                        "AKS:ARTICLE:E-ORG:release-1"
                    ),
                    "canonical_id": "C-ORG",
                    "match_status": "ACCEPTED",
                },
            ]
        )
        articles = [
            {
                "eid": "E-PERSON",
                "headword": "이황",
                "url": "https://example.test/person",
                "articleAttributes": [
                    {
                        "attrName": "출생지",
                        "attrValue": "안동(安東)",
                    },
                    {
                        "attrName": "주요 저서",
                        "attrValue": "성학십도",
                    },
                    {
                        "attrName": "관련 사건",
                        "attrValue": "을사사화",
                    },
                ],
                "relatedArticles": [
                    {
                        "targetEID": "E-WORK",
                        "targetUrl": "https://example.test/work",
                        "headword": "성학십도",
                    }
                ],
            },
            {
                "eid": "E-WORK",
                "headword": "성학십도",
                "url": "https://example.test/work",
                "articleAttributes": [
                    {
                        "attrName": "저자",
                        "attrValue": "이황(李滉)",
                    }
                ],
                "relatedArticles": [],
            },
            {
                "eid": "E-ORG",
                "headword": "독립 협회",
                "url": "https://example.test/org",
                "articleAttributes": [
                    {
                        "attrName": "설립자",
                        "attrValue": "김구",
                    }
                ],
                "relatedArticles": [],
            },
        ]
        return registry, resolutions, articles

    def test_fact_and_discovery_relationships_are_separated(self):
        registry, resolutions, articles = self.build_inputs()
        with TemporaryDirectory() as temporary_directory:
            articles_path = (
                Path(temporary_directory) / "articles.jsonl"
            )
            articles_path.write_text(
                "\n".join(
                    json.dumps(article, ensure_ascii=False)
                    for article in articles
                ),
                encoding="utf-8",
            )
            tables, statistics = self.build_tables(
                articles_path,
                registry,
                resolutions,
                self.policy,
            )

        relationships = tables["aks_attribute_relationships"]
        facts = relationships[
            relationships["candidate_status"].eq(
                "READY_TO_PROJECT"
            )
        ]
        discovery = relationships[
            relationships["candidate_status"].eq("DISCOVERY_ONLY")
        ]

        self.assertEqual(
            set(facts["relation_type"]),
            {"BORN_IN", "AUTHORED", "CREATED_BY"},
        )
        self.assertEqual(
            set(discovery["relation_type"]),
            {"ASSOCIATED_WITH_EVENT"},
        )
        self.assertEqual(statistics["fact_candidate_count"], 3)
        self.assertEqual(
            statistics["attribute_discovery_count"],
            1,
        )
        self.assertEqual(
            len(tables["aks_related_article_candidates"]),
            1,
        )

    def test_ambiguous_target_is_excluded(self):
        registry, resolutions, articles = self.build_inputs()
        with TemporaryDirectory() as temporary_directory:
            articles_path = (
                Path(temporary_directory) / "articles.jsonl"
            )
            articles_path.write_text(
                "\n".join(
                    json.dumps(article, ensure_ascii=False)
                    for article in articles
                ),
                encoding="utf-8",
            )
            tables, statistics = self.build_tables(
                articles_path,
                registry,
                resolutions,
                self.policy,
            )

        exclusions = tables["aks_attribute_exclusions"]
        ambiguous = exclusions[
            exclusions["exclusion_reason"].eq("TARGET_AMBIGUOUS")
        ]

        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(ambiguous.iloc[0]["attribute_value"], "김구")
        self.assertEqual(
            statistics["exclusion_reason_counts"][
                "TARGET_AMBIGUOUS"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()

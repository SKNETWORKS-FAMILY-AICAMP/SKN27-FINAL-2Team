import sys
import unittest
from pathlib import Path

import pandas as pd


class ExamRelationOfficialCorroborationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from choice_relation.official_corroboration import (
            build_exam_relation_official_corroboration_tables,
            load_exam_relation_official_policy,
        )

        cls.build_tables = staticmethod(
            build_exam_relation_official_corroboration_tables
        )
        cls.policy = load_exam_relation_official_policy(
            str(
                neo4j_root
                / "config"
                / "exam_relation_candidates.json"
            )
        )

    def make_registry(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-GEOCHILBU",
                    "entity_type": "Person",
                    "display_name": "거칠부",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "CAN-GUKSA",
                    "entity_type": "Work",
                    "display_name": "국사",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "CAN-WANGGEON-1",
                    "entity_type": "Person",
                    "display_name": "왕건",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "CAN-WANGGEON-2",
                    "entity_type": "Person",
                    "display_name": "왕건",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "CAN-HUNYO",
                    "entity_type": "Work",
                    "display_name": "훈요십조",
                    "lifecycle_status": "ACTIVE",
                },
                {
                    "canonical_id": "CAN-COMRADE",
                    "entity_type": "Concept",
                    "display_name": "동지",
                    "lifecycle_status": "ACTIVE",
                },
            ]
        )

    def make_facts(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "canonical_relationship_id": "FACT-GUKSA",
                    "start_canonical_id": "CAN-GEOCHILBU",
                    "end_canonical_id": "CAN-GUKSA",
                    "relation_type": "AUTHORED",
                    "verification_status": "PATTERN_ASSERTED",
                    "source_datasets_json": '["AKS_DESCRIPTION"]',
                    "evidence_urls_json": '["https://example.test/guksa"]',
                    "evidence_sentences_json": (
                        '["거칠부가 편찬한 신라의 역사서."]'
                    ),
                },
                {
                    "canonical_relationship_id": "FACT-HUNYO",
                    "start_canonical_id": "CAN-WANGGEON-1",
                    "end_canonical_id": "CAN-HUNYO",
                    "relation_type": "AUTHORED",
                    "verification_status": "SOURCE_ASSERTED",
                    "source_datasets_json": '["AKS_STRUCTURED_ATTRIBUTE"]',
                    "evidence_urls_json": '["https://example.test/hunyo"]',
                    "evidence_sentences_json": '["왕건이 남긴 훈요십조."]',
                },
            ]
        )

    def make_candidate(
        self,
        candidate_id: str,
        status: str,
        start_id: str,
        end_id: str,
        predicates: str,
        evidence_text: str,
    ) -> dict:
        return {
            "exam_relation_candidate_id": candidate_id,
            "claim_segment_id": f"CLAIM-{candidate_id}",
            "problem_id": f"PROBLEM-{candidate_id}",
            "candidate_status": status,
            "start_canonical_id": start_id,
            "end_canonical_id": end_id,
            "predicate_families_json": predicates,
            "evidence_text": evidence_text,
        }

    def test_existing_pair_and_predicate_link_to_official_fact(self):
        candidates = pd.DataFrame(
            [
                self.make_candidate(
                    "PAIR",
                    "NEEDS_OFFICIAL_CORROBORATION",
                    "CAN-GEOCHILBU",
                    "CAN-GUKSA",
                    '["AUTHOR_OR_COMPILE"]',
                    "거칠부가 국사를 편찬하였다.",
                )
            ]
        )
        tables, statistics = self.build_tables(
            candidates,
            self.make_registry(),
            self.make_facts(),
            self.policy,
        )
        check = tables["official_checks"].iloc[0]

        self.assertEqual(
            check["verification_status"],
            "VERIFIED_EXISTING_FACT",
        )
        self.assertEqual(
            check["endpoint_resolution_method"],
            "EXISTING_PAIR",
        )
        self.assertTrue(check["can_link_to_existing_fact"])
        self.assertFalse(check["may_create_new_fact"])
        self.assertEqual(statistics["verified_link_count"], 1)

    def test_one_known_endpoint_is_recovered_by_unique_name(self):
        candidates = pd.DataFrame(
            [
                self.make_candidate(
                    "FRAGMENT",
                    "TARGET_RESOLUTION_REQUIRED",
                    "CAN-GUKSA",
                    "",
                    '["AUTHOR_OR_COMPILE"]',
                    "거칠부가 국사를 편찬하였다.",
                )
            ]
        )
        tables, _ = self.build_tables(
            candidates,
            self.make_registry(),
            self.make_facts(),
            self.policy,
        )
        check = tables["official_checks"].iloc[0]

        self.assertEqual(
            check["verification_status"],
            "VERIFIED_EXISTING_FACT",
        )
        self.assertEqual(
            check["endpoint_resolution_method"],
            "RECOVERED_ONE_ENDPOINT",
        )
        self.assertEqual(
            check["resolved_start_canonical_id"],
            "CAN-GEOCHILBU",
        )
        self.assertEqual(
            check["resolved_end_canonical_id"],
            "CAN-GUKSA",
        )

    def test_homonym_is_recovered_only_by_unique_official_fact(self):
        candidates = pd.DataFrame(
            [
                self.make_candidate(
                    "HOMONYM",
                    "TARGET_RESOLUTION_REQUIRED",
                    "CAN-HUNYO",
                    "",
                    '["AUTHOR_OR_COMPILE"]',
                    "왕건이 훈요십조를 지었다.",
                )
            ]
        )
        tables, statistics = self.build_tables(
            candidates,
            self.make_registry(),
            self.make_facts(),
            self.policy,
        )
        check = tables["official_checks"].iloc[0]

        self.assertEqual(
            check["verification_status"],
            "VERIFIED_EXISTING_FACT",
        )
        self.assertEqual(
            check["endpoint_resolution_method"],
            "OFFICIAL_FACT_NEIGHBOR",
        )
        self.assertTrue(check["can_link_to_existing_fact"])
        self.assertEqual(statistics["new_fact_creation_count"], 0)

    def test_ambiguous_official_fact_neighbors_are_not_recovered(self):
        candidates = pd.DataFrame(
            [
                self.make_candidate(
                    "AMBIGUOUS-NEIGHBOR",
                    "TARGET_RESOLUTION_REQUIRED",
                    "CAN-HUNYO",
                    "",
                    '["AUTHOR_OR_COMPILE"]',
                    "왕건이 훈요십조를 지었다.",
                )
            ]
        )
        facts = pd.concat(
            [
                self.make_facts(),
                pd.DataFrame(
                    [
                        {
                            "canonical_relationship_id": "FACT-HUNYO-2",
                            "start_canonical_id": "CAN-WANGGEON-2",
                            "end_canonical_id": "CAN-HUNYO",
                            "relation_type": "AUTHORED",
                            "verification_status": "SOURCE_ASSERTED",
                            "source_datasets_json": (
                                '["AKS_STRUCTURED_ATTRIBUTE"]'
                            ),
                            "evidence_urls_json": (
                                '["https://example.test/hunyo-2"]'
                            ),
                            "evidence_sentences_json": (
                                '["왕건이 남긴 훈요십조."]'
                            ),
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        tables, _ = self.build_tables(
            candidates,
            self.make_registry(),
            facts,
            self.policy,
        )
        check = tables["official_checks"].iloc[0]

        self.assertEqual(
            check["verification_status"],
            "ENDPOINTS_UNRESOLVED",
        )
        self.assertFalse(check["can_link_to_existing_fact"])

    def test_missing_predicate_does_not_verify_existing_pair(self):
        candidates = pd.DataFrame(
            [
                self.make_candidate(
                    "NO-PREDICATE",
                    "NEEDS_OFFICIAL_CORROBORATION",
                    "CAN-GEOCHILBU",
                    "CAN-GUKSA",
                    "[]",
                    "거칠부와 국사에 관한 설명이다.",
                )
            ]
        )
        tables, _ = self.build_tables(
            candidates,
            self.make_registry(),
            self.make_facts(),
            self.policy,
        )
        check = tables["official_checks"].iloc[0]

        self.assertEqual(
            check["verification_status"],
            "PREDICATE_UNRESOLVED",
        )
        self.assertTrue(tables["verified_links"].empty)

    def test_name_inside_longer_word_is_not_recovered(self):
        candidates = pd.DataFrame(
            [
                self.make_candidate(
                    "WORD-INTERNAL",
                    "TARGET_RESOLUTION_REQUIRED",
                    "CAN-GUKSA",
                    "",
                    '["PARTICIPATE_OR_ACT"]',
                    "국사를 활동지침으로 삼았다.",
                )
            ]
        )
        tables, _ = self.build_tables(
            candidates,
            self.make_registry(),
            self.make_facts(),
            self.policy,
        )
        check = tables["official_checks"].iloc[0]

        self.assertNotIn(
            "CAN-COMRADE",
            check["recovered_mentions_json"],
        )
        self.assertEqual(
            check["verification_status"],
            "ENDPOINTS_UNRESOLVED",
        )

    def test_false_context_candidate_is_not_processed(self):
        candidates = pd.DataFrame(
            [
                self.make_candidate(
                    "FALSE-CONTEXT",
                    "BLOCKED_FALSE_CONTEXT",
                    "CAN-GEOCHILBU",
                    "CAN-GUKSA",
                    '["AUTHOR_OR_COMPILE"]',
                    "거칠부가 국사를 편찬하였다.",
                )
            ]
        )
        tables, statistics = self.build_tables(
            candidates,
            self.make_registry(),
            self.make_facts(),
            self.policy,
        )

        self.assertTrue(tables["official_checks"].empty)
        self.assertEqual(statistics["eligible_candidate_count"], 0)
        self.assertEqual(statistics["verified_link_count"], 0)


if __name__ == "__main__":
    unittest.main()

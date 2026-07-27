import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


class ExamRelationOfficialTextCorroborationTest(
    unittest.TestCase
):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from choice_relation.official_text_corroboration import (
            build_exam_relation_official_text_tables,
            load_exam_relation_official_text_policy,
        )

        cls.build_tables = staticmethod(
            build_exam_relation_official_text_tables
        )
        cls.policy = load_exam_relation_official_text_policy(
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
                    "canonical_id": "CAN-CHOE",
                    "entity_type": "Person",
                    "display_name": "최제우",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": "[]",
                },
                {
                    "canonical_id": "CAN-DONGHAK",
                    "entity_type": "Concept",
                    "display_name": "동학",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E-DONGHAK:release"]'
                    ),
                },
            ]
        )

    def make_check(
        self,
        candidate_id: str,
        verification_status: str,
        predicates: str,
        existing_ids: str,
    ) -> dict:
        return {
            "exam_relation_candidate_id": candidate_id,
            "claim_segment_id": f"CLAIM-{candidate_id}",
            "problem_id": f"PROBLEM-{candidate_id}",
            "verification_status": verification_status,
            "predicate_families_json": predicates,
            "existing_canonical_ids_json": existing_ids,
            "recovered_mentions_json": "[]",
            "exam_evidence_text": "최제우가 동학을 창시하였다.",
        }

    def write_aks_fixture(self, directory: str) -> str:
        output_path = Path(directory) / "aks.jsonl"
        record = {
            "eid": "E-DONGHAK",
            "url": "https://encykorea.aks.ac.kr/Article/E-DONGHAK",
            "headword": "동학",
            "definition": "1860년 최제우에 의해 창시된 민족 종교.",
            "summary": "",
            "body": "",
        }
        output_path.write_text(
            json.dumps(record, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return str(output_path)

    def test_subject_context_and_predicate_support_candidate(self):
        checks = pd.DataFrame(
            [
                self.make_check(
                    "SUPPORTED",
                    "OFFICIAL_FACT_NOT_FOUND",
                    '["FOUND_OR_ESTABLISH"]',
                    '["CAN-CHOE", "CAN-DONGHAK"]',
                )
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            tables, statistics = self.build_tables(
                checks,
                self.make_registry(),
                self.write_aks_fixture(directory),
                self.policy,
            )
        check = tables["text_checks"].iloc[0]
        evidence = tables["text_evidence"].iloc[0]

        self.assertEqual(
            check["search_status"],
            "SUPPORTED_BY_AKS_TEXT_STRICT",
        )
        self.assertEqual(
            evidence["evidence_mode"],
            "SUBJECT_CONTEXT_AND_EXPLICIT_OBJECT",
        )
        self.assertEqual(
            evidence["shared_predicate_families_json"],
            '["FOUND_OR_ESTABLISH"]',
        )
        self.assertEqual(
            evidence["shared_predicate_patterns_json"],
            '["창시"]',
        )
        self.assertFalse(evidence["may_create_new_fact"])
        self.assertEqual(statistics["supported_candidate_count"], 1)

    def test_one_third_party_sentence_is_insufficient(self):
        checks = pd.DataFrame(
            [
                self.make_check(
                    "ONE-THIRD-PARTY",
                    "OFFICIAL_FACT_NOT_FOUND",
                    '["FOUND_OR_ESTABLISH"]',
                    '["CAN-CHOE", "CAN-DONGHAK"]',
                )
            ]
        )
        record = {
            "eid": "E-OTHER",
            "url": "https://encykorea.aks.ac.kr/Article/E-OTHER",
            "headword": "다른 문서",
            "definition": "최제우가 동학을 창시하였다.",
            "summary": "",
            "body": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "aks.jsonl"
            fixture_path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tables, statistics = self.build_tables(
                checks,
                self.make_registry(),
                str(fixture_path),
                self.policy,
            )
        check = tables["text_checks"].iloc[0]

        self.assertEqual(
            check["search_status"],
            "AKS_TEXT_EVIDENCE_INSUFFICIENT",
        )
        self.assertFalse(check["strict_support"])
        self.assertEqual(statistics["supported_candidate_count"], 0)

    def test_predicate_missing_candidate_is_skipped(self):
        checks = pd.DataFrame(
            [
                self.make_check(
                    "NO-PREDICATE",
                    "PREDICATE_UNRESOLVED",
                    "[]",
                    '["CAN-CHOE", "CAN-DONGHAK"]',
                )
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            tables, _ = self.build_tables(
                checks,
                self.make_registry(),
                self.write_aks_fixture(directory),
                self.policy,
            )
        check = tables["text_checks"].iloc[0]

        self.assertEqual(
            check["search_status"],
            "SKIPPED_PREDICATE_UNRESOLVED",
        )
        self.assertTrue(tables["text_evidence"].empty)

    def test_different_pattern_in_same_family_is_not_support(self):
        checks = pd.DataFrame(
            [
                self.make_check(
                    "DIFFERENT-PATTERN",
                    "OFFICIAL_FACT_NOT_FOUND",
                    '["FOUND_OR_ESTABLISH"]',
                    '["CAN-CHOE", "CAN-DONGHAK"]',
                )
            ]
        )
        record = {
            "eid": "E-DONGHAK",
            "url": "https://encykorea.aks.ac.kr/Article/E-DONGHAK",
            "headword": "동학",
            "definition": "최제우가 동학을 설립하였다.",
            "summary": "",
            "body": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "aks.jsonl"
            fixture_path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tables, _ = self.build_tables(
                checks,
                self.make_registry(),
                str(fixture_path),
                self.policy,
            )
        check = tables["text_checks"].iloc[0]

        self.assertEqual(
            check["search_status"],
            "NO_AKS_TEXT_SUPPORT",
        )
        self.assertTrue(tables["text_evidence"].empty)

    def test_existing_fact_candidate_is_not_rescanned(self):
        checks = pd.DataFrame(
            [
                self.make_check(
                    "VERIFIED",
                    "VERIFIED_EXISTING_FACT",
                    '["FOUND_OR_ESTABLISH"]',
                    '["CAN-CHOE", "CAN-DONGHAK"]',
                )
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            tables, _ = self.build_tables(
                checks,
                self.make_registry(),
                self.write_aks_fixture(directory),
                self.policy,
            )
        check = tables["text_checks"].iloc[0]

        self.assertEqual(
            check["search_status"],
            "SKIPPED_ALREADY_VERIFIED",
        )
        self.assertTrue(tables["text_evidence"].empty)

    def test_one_endpoint_is_recovered_from_official_eid_link(self):
        checks = pd.DataFrame(
            [
                self.make_check(
                    "FRAGMENT",
                    "ENDPOINTS_UNRESOLVED",
                    '["FOUND_OR_ESTABLISH"]',
                    '["CAN-DONGHAK"]',
                )
            ]
        )
        registry = pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-CHOE",
                    "entity_type": "Person",
                    "display_name": "최제우",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000002:release"]'
                    ),
                },
                {
                    "canonical_id": "CAN-DONGHAK",
                    "entity_type": "Concept",
                    "display_name": "동학",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000001:release"]'
                    ),
                },
            ]
        )
        record = {
            "eid": "E0000001",
            "url": "https://example.test/donghak",
            "headword": "동학",
            "definition": (
                "[최제우](E0000002)가 동학을 창시하였다."
            ),
            "summary": "",
            "body": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "aks.jsonl"
            fixture_path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tables, statistics = self.build_tables(
                checks,
                registry,
                str(fixture_path),
                self.policy,
            )
        check = tables["text_checks"].iloc[0]

        self.assertEqual(
            check["search_status"],
            "SUPPORTED_BY_AKS_TEXT_STRICT",
        )
        self.assertEqual(
            set(json.loads(check["resolved_endpoint_ids_json"])),
            {"CAN-CHOE", "CAN-DONGHAK"},
        )
        self.assertEqual(
            statistics["endpoint_fragment_task_count"],
            1,
        )
        evidence = tables["text_evidence"].iloc[0]
        self.assertTrue(evidence["discovered_endpoint_linked"])

    def test_unlinked_name_is_not_used_as_discovered_endpoint(self):
        checks = pd.DataFrame(
            [
                self.make_check(
                    "UNLINKED-FRAGMENT",
                    "ENDPOINTS_UNRESOLVED",
                    '["FOUND_OR_ESTABLISH"]',
                    '["CAN-DONGHAK"]',
                )
            ]
        )
        registry = pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-CHOE",
                    "entity_type": "Person",
                    "display_name": "최제우",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000002:release"]'
                    ),
                },
                {
                    "canonical_id": "CAN-DONGHAK",
                    "entity_type": "Concept",
                    "display_name": "동학",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000001:release"]'
                    ),
                },
            ]
        )
        record = {
            "eid": "E0000001",
            "url": "https://example.test/donghak",
            "headword": "동학",
            "definition": "최제우가 동학을 창시하였다.",
            "summary": "",
            "body": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "aks.jsonl"
            fixture_path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tables, _ = self.build_tables(
                checks,
                registry,
                str(fixture_path),
                self.policy,
            )

        self.assertEqual(
            tables["text_checks"].iloc[0]["search_status"],
            "NO_AKS_TEXT_SUPPORT",
        )
        self.assertTrue(tables["text_evidence"].empty)

    def test_endpoint_after_predicate_is_not_used_as_actor(self):
        checks = pd.DataFrame(
            [
                self.make_check(
                    "POST-PREDICATE-FRAGMENT",
                    "ENDPOINTS_UNRESOLVED",
                    '["FOUND_OR_ESTABLISH"]',
                    '["CAN-DONGHAK"]',
                )
            ]
        )
        registry = pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-CHOE",
                    "entity_type": "Person",
                    "display_name": "최제우",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000002:release"]'
                    ),
                },
                {
                    "canonical_id": "CAN-DONGHAK",
                    "entity_type": "Concept",
                    "display_name": "동학",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000001:release"]'
                    ),
                },
            ]
        )
        record = {
            "eid": "E0000001",
            "url": "https://example.test/donghak",
            "headword": "동학",
            "definition": (
                "동학을 창시한 인물은 [최제우](E0000002)이다."
            ),
            "summary": "",
            "body": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "aks.jsonl"
            fixture_path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tables, _ = self.build_tables(
                checks,
                registry,
                str(fixture_path),
                self.policy,
            )

        self.assertEqual(
            tables["text_checks"].iloc[0]["search_status"],
            "NO_AKS_TEXT_SUPPORT",
        )
        self.assertTrue(tables["text_evidence"].empty)

    def test_multiple_strict_endpoint_pairs_remain_ambiguous(self):
        checks = pd.DataFrame(
            [
                self.make_check(
                    "AMBIGUOUS-FRAGMENT",
                    "ENDPOINTS_UNRESOLVED",
                    '["FOUND_OR_ESTABLISH"]',
                    '["CAN-DONGHAK"]',
                )
            ]
        )
        registry = pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-CHOE",
                    "entity_type": "Person",
                    "display_name": "최제우",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000002:release"]'
                    ),
                },
                {
                    "canonical_id": "CAN-OTHER",
                    "entity_type": "Person",
                    "display_name": "다른인물",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000003:release"]'
                    ),
                },
                {
                    "canonical_id": "CAN-DONGHAK",
                    "entity_type": "Concept",
                    "display_name": "동학",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:ARTICLE:E0000001:release"]'
                    ),
                },
            ]
        )
        record = {
            "eid": "E0000001",
            "url": "https://example.test/donghak",
            "headword": "동학",
            "definition": (
                "[최제우](E0000002)가 동학을 창시하였다."
            ),
            "summary": (
                "[다른인물](E0000003)이 동학을 창시하였다."
            ),
            "body": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "aks.jsonl"
            fixture_path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tables, _ = self.build_tables(
                checks,
                registry,
                str(fixture_path),
                self.policy,
            )
        check = tables["text_checks"].iloc[0]

        self.assertEqual(
            check["search_status"],
            "AMBIGUOUS_AKS_ENDPOINT_RECOVERY",
        )
        self.assertFalse(check["strict_support"])
        self.assertEqual(check["strict_supported_pair_count"], 2)


if __name__ == "__main__":
    unittest.main()

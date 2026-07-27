import sys
import unittest
from pathlib import Path

import pandas as pd


class ExamMatchRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = (
            project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(neo4j_root))

        from entity_resolution.exam_match_recovery import (
            build_exam_match_recovery_tables,
            load_exam_match_recovery_policy,
        )

        cls.build_tables = staticmethod(build_exam_match_recovery_tables)
        cls.policy = load_exam_match_recovery_policy(
            str(neo4j_root / "config" / "exam_match_recovery.json")
        )

    def build_inputs(self) -> dict[str, pd.DataFrame]:
        cases = pd.DataFrame(
            [
                {
                    "resolution_case_id": "CASE-UNIQUE",
                    "canonical_term": "개경",
                    "category": "지역",
                    "entity_type_proposal": "Place",
                },
                {
                    "resolution_case_id": "CASE-HOMONYM",
                    "canonical_term": "고종",
                    "category": "인물",
                    "entity_type_proposal": "Person",
                },
                {
                    "resolution_case_id": "CASE-DUPLICATE",
                    "canonical_term": "윤충",
                    "category": "인물",
                    "entity_type_proposal": "Person",
                },
            ]
        )
        candidates = pd.DataFrame(
            [
                {
                    "resolution_case_id": "CASE-UNIQUE",
                    "source_record_id": "AKS:PLACE",
                    "source": "AKS",
                    "matched_name": "개경",
                    "retrieval_methods_json": '["exact"]',
                    "retrieval_score": "1.0",
                    "category_compatibility": "COMPATIBLE",
                },
                {
                    "resolution_case_id": "CASE-UNIQUE",
                    "source_record_id": "AKS:RELATED",
                    "source": "AKS",
                    "matched_name": "개성",
                    "retrieval_methods_json": '["definition"]',
                    "retrieval_score": "0.8",
                    "category_compatibility": "COMPATIBLE",
                },
                {
                    "resolution_case_id": "CASE-HOMONYM",
                    "source_record_id": "AKS:GORYEO-KING",
                    "source": "AKS",
                    "matched_name": "고종",
                    "retrieval_methods_json": '["exact"]',
                    "retrieval_score": "1.0",
                    "category_compatibility": "COMPATIBLE",
                },
                {
                    "resolution_case_id": "CASE-HOMONYM",
                    "source_record_id": "AKS:JOSEON-KING",
                    "source": "AKS",
                    "matched_name": "고종",
                    "retrieval_methods_json": '["exact"]',
                    "retrieval_score": "1.0",
                    "category_compatibility": "COMPATIBLE",
                },
                {
                    "resolution_case_id": "CASE-DUPLICATE",
                    "source_record_id": "AKS:YUNCHUNG",
                    "source": "AKS",
                    "matched_name": "윤충",
                    "retrieval_methods_json": '["exact"]',
                    "retrieval_score": "1.0",
                    "category_compatibility": "COMPATIBLE",
                },
                {
                    "resolution_case_id": "CASE-DUPLICATE",
                    "source_record_id": "THESAURUS:YUNCHUNG",
                    "source": "THESAURUS",
                    "matched_name": "윤충",
                    "retrieval_methods_json": '["exact"]',
                    "retrieval_score": "1.0",
                    "category_compatibility": "COMPATIBLE",
                },
            ]
        )
        registry = pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-PLACE",
                    "entity_type": "Place",
                    "display_name": "개경",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": '["AKS:PLACE"]',
                    "resolution_case_ids_json": '["CASE-UNIQUE"]',
                },
                {
                    "canonical_id": "CAN-GORYEO-KING",
                    "entity_type": "Person",
                    "display_name": "고종",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:GORYEO-KING"]'
                    ),
                    "resolution_case_ids_json": '["CASE-HOMONYM"]',
                },
                {
                    "canonical_id": "CAN-JOSEON-KING",
                    "entity_type": "Person",
                    "display_name": "고종",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:JOSEON-KING"]'
                    ),
                    "resolution_case_ids_json": '["CASE-HOMONYM"]',
                },
                {
                    "canonical_id": "CAN-YUNCHUNG-A",
                    "entity_type": "Person",
                    "display_name": "윤충",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["AKS:YUNCHUNG"]'
                    ),
                    "resolution_case_ids_json": '["CASE-DUPLICATE"]',
                },
                {
                    "canonical_id": "CAN-YUNCHUNG-B",
                    "entity_type": "Person",
                    "display_name": "윤충",
                    "lifecycle_status": "ACTIVE",
                    "identity_member_source_ids_json": (
                        '["THESAURUS:YUNCHUNG"]'
                    ),
                    "resolution_case_ids_json": '["CASE-DUPLICATE"]',
                },
            ]
        )
        assignments = pd.DataFrame(
            [
                {
                    "problem_assignment_id": "PA-UNIQUE",
                    "problem_id": "P-UNIQUE",
                    "resolution_case_id": "CASE-UNIQUE",
                    "canonical_ids_json": "[]",
                    "link_status": "AMBIGUOUS",
                },
                {
                    "problem_assignment_id": "PA-HOMONYM",
                    "problem_id": "P-HOMONYM",
                    "resolution_case_id": "CASE-HOMONYM",
                    "canonical_ids_json": "[]",
                    "link_status": "AMBIGUOUS",
                },
                {
                    "problem_assignment_id": "PA-DUPLICATE",
                    "problem_id": "P-DUPLICATE",
                    "resolution_case_id": "CASE-DUPLICATE",
                    "canonical_ids_json": "[]",
                    "link_status": "AMBIGUOUS",
                },
                {
                    "problem_assignment_id": "PA-N1-H",
                    "problem_id": "P-HOMONYM",
                    "resolution_case_id": "NEIGHBOR-1",
                    "canonical_ids_json": '["CAN-NEIGHBOR-1"]',
                    "link_status": "ACCEPTED",
                },
                {
                    "problem_assignment_id": "PA-N2-H",
                    "problem_id": "P-HOMONYM",
                    "resolution_case_id": "NEIGHBOR-2",
                    "canonical_ids_json": '["CAN-NEIGHBOR-2"]',
                    "link_status": "ACCEPTED",
                },
                {
                    "problem_assignment_id": "PA-N1-D",
                    "problem_id": "P-DUPLICATE",
                    "resolution_case_id": "NEIGHBOR-1",
                    "canonical_ids_json": '["CAN-NEIGHBOR-1"]',
                    "link_status": "ACCEPTED",
                },
                {
                    "problem_assignment_id": "PA-N2-D",
                    "problem_id": "P-DUPLICATE",
                    "resolution_case_id": "NEIGHBOR-2",
                    "canonical_ids_json": '["CAN-NEIGHBOR-2"]',
                    "link_status": "ACCEPTED",
                },
            ]
        )
        era_relationships = pd.DataFrame(
            [
                {
                    "canonical_id": "CAN-GORYEO-KING",
                    "era_id": "era:goryeo",
                    "verification_status": "VERIFIED",
                },
                {
                    "canonical_id": "CAN-JOSEON-KING",
                    "era_id": "era:joseon",
                    "verification_status": "VERIFIED",
                },
                {
                    "canonical_id": "CAN-YUNCHUNG-A",
                    "era_id": "era:joseon",
                    "verification_status": "VERIFIED",
                },
                {
                    "canonical_id": "CAN-YUNCHUNG-B",
                    "era_id": "era:joseon",
                    "verification_status": "VERIFIED",
                },
                {
                    "canonical_id": "CAN-NEIGHBOR-1",
                    "era_id": "era:joseon",
                    "verification_status": "VERIFIED",
                },
                {
                    "canonical_id": "CAN-NEIGHBOR-2",
                    "era_id": "era:joseon",
                    "verification_status": "VERIFIED",
                },
            ]
        )
        term_nodes = pd.DataFrame(
            [
                {
                    "exam_term_id": "TERM-UNIQUE",
                    "term": "개경",
                    "categories_json": '["지역"]',
                    "source_link_status": "PENDING",
                    "resolution_case_ids_json": '["CASE-UNIQUE"]',
                },
                {
                    "exam_term_id": "TERM-HOMONYM",
                    "term": "고종",
                    "categories_json": '["인물"]',
                    "source_link_status": "PENDING",
                    "resolution_case_ids_json": '["CASE-HOMONYM"]',
                },
                {
                    "exam_term_id": "TERM-DUPLICATE",
                    "term": "윤충",
                    "categories_json": '["인물"]',
                    "source_link_status": "PENDING",
                    "resolution_case_ids_json": '["CASE-DUPLICATE"]',
                },
            ]
        )
        term_relationships = pd.DataFrame(
            columns=[
                "exam_term_id",
                "canonical_id",
                "match_status",
            ]
        )
        current_facts = pd.DataFrame(
            [
                {
                    "start_canonical_id": "CAN-JOSEON-KING",
                    "end_canonical_id": "CAN-NEIGHBOR-1",
                    "relation_type": "PARTICIPATED_IN",
                }
            ]
        )
        staged_facts = pd.DataFrame(
            [
                {
                    "start_canonical_id": "CAN-PLACE",
                    "end_canonical_id": "CAN-NEIGHBOR-2",
                    "relation_type": "LOCATED_IN",
                    "candidate_status": "READY_TO_PROJECT",
                }
            ]
        )
        problem_contexts = pd.DataFrame(
            [
                {"problem_id": "P-UNIQUE"},
                {"problem_id": "P-HOMONYM"},
                {"problem_id": "P-DUPLICATE"},
                {"problem_id": "P-NO-TERM"},
            ]
        )
        return {
            "resolution_cases": cases,
            "source_candidates": candidates,
            "canonical_registry": registry,
            "final_assignments": assignments,
            "problem_contexts": problem_contexts,
            "canonical_era_relationships": era_relationships,
            "exam_term_nodes": term_nodes,
            "exam_term_relationships": term_relationships,
            "current_fact_relationships": current_facts,
            "staged_fact_relationships": staged_facts,
        }

    def test_safe_recovery_keeps_duplicate_candidates_for_review(self):
        tables, statistics = self.build_tables(
            **self.build_inputs(),
            policy=self.policy,
        )
        recovery = tables["problem_recovery"].set_index(
            "resolution_case_id"
        )

        self.assertEqual(
            recovery.loc["CASE-UNIQUE", "selected_canonical_id"],
            "CAN-PLACE",
        )
        self.assertEqual(
            recovery.loc["CASE-UNIQUE", "selection_method"],
            "UNIQUE_EXACT_OFFICIAL_NAME",
        )
        self.assertEqual(
            recovery.loc["CASE-HOMONYM", "selected_canonical_id"],
            "CAN-JOSEON-KING",
        )
        self.assertEqual(
            recovery.loc["CASE-HOMONYM", "selection_method"],
            "EXACT_NAME_AND_PROBLEM_ERA",
        )
        self.assertEqual(
            recovery.loc["CASE-DUPLICATE", "selected_canonical_id"],
            "",
        )
        self.assertEqual(
            recovery.loc["CASE-DUPLICATE", "selection_method"],
            "DUPLICATE_OR_HOMONYM_REVIEW",
        )
        self.assertEqual(statistics["auto_accept_candidate_count"], 2)
        self.assertEqual(statistics["duplicate_review_case_count"], 1)

    def test_projected_matching_and_fact_coverage_are_separate(self):
        tables, statistics = self.build_tables(
            **self.build_inputs(),
            policy=self.policy,
        )
        terms = tables["term_recovery"].set_index("exam_term_id")
        coverage = tables["problem_fact_coverage"].set_index(
            "problem_id"
        )

        self.assertEqual(
            terms.loc["TERM-UNIQUE", "projected_source_link_status"],
            "ACCEPTED",
        )
        self.assertEqual(
            terms.loc["TERM-HOMONYM", "projected_source_link_status"],
            "ACCEPTED",
        )
        self.assertEqual(
            terms.loc["TERM-DUPLICATE", "projected_source_link_status"],
            "PENDING",
        )
        self.assertEqual(
            coverage.loc[
                "P-HOMONYM",
                "projected_internal_core_fact_count",
            ],
            1,
        )
        self.assertEqual(
            coverage.loc[
                "P-UNIQUE",
                "projected_core_with_staged_fact_endpoint_count",
            ],
            1,
        )
        self.assertEqual(statistics["problem_count"], 4)


if __name__ == "__main__":
    unittest.main()

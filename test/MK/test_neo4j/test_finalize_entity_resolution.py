import json
import sys
import unittest
from pathlib import Path

import pandas as pd


class FinalizeEntityResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))
        sys.path.insert(0, str(neo4j_root / "terms"))

        from common import load_pipeline_policy
        from entity_resolution.finalize_entity_resolution import (
            finalize_entity_resolution,
            load_existing_registry,
        )

        cls.finalize = staticmethod(finalize_entity_resolution)
        cls.load_registry = staticmethod(load_existing_registry)
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def build_fixture(
        self,
        member_candidate_ids: list[str] | None = None,
        alternative_id: str = "alternative-1",
    ):
        active_member_ids = member_candidate_ids
        if active_member_ids is None:
            active_member_ids = ["candidate-aks", "candidate-thesaurus"]
        candidates = [
            {
                "source_candidate_id": "candidate-aks",
                "source_record_id": "AKS:ARTICLE:E1:release",
                "source": "AKS",
                "source_key": "E1",
                "source_release": "release",
                "source_metadata_json": json.dumps(
                    {"headword": "이순신"},
                    ensure_ascii=False,
                ),
            },
            {
                "source_candidate_id": "candidate-thesaurus",
                "source_record_id": "THESAURUS:TERM:T1:release",
                "source": "THESAURUS",
                "source_key": "T1",
                "source_release": "release",
                "source_metadata_json": json.dumps(
                    {"term_name": "이순신"},
                    ensure_ascii=False,
                ),
            },
            {
                "source_candidate_id": "candidate-extra",
                "source_record_id": "ITKC:PERSON:P1:release",
                "source": "ITKC_PERSON",
                "source_key": "P1",
                "source_release": "release",
                "source_metadata_json": json.dumps(
                    {"name": "이순신"},
                    ensure_ascii=False,
                ),
            },
        ]
        candidate_by_id = {
            row["source_candidate_id"]: row for row in candidates
        }
        source_record_ids = [
            candidate_by_id[candidate_id]["source_record_id"]
            for candidate_id in active_member_ids
        ]
        resolution_tables = {
            "resolution_cases": pd.DataFrame(
                [
                    {
                        "resolution_case_id": "case-1",
                        "canonical_term": "이순신",
                        "term_variants_json": json.dumps(
                            ["이순신", "충무공 이순신"],
                            ensure_ascii=False,
                        ),
                    }
                ]
            ),
            "source_record_candidates": pd.DataFrame(candidates),
        }
        alternatives = pd.DataFrame(
            [
                {
                    "canonical_alternative_id": alternative_id,
                    "resolution_case_id": "case-1",
                    "display_name_proposal": "이순신(조선)",
                    "entity_type_proposal": "Person",
                    "source_candidate_ids_json": json.dumps(
                        active_member_ids,
                        ensure_ascii=False,
                    ),
                    "identity_member_source_ids_json": json.dumps(
                        source_record_ids,
                        ensure_ascii=False,
                    ),
                    "member_count": len(active_member_ids),
                    "merge_gate_passed": True,
                    "verification_status": "VERIFIED",
                    "term_decision_id": "term-decision-1",
                }
            ]
        )
        roles = []
        for candidate in candidates:
            candidate_id = candidate["source_candidate_id"]
            role = "EVIDENCE_ONLY"
            canonical_alternative_id = ""
            if candidate_id in active_member_ids:
                role = "IDENTITY_MEMBER"
                canonical_alternative_id = alternative_id
            roles.append(
                {
                    "source_candidate_id": candidate_id,
                    "canonical_alternative_id": canonical_alternative_id,
                    "verified_role": role,
                    "verification_status": "VERIFIED",
                }
            )
        term_tables = {
            "reviewed_canonical_alternatives": alternatives,
            "reviewed_source_roles": pd.DataFrame(roles),
        }
        assignments = pd.DataFrame(
            [
                {
                    "problem_assignment_id": "assignment-1",
                    "problem_id": "question-1",
                    "resolution_case_id": "case-1",
                    "selected_canonical_alternative_ids_json": json.dumps(
                        [alternative_id],
                        ensure_ascii=False,
                    ),
                    "selection_mode": "SINGLE",
                    "resolution_method": "llm_per_problem",
                    "verification_status": "VERIFIED",
                }
            ]
        )
        return resolution_tables, term_tables, assignments

    def test_verified_multi_source_alternative_creates_identity_import(self):
        resolution_tables, term_tables, assignments = self.build_fixture()

        outputs = self.finalize(
            resolution_tables,
            term_tables,
            assignments,
            self.load_registry(""),
            self.policy,
            uuid_factory=lambda: "uuid-fixed",
            timestamp="2026-07-21T00:00:00+00:00",
        )

        registry = outputs["canonical_registry"]
        resolutions = outputs["source_record_resolutions"]
        final_assignments = outputs["final_problem_assignments"]
        self.assertEqual(len(registry), 1)
        self.assertEqual(
            registry.iloc[0]["canonical_id"],
            "canonical:person:uuid-fixed",
        )
        self.assertNotIn("E1", registry.iloc[0]["canonical_id"])
        self.assertEqual(len(resolutions), 2)
        self.assertEqual(set(resolutions["match_status"]), {"ACCEPTED"})
        self.assertEqual(final_assignments.iloc[0]["link_status"], "ACCEPTED")
        self.assertEqual(len(outputs["entity_name_nodes"]), 2)

    def test_evidence_only_source_does_not_resolve_to_canonical(self):
        resolution_tables, term_tables, assignments = self.build_fixture()

        outputs = self.finalize(
            resolution_tables,
            term_tables,
            assignments,
            self.load_registry(""),
            self.policy,
            uuid_factory=lambda: "uuid-fixed",
            timestamp="2026-07-21T00:00:00+00:00",
        )

        self.assertNotIn(
            "ITKC:PERSON:P1:release",
            set(outputs["source_record_resolutions"]["source_record_id"]),
        )

    def test_single_source_alternative_remains_in_review_queue(self):
        resolution_tables, term_tables, assignments = self.build_fixture(
            member_candidate_ids=["candidate-aks"]
        )

        outputs = self.finalize(
            resolution_tables,
            term_tables,
            assignments,
            self.load_registry(""),
            self.policy,
            uuid_factory=lambda: "unused",
            timestamp="2026-07-21T00:00:00+00:00",
        )

        self.assertTrue(outputs["canonical_registry"].empty)
        self.assertEqual(len(outputs["canonical_acceptance_review_queue"]), 1)
        self.assertEqual(
            outputs["final_problem_assignments"].iloc[0]["link_status"],
            "AMBIGUOUS",
        )

    def test_registry_id_is_reused_when_one_source_record_persists(self):
        resolution_tables, term_tables, assignments = self.build_fixture()
        first_outputs = self.finalize(
            resolution_tables,
            term_tables,
            assignments,
            self.load_registry(""),
            self.policy,
            uuid_factory=lambda: "uuid-fixed",
            timestamp="2026-07-21T00:00:00+00:00",
        )
        second_resolution, second_terms, second_assignments = self.build_fixture(
            member_candidate_ids=["candidate-thesaurus", "candidate-extra"],
            alternative_id="alternative-2",
        )

        second_outputs = self.finalize(
            second_resolution,
            second_terms,
            second_assignments,
            first_outputs["canonical_registry"],
            self.policy,
            uuid_factory=lambda: "must-not-be-used",
            timestamp="2026-07-22T00:00:00+00:00",
        )

        registry = second_outputs["canonical_registry"]
        self.assertEqual(len(registry), 1)
        self.assertEqual(
            registry.iloc[0]["canonical_id"],
            "canonical:person:uuid-fixed",
        )
        source_ids = set(
            json.loads(registry.iloc[0]["identity_member_source_ids_json"])
        )
        self.assertEqual(
            source_ids,
            {
                "AKS:ARTICLE:E1:release",
                "THESAURUS:TERM:T1:release",
                "ITKC:PERSON:P1:release",
            },
        )


if __name__ == "__main__":
    unittest.main()

import copy
import json
import sys
import unittest
from pathlib import Path

import pandas as pd


class CanonicalAlternativeProposalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))
        sys.path.insert(0, str(neo4j_root / "terms"))

        from common import load_pipeline_policy
        from entity_resolution.propose_canonical_alternatives import (
            build_source_candidate_proposal_tables,
        )

        cls.build_proposals = staticmethod(build_source_candidate_proposal_tables)
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def make_case(self, case_id: str = "case-1") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "resolution_case_id": case_id,
                    "canonical_term": "이순신",
                    "entity_type_proposal": "Person",
                }
            ]
        )

    def make_candidate(
        self,
        candidate_id: str,
        source: str,
        metadata: dict,
        retrieval_method: str = "exact",
        compatibility: str = "COMPATIBLE",
        case_id: str = "case-1",
    ) -> dict:
        return {
            "source_candidate_id": candidate_id,
            "resolution_case_id": case_id,
            "source_record_id": f"{source}:RECORD:{candidate_id}:release",
            "source": source,
            "category_compatibility": compatibility,
            "retrieval_method": retrieval_method,
            "source_metadata_json": json.dumps(metadata, ensure_ascii=False),
        }

    def build_tables(self, candidates: list[dict], policy: dict | None = None):
        active_policy = self.policy
        if policy is not None:
            active_policy = policy
        base_tables = {
            "resolution_cases": self.make_case(),
            "source_record_candidates": pd.DataFrame(candidates),
        }
        return self.build_proposals(base_tables, active_policy)

    def test_multiple_sources_are_kept_as_identity_members(self):
        candidates = [
            self.make_candidate(
                "candidate-aks",
                "AKS",
                {
                    "headword": "이순신",
                    "aliases": ["李舜臣"],
                    "era": "조선/조선 후기",
                    "primary_type_part": "인물",
                },
            ),
            self.make_candidate(
                "candidate-thesaurus",
                "THESAURUS",
                {
                    "term_name": "이순신",
                    "hanja": "李舜臣",
                    "era": "조선 후기",
                    "thesaurus_category": "인명",
                },
            ),
            self.make_candidate(
                "candidate-itkc",
                "ITKC_PERSON",
                {
                    "name": "이순신",
                    "hanja": "李舜臣",
                    "birth_year": "1545",
                    "death_year": "1598",
                },
            ),
        ]
        tables = self.build_tables(candidates)

        clusters = tables["canonical_alternative_clusters"]
        members = tables["canonical_cluster_members"]
        features = tables["source_candidate_features"]
        self.assertEqual(len(clusters), 1)
        self.assertEqual(int(clusters.iloc[0]["member_count"]), 3)
        self.assertEqual(int(clusters.iloc[0]["source_system_count"]), 3)
        self.assertEqual(set(members["proposed_case_role"]), {"IDENTITY_MEMBER"})
        self.assertEqual(set(features["proposed_role"]), {"IDENTITY_MEMBER"})

    def test_clothing_source_type_maps_to_heritage(self):
        candidate = self.make_candidate(
            "candidate-clothing",
            "AKS",
            {
                "headword": "몸뻬",
                "primary_type_part": "의복",
            },
        )

        tables = self.build_tables([candidate])
        features = tables["source_candidate_features"]

        self.assertEqual(
            features.iloc[0]["source_entity_type_proposal"],
            "Heritage",
        )
        self.assertTrue(all(value == "PROPOSED" for value in features["role_status"]))

    def test_same_source_records_are_not_automatically_merged(self):
        candidates = [
            self.make_candidate(
                "candidate-aks-1",
                "AKS",
                {
                    "headword": "고종",
                    "aliases": ["高宗"],
                    "era": "고려",
                    "primary_type_part": "인물",
                },
            ),
            self.make_candidate(
                "candidate-aks-2",
                "AKS",
                {
                    "headword": "고종",
                    "aliases": ["高宗"],
                    "era": "고려",
                    "primary_type_part": "인물",
                },
            ),
        ]
        tables = self.build_tables(candidates)

        pairs = tables["source_candidate_pair_signals"]
        clusters = tables["canonical_alternative_clusters"]
        self.assertFalse(bool(pairs.iloc[0]["merge_eligible"]))
        self.assertTrue(bool(pairs.iloc[0]["same_source_system"]))
        self.assertEqual(len(clusters), 2)

    def test_birth_year_conflict_keeps_cross_source_candidates_separate(self):
        policy = copy.deepcopy(self.policy)
        policy["entity_resolution"]["source_feature_policy"]["sources"][
            "OTHER_PERSON"
        ] = {
            "name_fields": ["name"],
            "hanja_fields": ["hanja"],
            "era_fields": [],
            "birth_year_field": "birth_year",
            "death_year_field": "death_year",
            "default_entity_type": "Person",
        }
        candidates = [
            self.make_candidate(
                "candidate-itkc",
                "ITKC_PERSON",
                {
                    "name": "동명이인",
                    "hanja": "同名異人",
                    "birth_year": "1000",
                    "death_year": "1070",
                },
            ),
            self.make_candidate(
                "candidate-other",
                "OTHER_PERSON",
                {
                    "name": "동명이인",
                    "hanja": "同名異人",
                    "birth_year": "1200",
                    "death_year": "1270",
                },
            ),
        ]
        tables = self.build_tables(candidates, policy)

        pair = tables["source_candidate_pair_signals"].iloc[0]
        conflicts = set(json.loads(pair["conflict_signals_json"]))
        self.assertFalse(bool(pair["merge_eligible"]))
        self.assertEqual(
            conflicts,
            {"birth_year_conflict", "death_year_conflict"},
        )
        self.assertEqual(len(tables["canonical_alternative_clusters"]), 2)

    def test_complete_link_blocks_transitive_bridge_merge(self):
        candidates = [
            self.make_candidate(
                "candidate-a",
                "AKS",
                {
                    "headword": "동명이인",
                    "aliases": ["甲"],
                    "primary_type_part": "인물",
                },
            ),
            self.make_candidate(
                "candidate-b",
                "THESAURUS",
                {
                    "term_name": "동명이인",
                    "hanja": "甲 乙",
                    "thesaurus_category": "인명",
                },
            ),
            self.make_candidate(
                "candidate-c",
                "ITKC_PERSON",
                {"name": "동명이인", "hanja": "乙"},
            ),
        ]
        tables = self.build_tables(candidates)

        clusters = tables["canonical_alternative_clusters"]
        self.assertEqual(len(clusters), 2)
        self.assertEqual(int(clusters["member_count"].max()), 2)
        self.assertEqual(int(clusters["member_count"].sum()), 3)

    def test_conflict_is_rejected_and_weak_description_hit_stays_ambiguous(self):
        candidates = [
            self.make_candidate(
                "candidate-conflict",
                "AKS",
                {
                    "headword": "이순신",
                    "primary_type_part": "사건",
                },
                compatibility="CONFLICT",
            ),
            self.make_candidate(
                "candidate-description",
                "THESAURUS",
                {
                    "term_name": "다른 표제어",
                    "description": "이순신을 설명에서 언급",
                    "thesaurus_category": "인명",
                },
                retrieval_method="description_ngram",
            ),
        ]
        tables = self.build_tables(candidates)

        features = tables["source_candidate_features"].set_index(
            "source_candidate_id"
        )
        members = tables["canonical_cluster_members"]
        self.assertEqual(
            features.loc["candidate-conflict", "proposed_role"],
            "REJECTED",
        )
        self.assertEqual(
            features.loc["candidate-description", "proposed_role"],
            "AMBIGUOUS",
        )
        self.assertNotIn(
            "candidate-conflict",
            set(members["source_candidate_id"]),
        )

    def test_non_specific_era_value_is_not_a_merge_signal(self):
        candidates = [
            self.make_candidate(
                "candidate-aks",
                "AKS",
                {
                    "headword": "동명이인",
                    "era": "통시대",
                    "primary_type_part": "인물",
                },
            ),
            self.make_candidate(
                "candidate-thesaurus",
                "THESAURUS",
                {
                    "term_name": "동명이인",
                    "era": "통시대",
                    "thesaurus_category": "인명",
                },
            ),
        ]

        tables = self.build_tables(candidates)

        features = tables["source_candidate_features"]
        self.assertTrue(
            all(json.loads(value) == [] for value in features["era_values_json"])
        )
        pair = tables["source_candidate_pair_signals"].iloc[0]
        signals = set(json.loads(pair["signal_dimensions_json"]))
        self.assertNotIn("era_overlap", signals)
        self.assertFalse(bool(pair["merge_eligible"]))

    def test_canonical_alternative_ids_are_stable(self):
        candidates = [
            self.make_candidate(
                "candidate-aks",
                "AKS",
                {
                    "headword": "이순신",
                    "aliases": ["李舜臣"],
                    "era": "조선",
                    "primary_type_part": "인물",
                },
            ),
            self.make_candidate(
                "candidate-thesaurus",
                "THESAURUS",
                {
                    "term_name": "이순신",
                    "hanja": "李舜臣",
                    "era": "조선",
                    "thesaurus_category": "인명",
                },
            ),
        ]
        first = self.build_tables(candidates)["canonical_alternative_clusters"]
        second = self.build_tables(list(reversed(candidates)))[
            "canonical_alternative_clusters"
        ]
        self.assertEqual(
            sorted(first["canonical_alternative_id"]),
            sorted(second["canonical_alternative_id"]),
        )


if __name__ == "__main__":
    unittest.main()

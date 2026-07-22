import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


class ResolutionPackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_root = Path(__file__).resolve().parents[3]
        neo4j_root = project_root / "etl" / "preprocessing" / "neo4j"
        sys.path.insert(0, str(neo4j_root))
        sys.path.insert(0, str(neo4j_root / "terms"))

        from common import load_pipeline_policy
        from entity_resolution.build_resolution_package import (
            build_resolution_tables,
            validate_resolution_tables,
            write_resolution_package,
        )

        cls.build_resolution_tables = staticmethod(build_resolution_tables)
        cls.validate_resolution_tables = staticmethod(validate_resolution_tables)
        cls.write_resolution_package = staticmethod(write_resolution_package)
        cls.policy = load_pipeline_policy(
            str(neo4j_root / "config" / "resolution_policy.json")
        )

    def make_candidate(self, source_record_id: str, source_id: str) -> dict:
        return {
            "source": "AKS",
            "source_id": source_id,
            "source_release": "sha256-test",
            "source_record_id": source_record_id,
            "eid": source_id,
            "headword": "고종",
            "primary_type_part": "인물",
            "matched_name": "고종",
            "matched_field": "name",
            "retrieval_method": "exact",
            "retrieval_methods": ["exact"],
            "retrieval_score": 1.0,
            "score_components": {},
            "verification_status": "PROPOSED",
            "category_mismatch": False,
        }

    def build_fixture_tables(self) -> dict[str, pd.DataFrame]:
        first_candidate = self.make_candidate(
            "AKS:ARTICLE:E1:sha256-test",
            "E1",
        )
        second_candidate = self.make_candidate(
            "AKS:ARTICLE:E2:sha256-test",
            "E2",
        )
        match_results = [
            {
                "canonical_term": "고종",
                "category": "인물",
                "problem_ids": ["question-1", "question-2"],
                "is_noise": False,
                "encyclopedia": [first_candidate, second_candidate],
                "thesaurus": [],
                "itkc_people": [],
                "itkc_events": [],
                "extraction_model": "test-model",
                "extraction_policy_version": "test-extraction",
            },
            {
                "canonical_term": "미해소 용어",
                "category": "사건",
                "problem_ids": ["question-3"],
                "is_noise": False,
                "encyclopedia": [],
                "thesaurus": [],
                "itkc_people": [],
                "itkc_events": [],
            },
            {
                "canonical_term": "왕",
                "category": "인물",
                "problem_ids": ["question-4"],
                "is_noise": True,
                "encyclopedia": [],
                "thesaurus": [],
                "itkc_people": [],
                "itkc_events": [],
            },
            {
                "canonical_term": "삼백 산업",
                "category": "산업",
                "problem_ids": ["question-5"],
                "is_noise": False,
                "encyclopedia": [],
                "thesaurus": [],
                "itkc_people": [],
                "itkc_events": [],
            },
        ]
        definition_results = [
            {
                "canonical_term": "고종",
                "category": "인물",
                "candidates": [first_candidate],
            }
        ]
        problem_context_df = pd.DataFrame(
            [
                {"problem_id": "question-1", "full_text": "조선 고종 문항"},
                {"problem_id": "question-2", "full_text": "고려 고종 문항"},
                {"problem_id": "question-3", "full_text": "미해소 문항"},
                {"problem_id": "question-4", "full_text": "노이즈 문항"},
                {"problem_id": "question-5", "full_text": "비허용 category 문항"},
            ]
        )
        return self.build_resolution_tables(
            match_results,
            definition_results,
            problem_context_df,
            self.policy,
        )

    def test_ambiguous_unresolved_and_noise_statuses_are_separated(self):
        tables = self.build_fixture_tables()
        cases = tables["resolution_cases"].set_index("canonical_term")
        self.assertEqual(cases.loc["고종", "link_status"], "AMBIGUOUS")
        self.assertEqual(cases.loc["미해소 용어", "link_status"], "UNRESOLVED")
        self.assertEqual(cases.loc["왕", "link_status"], "REJECTED")
        self.assertEqual(cases.loc["삼백 산업", "link_status"], "REJECTED")
        self.assertEqual(
            cases.loc["삼백 산업", "review_reason"],
            "INVALID_EXTRACTION_CATEGORY",
        )
        self.assertEqual(cases.loc["삼백 산업", "entity_type_proposal"], "")
        self.assertEqual(cases.loc["고종", "entity_type_proposal"], "Person")

    def test_problem_assignments_preserve_homonym_contexts(self):
        tables = self.build_fixture_tables()
        assignments = tables["problem_resolution_assignments"]
        gojong = assignments[assignments["canonical_term"] == "고종"]
        self.assertEqual(set(gojong["problem_id"]), {"question-1", "question-2"})
        self.assertTrue(all(value == "AMBIGUOUS" for value in gojong["assignment_status"]))
        self.assertTrue(all(value == "" for value in gojong["canonical_id"]))
        alternative_ids = json.loads(
            gojong.iloc[0]["canonical_alternative_ids_json"]
        )
        self.assertEqual(len(alternative_ids), 2)
        self.assertEqual(
            gojong.iloc[0]["selected_canonical_alternative_id"],
            "",
        )

    def test_duplicate_source_candidate_merges_retrieval_channels(self):
        tables = self.build_fixture_tables()
        candidates = tables["source_record_candidates"]
        first = candidates[
            candidates["source_record_id"] == "AKS:ARTICLE:E1:sha256-test"
        ]
        self.assertEqual(len(first), 1)
        channels = json.loads(first.iloc[0]["retrieval_channels_json"])
        self.assertEqual(set(channels), {"aks_name", "aks_definition"})

    def test_body_mention_channel_is_preserved_with_same_source_record(self):
        candidate = self.make_candidate(
            "AKS:ARTICLE:E1:sha256-test",
            "E1",
        )
        match_results = [
            {
                "canonical_term": "진묘수",
                "category": "유물",
                "problem_ids": ["question-1"],
                "is_noise": False,
                "encyclopedia": [candidate],
                "thesaurus": [],
                "itkc_people": [],
                "itkc_events": [],
            }
        ]
        body_candidate = dict(candidate)
        body_candidate["retrieval_method"] = "body_mention"
        body_candidate["retrieval_methods"] = ["body_mention"]
        body_candidate["retrieval_score"] = 1.0
        body_results = [
            {
                "canonical_term": "진묘수",
                "category": "유물",
                "candidates": [body_candidate],
            }
        ]
        contexts = pd.DataFrame(
            [{"problem_id": "question-1", "full_text": "진묘수 문항"}]
        )

        tables = self.build_resolution_tables(
            match_results,
            [],
            contexts,
            self.policy,
            body_mention_results=body_results,
        )

        row = tables["source_record_candidates"].iloc[0]
        channels = set(json.loads(row["retrieval_channels_json"]))
        self.assertEqual(channels, {"aks_name", "aks_body_mention"})

    def test_noise_is_not_added_to_review_queue(self):
        tables = self.build_fixture_tables()
        review_queue = tables["review_queue"]
        self.assertNotIn("왕", set(review_queue["canonical_term"]))
        self.assertIn("삼백 산업", set(review_queue["canonical_term"]))
        self.assertEqual(len(review_queue), 4)

    def test_staging_ids_are_stable_and_csv_files_are_written(self):
        first_tables = self.build_fixture_tables()
        second_tables = self.build_fixture_tables()
        self.assertEqual(
            list(first_tables["resolution_cases"]["resolution_case_id"]),
            list(second_tables["resolution_cases"]["resolution_case_id"]),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            written = self.write_resolution_package(
                first_tables,
                temporary_directory,
                self.policy,
            )
            self.assertEqual(set(written), set(first_tables))
            self.assertTrue(all(Path(path).is_file() for path in written.values()))

    def test_invalid_foreign_key_is_rejected(self):
        tables = self.build_fixture_tables()
        tables["source_record_candidates"].loc[0, "resolution_case_id"] = "missing-case"
        with self.assertRaises(ValueError):
            self.validate_resolution_tables(tables, self.policy)

    def test_normalized_variants_merge_into_one_case(self):
        match_results = [
            {
                "canonical_term": "9서당 10정",
                "category": "기관",
                "problem_count": 2,
                "problem_ids": ["question-1", "question-2"],
                "is_noise": False,
            },
            {
                "canonical_term": "구서당 십정",
                "category": "기관",
                "problem_count": 1,
                "problem_ids": ["question-3"],
                "is_noise": False,
            },
        ]
        contexts = pd.DataFrame(
            [
                {"problem_id": "question-1", "full_text": "문항 1"},
                {"problem_id": "question-2", "full_text": "문항 2"},
                {"problem_id": "question-3", "full_text": "문항 3"},
            ]
        )
        tables = self.build_resolution_tables(
            match_results,
            [],
            contexts,
            self.policy,
        )
        self.assertEqual(len(tables["resolution_cases"]), 1)
        case = tables["resolution_cases"].iloc[0]
        self.assertEqual(case["problem_count"], 3)
        self.assertEqual(
            set(json.loads(case["term_variants_json"])),
            {"9서당 10정", "구서당 십정"},
        )


if __name__ == "__main__":
    unittest.main()

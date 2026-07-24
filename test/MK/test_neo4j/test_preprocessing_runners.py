import sys
import unittest
from pathlib import Path


class PreprocessingRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[3]
        cls.neo4j_root = (
            cls.project_root / "etl" / "preprocessing" / "neo4j"
        )
        sys.path.insert(0, str(cls.neo4j_root))

        from common import load_pipeline_policy
        from run_neo4j_preprocessing import (
            resolve_pipeline_paths,
            resolve_stage_output_paths,
        )
        from run_neo4j_preprocessing_test import (
            resolve_test_output_directory,
        )

        cls.load_pipeline_policy = staticmethod(load_pipeline_policy)
        cls.resolve_pipeline_paths = staticmethod(resolve_pipeline_paths)
        cls.resolve_stage_output_paths = staticmethod(
            resolve_stage_output_paths
        )
        cls.resolve_test_output_directory = staticmethod(
            resolve_test_output_directory
        )

    def test_explicit_pipeline_paths_are_preserved(self):
        paths = self.resolve_pipeline_paths(
            exam_json_path="exam.json",
            thesaurus_csv_path="thesaurus.csv",
            output_dir="output-dir",
            encyclopedia_jsonl_path="aks.jsonl",
            itkc_people_csv_path="people.csv",
            itkc_events_csv_path="events.csv",
        )

        self.assertEqual(paths["exam_json_path"], "exam.json")
        self.assertEqual(paths["thesaurus_csv_path"], "thesaurus.csv")
        self.assertEqual(paths["output_dir"], "output-dir")
        self.assertEqual(paths["encyclopedia_jsonl_path"], "aks.jsonl")
        self.assertEqual(paths["itkc_people_csv_path"], "people.csv")
        self.assertEqual(paths["itkc_events_csv_path"], "events.csv")

    def test_test_output_is_nested_under_pipeline_output(self):
        output_directory = Path(
            self.resolve_test_output_directory(
                self.neo4j_root,
                "test_run",
            )
        )

        self.assertEqual(
            output_directory,
            (self.neo4j_root / "output" / "test_run").resolve(),
        )

    def test_test_output_rejects_production_root_and_parent_escape(self):
        with self.assertRaises(ValueError):
            self.resolve_test_output_directory(self.neo4j_root, ".")
        with self.assertRaises(ValueError):
            self.resolve_test_output_directory(self.neo4j_root, "../outside")

    def test_policy_has_bounded_test_run_defaults(self):
        policy = self.load_pipeline_policy(
            str(self.neo4j_root / "config" / "resolution_policy.json")
        )
        test_policy = policy["test_run"]

        self.assertGreater(int(test_policy["limit"]), 0)
        self.assertGreater(int(test_policy["batch_size"]), 0)
        self.assertEqual(test_policy["output_subdirectory"], "test_run")

    def test_stage_output_paths_follow_business_process_order(self):
        policy = self.load_pipeline_policy(
            str(self.neo4j_root / "config" / "resolution_policy.json")
        )
        paths = self.resolve_stage_output_paths(
            "pipeline-output",
            policy,
        )

        self.assertEqual(
            paths["extracted_terms_csv"],
            Path("pipeline-output/01_term_extraction/unique_exam_terms.csv"),
        )
        self.assertEqual(
            paths["term_checkpoint"],
            Path(
                "pipeline-output/01_term_extraction/internal/"
                "term_extraction_checkpoint.jsonl"
            ),
        )
        self.assertEqual(
            paths["coverage_report"],
            Path(
                "pipeline-output/02_candidate_retrieval/"
                "source_coverage_report.json"
            ),
        )
        self.assertEqual(
            paths["name_matches"],
            Path(
                "pipeline-output/02_candidate_retrieval/internal/"
                "name_match_candidates.json"
            ),
        )
        self.assertEqual(
            paths["entity_resolution_directory"],
            Path("pipeline-output/03_entity_resolution"),
        )
        self.assertEqual(
            paths["term_review_tasks"],
            Path(
                "pipeline-output/04_llm_review/internal/"
                "term_identity_review_tasks.jsonl"
            ),
        )


if __name__ == "__main__":
    unittest.main()

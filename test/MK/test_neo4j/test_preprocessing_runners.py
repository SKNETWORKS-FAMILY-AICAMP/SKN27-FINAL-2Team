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
        from entity_resolution.goldset_workflow import (
            resolve_goldset_workflow_paths,
        )
        from run_preprocessing_test import (
            resolve_shared_thesaurus_path,
            resolve_test_output_directory,
        )

        cls.load_pipeline_policy = staticmethod(load_pipeline_policy)
        cls.resolve_pipeline_paths = staticmethod(resolve_pipeline_paths)
        cls.resolve_stage_output_paths = staticmethod(
            resolve_stage_output_paths
        )
        cls.resolve_goldset_workflow_paths = staticmethod(
            resolve_goldset_workflow_paths
        )
        cls.resolve_test_output_directory = staticmethod(
            resolve_test_output_directory
        )
        cls.resolve_shared_thesaurus_path = staticmethod(
            resolve_shared_thesaurus_path
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
            Path("pipeline-output/review/unique_exam_terms.csv"),
        )
        self.assertEqual(
            paths["term_checkpoint"],
            Path(
                "pipeline-output/internal/term_extraction/"
                "term_extraction_checkpoint.jsonl"
            ),
        )
        self.assertEqual(
            paths["normalized_thesaurus"],
            Path(
                "pipeline-output/internal/shared/"
                "normalized_history_thesaurus.json"
            ),
        )
        self.assertEqual(
            paths["coverage_report"],
            Path(
                "pipeline-output/review/"
                "source_coverage_report.json"
            ),
        )
        self.assertEqual(
            paths["name_matches"],
            Path(
                "pipeline-output/internal/candidate_retrieval/"
                "name_match_candidates.json"
            ),
        )
        self.assertEqual(
            paths["body_mention_matches"],
            Path(
                "pipeline-output/internal/candidate_retrieval/"
                "body_mention_candidates.json"
            ),
        )
        self.assertEqual(
            paths["entity_resolution_directory"],
            Path("pipeline-output/internal/entity_resolution"),
        )
        self.assertEqual(
            paths["entity_resolution_review_queue"],
            Path("pipeline-output/review/cases_requiring_review.csv"),
        )
        self.assertEqual(
            paths["term_review_tasks"],
            Path(
                "pipeline-output/internal/model_review/"
                "term_identity_review_tasks.jsonl"
            ),
        )

    def test_test_run_uses_the_shared_normalized_thesaurus(self):
        policy = self.load_pipeline_policy(
            str(self.neo4j_root / "config" / "resolution_policy.json")
        )

        shared_path = self.resolve_shared_thesaurus_path(
            self.neo4j_root,
            policy,
        )

        self.assertEqual(
            Path(shared_path),
            (
                self.neo4j_root
                / "output"
                / "internal"
                / "shared"
                / "normalized_history_thesaurus.json"
            ).resolve(),
        )
        self.assertFalse(
            policy["output_retention"][
                "keep_candidate_retrieval_cache_after_resolution"
            ]
        )

    def test_goldset_workflow_paths_stay_under_goldset_internal(self):
        policy = self.load_pipeline_policy(
            str(self.neo4j_root / "config" / "resolution_policy.json")
        )
        paths = self.resolve_goldset_workflow_paths(
            self.neo4j_root,
            policy,
        )

        self.assertEqual(
            paths["annotation_directory"],
            (self.neo4j_root / "goldset" / "human_review_csv").resolve(),
        )
        self.assertEqual(
            paths["related_model_prediction_directory"],
            (
                self.neo4j_root
                / "goldset"
                / "internal"
                / "related_entity"
            ).resolve(),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from etl.preprocessing.neo4j.run_fact_graph_load_pipeline import (
    run_fact_graph_load_pipeline,
)


class FactGraphLoadPipelineTest(unittest.TestCase):
    def test_rejects_non_positive_batch_size(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            config_path = temporary_path / "config.json"
            config_path.write_text(
                json.dumps({"output_directory": "release"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "batch-size must be greater than zero",
            ):
                run_fact_graph_load_pipeline(
                    project_root=temporary_path,
                    config_path=config_path,
                    output_root=temporary_path,
                    release_output=None,
                    batch_size=0,
                    replace=False,
                    load_only=False,
                )

    def test_rejects_missing_release_config(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)

            with self.assertRaisesRegex(
                FileNotFoundError,
                "Release config not found",
            ):
                run_fact_graph_load_pipeline(
                    project_root=temporary_path,
                    config_path=temporary_path / "missing.json",
                    output_root=temporary_path,
                    release_output=None,
                    batch_size=100,
                    replace=False,
                    load_only=False,
                )

    def test_builds_loads_and_validates_release(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            config_path = temporary_path / "config.json"
            config_path.write_text(
                json.dumps({"output_directory": "release"}),
                encoding="utf-8",
            )
            package_directory = temporary_path / "release"
            package_directory.mkdir()
            release_manifest = {
                "graph_release_id": "release-1",
                "statistics": {"fact_count": 3},
                "output_paths": {},
            }
            load_manifest = {
                "status": "COMPLETED",
                "graph_release_id": "release-1",
                "verification": {"status": "PASSED"},
            }

            with patch(
                "etl.preprocessing.neo4j.fact_retrieval."
                "fact_graph_release.build_fact_graph_release",
                return_value={"facts": "package"},
            ) as build_release, patch(
                "etl.preprocessing.neo4j.fact_retrieval."
                "fact_graph_release.write_fact_graph_release",
                return_value=release_manifest,
            ) as write_release, patch(
                "storage.fact_neo4j.load_fact_graph.load_fact_graph",
                return_value=load_manifest,
            ) as load_release:
                result = run_fact_graph_load_pipeline(
                    project_root=temporary_path,
                    config_path=config_path,
                    output_root=temporary_path,
                    release_output=None,
                    batch_size=500,
                    replace=True,
                    load_only=False,
                )

            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(result["graph_release_id"], "release-1")
            self.assertTrue(result["replace_existing_release"])
            self.assertEqual(
                result["release_package_mode"],
                "BUILD_AND_LOAD",
            )
            self.assertTrue(
                (package_directory / "pipeline_manifest.json").is_file()
            )
            build_release.assert_called_once()
            write_release.assert_called_once()
            load_release.assert_called_once_with(
                project_root=temporary_path,
                package_directory=package_directory,
                config_path=config_path,
                batch_size=500,
                replace=True,
            )

    def test_load_only_uses_existing_complete_package(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            config_path = temporary_path / "config.json"
            config_path.write_text(
                json.dumps({"output_directory": "release"}),
                encoding="utf-8",
            )
            package_directory = temporary_path / "release"
            package_directory.mkdir()
            (package_directory / "facts.csv").write_text(
                "fact_id\nF1\n",
                encoding="utf-8",
            )
            release_manifest = {
                "graph_release_id": "release-2",
                "statistics": {"fact_count": 1},
                "output_paths": {"facts": "facts.csv"},
            }
            (package_directory / "manifest.json").write_text(
                json.dumps(release_manifest),
                encoding="utf-8",
            )
            load_manifest = {
                "status": "COMPLETED",
                "graph_release_id": "release-2",
                "verification": {"status": "PASSED"},
            }

            with patch(
                "etl.preprocessing.neo4j.fact_retrieval."
                "fact_graph_release.build_fact_graph_release",
            ) as build_release, patch(
                "etl.preprocessing.neo4j.fact_retrieval."
                "fact_graph_release.write_fact_graph_release",
            ) as write_release, patch(
                "storage.fact_neo4j.load_fact_graph.load_fact_graph",
                return_value=load_manifest,
            ):
                result = run_fact_graph_load_pipeline(
                    project_root=temporary_path,
                    config_path=config_path,
                    output_root=temporary_path,
                    release_output=package_directory,
                    batch_size=100,
                    replace=False,
                    load_only=True,
                )

            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(result["release_package_mode"], "LOAD_ONLY")
            build_release.assert_not_called()
            write_release.assert_not_called()

    def test_fails_when_loaded_release_id_does_not_match(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            config_path = temporary_path / "config.json"
            config_path.write_text(
                json.dumps({"output_directory": "release"}),
                encoding="utf-8",
            )
            package_directory = temporary_path / "release"
            package_directory.mkdir()
            release_manifest = {
                "graph_release_id": "release-3",
                "statistics": {},
                "output_paths": {},
            }
            load_manifest = {
                "status": "COMPLETED",
                "graph_release_id": "different-release",
                "verification": {"status": "PASSED"},
            }

            with patch(
                "etl.preprocessing.neo4j.fact_retrieval."
                "fact_graph_release.build_fact_graph_release",
                return_value={},
            ), patch(
                "etl.preprocessing.neo4j.fact_retrieval."
                "fact_graph_release.write_fact_graph_release",
                return_value=release_manifest,
            ), patch(
                "storage.fact_neo4j.load_fact_graph.load_fact_graph",
                return_value=load_manifest,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "validation failed",
                ):
                    run_fact_graph_load_pipeline(
                        project_root=temporary_path,
                        config_path=config_path,
                        output_root=temporary_path,
                        release_output=None,
                        batch_size=100,
                        replace=False,
                        load_only=False,
                    )

            pipeline_manifest = json.loads(
                (package_directory / "pipeline_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                pipeline_manifest["status"],
                "FAILED_VALIDATION",
            )


if __name__ == "__main__":
    unittest.main()

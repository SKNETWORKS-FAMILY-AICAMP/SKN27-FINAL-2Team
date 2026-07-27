from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_arguments(neo4j_root: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "사실 그래프 release를 생성하고 별도 Neo4j에 적재한 뒤 "
            "무결성을 검증합니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(neo4j_root / "config" / "fact_graph_release.json"),
    )
    parser.add_argument(
        "--output-root",
        default=str(neo4j_root / "output"),
    )
    parser.add_argument(
        "--release-output",
        default="",
        help="release CSV와 manifest를 저장할 경로입니다.",
    )
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="기존 fact graph release를 안전검사 후 교체합니다.",
    )
    parser.add_argument(
        "--load-only",
        action="store_true",
        help="저장소에 포함된 최종 release 패키지를 다시 생성하지 않고 적재합니다.",
    )
    return parser.parse_args()


def run_fact_graph_load_pipeline(
    project_root: Path,
    config_path: Path,
    output_root: Path,
    release_output: Path | None,
    batch_size: int,
    replace: bool,
    load_only: bool,
) -> dict[str, Any]:
    if batch_size <= 0:
        raise ValueError("batch-size must be greater than zero")
    if not config_path.is_file():
        raise FileNotFoundError(f"Release config not found: {config_path}")

    from etl.preprocessing.neo4j.fact_retrieval.fact_graph_release import (
        build_fact_graph_release,
        read_json,
        write_fact_graph_release,
    )
    from storage.fact_neo4j.load_fact_graph import load_fact_graph

    config = read_json(config_path)
    package_directory = release_output
    if package_directory is None:
        package_directory = output_root / str(config["output_directory"])

    release_manifest: dict[str, Any] = {}
    if load_only:
        manifest_path = package_directory / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"Final release manifest not found: {manifest_path}"
            )
        with manifest_path.open("r", encoding="utf-8") as input_file:
            release_manifest = json.load(input_file)
        missing_package_paths = [
            str(package_directory / relative_path)
            for relative_path in release_manifest["output_paths"].values()
            if not (package_directory / relative_path).is_file()
        ]
        if missing_package_paths:
            raise FileNotFoundError(
                "Final release package is incomplete: "
                + ", ".join(missing_package_paths)
            )
        print("[1/3] 저장소의 최종 fact graph release 확인")
    elif not load_only:
        print("[1/3] fact graph release 생성")
        package = build_fact_graph_release(output_root, config)
        release_manifest = write_fact_graph_release(
            package,
            package_directory,
            config,
        )

    print("[2/3] 별도 Neo4j 적재")
    load_manifest = load_fact_graph(
        project_root=project_root,
        package_directory=package_directory,
        config_path=config_path,
        batch_size=batch_size,
        replace=replace,
    )

    print("[3/3] release와 적재 결과 교차 검증")
    release_id_matches = (
        release_manifest["graph_release_id"]
        == load_manifest["graph_release_id"]
    )
    load_passed = (
        load_manifest["status"] == "COMPLETED"
        and load_manifest["verification"]["status"] == "PASSED"
    )
    status = "COMPLETED"
    if not release_id_matches or not load_passed:
        status = "FAILED_VALIDATION"

    pipeline_manifest = {
        "status": status,
        "stage": "FACT_GRAPH_LOAD_PIPELINE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "graph_release_id": release_manifest["graph_release_id"],
        "replace_existing_release": replace,
        "release_package_mode": (
            "LOAD_ONLY" if load_only else "BUILD_AND_LOAD"
        ),
        "release_statistics": release_manifest["statistics"],
        "load_verification": load_manifest["verification"],
        "release_manifest_path": str(
            (package_directory / "manifest.json").resolve()
        ),
        "load_manifest_path": str(
            (package_directory / "neo4j_load_manifest.json").resolve()
        ),
    }
    pipeline_manifest_path = package_directory / "pipeline_manifest.json"
    with pipeline_manifest_path.open("w", encoding="utf-8") as output_file:
        json.dump(
            pipeline_manifest,
            output_file,
            ensure_ascii=False,
            indent=2,
        )
    pipeline_manifest["pipeline_manifest_path"] = str(
        pipeline_manifest_path.resolve()
    )
    if status != "COMPLETED":
        raise RuntimeError("Fact graph load pipeline validation failed")
    return pipeline_manifest


def main() -> None:
    neo4j_root = Path(__file__).resolve().parent
    project_root = neo4j_root.parents[2]
    sys.path.insert(0, str(project_root))
    args = parse_arguments(neo4j_root)
    release_output = None
    if args.release_output:
        release_output = Path(args.release_output)
    manifest = run_fact_graph_load_pipeline(
        project_root=project_root,
        config_path=Path(args.config),
        output_root=Path(args.output_root),
        release_output=release_output,
        batch_size=args.batch_size,
        replace=args.replace,
        load_only=args.load_only,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from choice_relation.relation_frames import (
    build_exam_relation_frame_tables,
    load_exam_relation_frame_policy,
)


def parse_arguments() -> Namespace:
    """기출 원자 관계 프레임 CLI 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent
    parser = ArgumentParser(
        description=(
            "AKS 공식 원문이 지지한 기출 선지를 원자 행동과 "
            "canonical 역할 프레임으로 분해합니다. 새 사실 생성, "
            "LLM 호출, Neo4j 적재는 하지 않습니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(
            neo4j_root / "config" / "exam_relation_candidates.json"
        ),
    )
    parser.add_argument(
        "--relation-candidates",
        default=str(
            neo4j_root
            / "output"
            / "exam_relation_candidates"
            / "exam_relation_candidates.csv"
        ),
    )
    parser.add_argument(
        "--text-checks",
        default=str(
            neo4j_root
            / "output"
            / "exam_relation_official_text_corroboration"
            / "exam_relation_official_text_checks.csv"
        ),
    )
    parser.add_argument(
        "--text-evidence",
        default=str(
            neo4j_root
            / "output"
            / "exam_relation_official_text_corroboration"
            / "exam_relation_official_text_evidence.csv"
        ),
    )
    parser.add_argument(
        "--canonical-registry",
        default=str(
            neo4j_root
            / "output"
            / "final_identity"
            / "canonical_entity_registry.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            neo4j_root
            / "output"
            / "exam_relation_frames"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_exam_relation_frame_preprocessing(
    cli_args: Namespace,
) -> dict[str, object]:
    """원자 관계 프레임과 참여자 역할 CSV를 생성한다."""
    policy = load_exam_relation_frame_policy(cli_args.config)
    input_paths = {
        "relation_candidates": Path(cli_args.relation_candidates),
        "text_checks": Path(cli_args.text_checks),
        "text_evidence": Path(cli_args.text_evidence),
        "canonical_registry": Path(cli_args.canonical_registry),
    }
    missing_inputs = [
        str(path) for path in input_paths.values() if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "기출 관계 프레임 입력 파일이 없습니다: "
            + ", ".join(missing_inputs)
        )
    output_directory = Path(cli_args.output_dir)
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "EXAM_RELATION_FRAME_PREPROCESSING",
            "dry_run": True,
            "llm_used": False,
            "neo4j_load": False,
            "creates_new_fact": False,
            "input_paths": {
                name: str(path) for name, path in input_paths.items()
            },
            "output_directory": str(output_directory),
        }
    relation_candidates = pd.read_csv(
        input_paths["relation_candidates"],
        dtype=str,
    ).fillna("")
    text_checks = pd.read_csv(
        input_paths["text_checks"],
        dtype=str,
    ).fillna("")
    text_evidence = pd.read_csv(
        input_paths["text_evidence"],
        dtype=str,
    ).fillna("")
    canonical_registry = pd.read_csv(
        input_paths["canonical_registry"],
        dtype=str,
    ).fillna("")
    tables, statistics = build_exam_relation_frame_tables(
        relation_candidates,
        text_checks,
        text_evidence,
        canonical_registry,
        policy,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    frame_policy = policy["exam_relation_frames"]
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = (
            output_directory / frame_policy["outputs"][table_name]
        )
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path)
    manifest = {
        "status": "COMPLETED",
        "stage": "EXAM_RELATION_FRAME_PREPROCESSING",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": frame_policy["policy_version"],
        "llm_used": False,
        "neo4j_load": False,
        "creates_new_fact": False,
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory / frame_policy["outputs"]["manifest"]
    )
    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """프레임 전처리 실행 결과를 JSON으로 출력한다."""
    result = run_exam_relation_frame_preprocessing(
        parse_arguments()
    )
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

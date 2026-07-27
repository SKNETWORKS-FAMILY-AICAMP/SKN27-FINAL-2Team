from __future__ import annotations

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from choice_relation.official_text_corroboration import (
    build_exam_relation_official_text_tables,
    load_exam_relation_official_text_policy,
)


def parse_arguments() -> Namespace:
    """AKS 원문 기반 기출 관계 검증 CLI 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent
    project_root = neo4j_root.parents[2]
    parser = ArgumentParser(
        description=(
            "기존 공식 사실표에서 놓친 기출 관계 후보를 AKS 원문과 "
            "교차 확인합니다. 새 사실 생성, LLM 호출, Neo4j 적재는 "
            "하지 않습니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(
            neo4j_root / "config" / "exam_relation_candidates.json"
        ),
    )
    parser.add_argument(
        "--official-checks",
        default=str(
            neo4j_root
            / "output"
            / "exam_relation_official_corroboration"
            / "exam_relation_official_checks.csv"
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
        "--aks-details",
        default=str(
            project_root
            / "etl"
            / "raw_data"
            / "한국민족문화대백과사전"
            / "articles_detail.jsonl"
        ),
    )
    parser.add_argument(
        "--aks-list",
        default=str(
            project_root
            / "etl"
            / "raw_data"
            / "한국민족문화대백과사전"
            / "articles_list.jsonl"
        ),
    )
    parser.add_argument(
        "--source-records",
        default=str(
            neo4j_root
            / "output"
            / "source_relationships"
            / "source_record_nodes.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            neo4j_root
            / "output"
            / "exam_relation_official_text_corroboration"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_exam_relation_official_text_corroboration(
    cli_args: Namespace,
) -> dict[str, object]:
    """AKS 원문 교차 검증 결과와 근거 CSV를 생성한다."""
    policy = load_exam_relation_official_text_policy(
        cli_args.config
    )
    input_paths = {
        "official_checks": Path(cli_args.official_checks),
        "canonical_registry": Path(cli_args.canonical_registry),
        "aks_details": Path(cli_args.aks_details),
        "aks_list": Path(cli_args.aks_list),
        "source_records": Path(cli_args.source_records),
    }
    missing_inputs = [
        str(path) for path in input_paths.values() if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "AKS 원문 검증 입력 파일이 없습니다: "
            + ", ".join(missing_inputs)
        )
    output_directory = Path(cli_args.output_dir)
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "EXAM_RELATION_OFFICIAL_TEXT_CORROBORATION",
            "dry_run": True,
            "llm_used": False,
            "neo4j_load": False,
            "creates_new_fact": False,
            "input_paths": {
                name: str(path) for name, path in input_paths.items()
            },
            "output_directory": str(output_directory),
        }

    official_checks = pd.read_csv(
        input_paths["official_checks"],
        dtype=str,
    ).fillna("")
    canonical_registry = pd.read_csv(
        input_paths["canonical_registry"],
        dtype=str,
    ).fillna("")
    source_records = pd.read_csv(
        input_paths["source_records"],
        dtype=str,
    ).fillna("")
    tables, statistics = build_exam_relation_official_text_tables(
        official_checks,
        canonical_registry,
        str(input_paths["aks_details"]),
        policy,
        source_records,
        str(input_paths["aks_list"]),
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    text_policy = policy[
        "exam_relation_official_text_corroboration"
    ]
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = (
            output_directory / text_policy["outputs"][table_name]
        )
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path)

    manifest = {
        "status": "COMPLETED",
        "stage": "EXAM_RELATION_OFFICIAL_TEXT_CORROBORATION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": text_policy["policy_version"],
        "llm_used": False,
        "neo4j_load": False,
        "creates_new_fact": False,
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory / text_policy["outputs"]["manifest"]
    )
    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """AKS 원문 검증 실행 결과를 JSON으로 출력한다."""
    result = run_exam_relation_official_text_corroboration(
        parse_arguments()
    )
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import _bootstrap

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path
from typing import Iterator

import pandas as pd

from choice_relation.exam_term_raw_relations import (
    build_raw_relation_eda_tables,
    infer_aks_source_release,
    iter_dataset_documents,
    load_exam_term_raw_relation_policy,
)


def parse_arguments() -> Namespace:
    """기출 용어 중심 원문 관계 EDA 실행 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent.parent
    project_root = neo4j_root.parents[2]
    parser = ArgumentParser(
        description=(
            "공식 raw_data를 한 번 순회해 기출 용어와 같은 절에 "
            "명시된 관계·비기출 endpoint 후보를 추출합니다. "
            "LLM 호출과 Neo4j 적재는 하지 않습니다."
        )
    )
    parser.add_argument(
        "--eda-config",
        default=str(
            neo4j_root
            / "config"
            / "exam_term_raw_relation_eda.json"
        ),
    )
    parser.add_argument(
        "--relation-config",
        default=str(
            neo4j_root
            / "config"
            / "exam_relation_candidates.json"
        ),
    )
    parser.add_argument(
        "--resolution-config",
        default=str(
            neo4j_root / "config" / "entity_resolution.json"
        ),
    )
    parser.add_argument(
        "--source-first-config",
        default=str(
            neo4j_root
            / "config"
            / "source_first_fact_eda.json"
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
        "--exam-term-matches",
        default=str(
            neo4j_root
            / "output"
            / "exam_match_recovery"
            / "exam_term_match_recovery.csv"
        ),
    )
    parser.add_argument(
        "--source-nodes",
        default=str(
            neo4j_root
            / "output"
            / "source_relationships"
            / "source_record_nodes.csv"
        ),
    )
    parser.add_argument(
        "--source-resolutions",
        default=str(
            neo4j_root
            / "output"
            / "final_identity"
            / "neo4j_source_to_entity_relationships.csv"
        ),
    )
    parser.add_argument(
        "--aks-articles-list",
        default=str(
            project_root
            / "etl"
            / "raw_data"
            / "한국민족문화대백과사전"
            / "articles_list.jsonl"
        ),
    )
    parser.add_argument(
        "--raw-data-root",
        default=str(project_root / "etl" / "raw_data"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            neo4j_root
            / "output"
            / "exam_term_raw_relation_eda"
        ),
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help=(
            "설정의 dataset name을 반복 지정합니다. "
            "생략하면 enabled dataset을 모두 실행합니다."
        ),
    )
    parser.add_argument(
        "--document-limit-per-dataset",
        type=int,
        default=None,
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def select_dataset_policies(
    policy: dict,
    selected_names: list[str],
) -> list[dict]:
    """활성화된 원천 중 요청한 dataset만 선택한다."""
    configured = {
        str(dataset["name"]): dataset
        for dataset in policy["exam_term_raw_relation_eda"][
            "datasets"
        ]
    }
    unknown_names = set(selected_names).difference(configured)
    if unknown_names:
        raise ValueError(
            "설정에 없는 dataset입니다: "
            + ", ".join(sorted(unknown_names))
        )
    selected: list[dict] = []
    for dataset in configured.values():
        if not bool(dataset["enabled"]):
            continue
        if selected_names and str(dataset["name"]) not in selected_names:
            continue
        selected.append(dataset)
    if not selected:
        raise ValueError("실행할 enabled dataset이 없습니다.")
    return selected


def run_exam_term_raw_relation_eda(
    cli_args: Namespace,
) -> dict[str, object]:
    """격리 원문 관계 EDA를 실행하고 결과를 저장한다."""
    policy = load_exam_term_raw_relation_policy(
        cli_args.eda_config,
        cli_args.relation_config,
        cli_args.resolution_config,
        cli_args.source_first_config,
    )
    selected_datasets = select_dataset_policies(
        policy,
        list(cli_args.dataset),
    )
    input_paths = {
        "canonical_registry": Path(cli_args.canonical_registry),
        "exam_term_matches": Path(cli_args.exam_term_matches),
        "source_nodes": Path(cli_args.source_nodes),
        "source_resolutions": Path(cli_args.source_resolutions),
        "aks_articles_list": Path(cli_args.aks_articles_list),
        "raw_data_root": Path(cli_args.raw_data_root),
    }
    missing_inputs = [
        str(path) for path in input_paths.values() if not path.exists()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "원문 관계 EDA 입력이 없습니다: "
            + ", ".join(missing_inputs)
        )
    output_directory = Path(cli_args.output_dir)
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "EXAM_TERM_RAW_RELATION_EDA",
            "dry_run": True,
            "selected_datasets": [
                str(dataset["name"])
                for dataset in selected_datasets
            ],
            "llm_used": False,
            "neo4j_load": False,
            "output_directory": str(output_directory),
        }
    canonical_registry = pd.read_csv(
        input_paths["canonical_registry"],
        dtype=str,
    ).fillna("")
    exam_term_matches = pd.read_csv(
        input_paths["exam_term_matches"],
        dtype=str,
    ).fillna("")
    source_nodes = pd.read_csv(
        input_paths["source_nodes"],
        dtype=str,
    ).fillna("")
    source_resolutions = pd.read_csv(
        input_paths["source_resolutions"],
        dtype=str,
    ).fillna("")
    aks_source_release = infer_aks_source_release(
        canonical_registry,
        policy,
    )

    def iter_selected_documents() -> Iterator[dict]:
        for dataset_policy in selected_datasets:
            yield from iter_dataset_documents(
                str(input_paths["raw_data_root"]),
                dataset_policy,
                aks_source_release,
                int(
                    policy["exam_term_raw_relation_eda"][
                        "maximum_csv_field_size"
                    ]
                ),
                cli_args.document_limit_per_dataset,
            )

    tables, statistics = build_raw_relation_eda_tables(
        canonical_registry,
        exam_term_matches,
        source_nodes,
        source_resolutions,
        str(input_paths["aks_articles_list"]),
        iter_selected_documents(),
        policy,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    eda_policy = policy["exam_term_raw_relation_eda"]
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = (
            output_directory / eda_policy["outputs"][table_name]
        )
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path)
    manifest = {
        "status": "COMPLETED",
        "stage": "EXAM_TERM_RAW_RELATION_EDA",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": str(eda_policy["policy_version"]),
        "selected_datasets": [
            str(dataset["name"]) for dataset in selected_datasets
        ],
        "document_limit_per_dataset": (
            cli_args.document_limit_per_dataset
        ),
        "llm_used": False,
        "neo4j_load": False,
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory / eda_policy["outputs"]["manifest"]
    )
    with manifest_path.open("w", encoding="utf-8") as output_file:
        dump(manifest, output_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """CLI 실행 결과를 JSON으로 출력한다."""
    result = run_exam_term_raw_relation_eda(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

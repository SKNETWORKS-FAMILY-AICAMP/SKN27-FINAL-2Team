from __future__ import annotations

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps, load
from pathlib import Path
import sys
from typing import Iterator

from kiwipiepy import Kiwi
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from choice_relation.exam_term_nlp_relations import (
    build_exam_term_nlp_relation_tables,
)
from choice_relation.exam_term_raw_relations import (
    build_exam_endpoint_groups,
    build_target_endpoint_groups,
    infer_aks_source_release,
    iter_dataset_documents,
    load_exam_term_raw_relation_policy,
)


def parse_arguments() -> Namespace:
    """NLP 관계 후보 실행 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent
    project_root = neo4j_root.parents[2]
    parser = ArgumentParser(
        description=(
            "등록 기출 용어와 같은 절의 Kiwi 명사구를 관계 "
            "후보로 연결합니다. LLM 호출과 적재는 하지 않습니다."
        )
    )
    parser.add_argument(
        "--nlp-config",
        default=str(
            neo4j_root
            / "config"
            / "exam_term_nlp_relations.json"
        ),
    )
    parser.add_argument(
        "--noun-config",
        default=str(
            neo4j_root
            / "config"
            / "exam_term_noun_phrase_eda.json"
        ),
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
            neo4j_root / "output" / "exam_term_nlp_relations"
        ),
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--document-limit-per-dataset",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--additional-predicates-only",
        action="store_true",
        help=(
            "기존 결과에 추가할 신규 술어만 추출합니다. "
            "LLM 호출과 Neo4j 적재는 하지 않습니다."
        ),
    )
    return parser.parse_args()


def run_exam_term_nlp_relations(
    cli_args: Namespace,
) -> dict[str, object]:
    """NLP 관계 후보를 생성하고 격리된 CSV로 저장한다."""
    policy = load_exam_term_raw_relation_policy(
        cli_args.eda_config,
        cli_args.relation_config,
        cli_args.resolution_config,
        cli_args.source_first_config,
    )
    with open(
        cli_args.noun_config,
        "r",
        encoding="utf-8",
    ) as input_file:
        noun_policy = load(input_file)
    with open(
        cli_args.nlp_config,
        "r",
        encoding="utf-8",
    ) as input_file:
        nlp_policy = load(input_file)
    additional_trigger_rules = [
        dict(rule)
        for rule in nlp_policy[
            "additional_relationship_trigger_rules"
        ]
    ]
    configured_trigger_rules: list[dict[str, object]] = []
    if cli_args.additional_predicates_only:
        configured_trigger_rules = additional_trigger_rules
    elif not cli_args.additional_predicates_only:
        patterns_by_family: dict[str, list[str]] = {}
        for rule in policy["exam_relation_candidates"][
            "relationship_trigger_rules"
        ]:
            family = str(rule["predicate_family"])
            patterns_by_family[family] = [
                str(value) for value in rule["patterns"]
            ]
        for rule in additional_trigger_rules:
            family = str(rule["predicate_family"])
            patterns = patterns_by_family.setdefault(family, [])
            for value in rule["patterns"]:
                pattern = str(value)
                if pattern not in patterns:
                    patterns.append(pattern)
        configured_trigger_rules = [
            {
                "predicate_family": family,
                "patterns": patterns,
            }
            for family, patterns in patterns_by_family.items()
        ]
    policy["exam_relation_candidates"][
        "relationship_trigger_rules"
    ] = configured_trigger_rules
    configured_datasets = {
        str(dataset["name"]): dataset
        for dataset in policy["exam_term_raw_relation_eda"][
            "datasets"
        ]
    }
    selected_names = {
        str(value) for value in cli_args.dataset
    }
    unknown_names = selected_names.difference(
        configured_datasets
    )
    if unknown_names:
        raise ValueError(
            "설정에 없는 dataset입니다: "
            + ", ".join(sorted(unknown_names))
        )
    selected_datasets = [
        dataset
        for name, dataset in configured_datasets.items()
        if bool(dataset["enabled"])
        and (not selected_names or name in selected_names)
    ]
    if not selected_datasets:
        raise ValueError("실행할 enabled dataset이 없습니다.")

    canonical_registry = pd.read_csv(
        cli_args.canonical_registry,
        dtype=str,
    ).fillna("")
    exam_term_matches = pd.read_csv(
        cli_args.exam_term_matches,
        dtype=str,
    ).fillna("")
    source_nodes = pd.read_csv(
        cli_args.source_nodes,
        dtype=str,
    ).fillna("")
    source_resolutions = pd.read_csv(
        cli_args.source_resolutions,
        dtype=str,
    ).fillna("")
    aks_source_release = infer_aks_source_release(
        canonical_registry,
        policy,
    )
    exam_groups, exam_statistics = build_exam_endpoint_groups(
        exam_term_matches,
        canonical_registry,
        policy,
    )
    target_groups, target_statistics = (
        build_target_endpoint_groups(
            canonical_registry,
            source_nodes,
            source_resolutions,
            cli_args.aks_articles_list,
            aks_source_release,
            policy,
        )
    )

    def iter_selected_documents() -> Iterator[dict]:
        for dataset_policy in selected_datasets:
            yield from iter_dataset_documents(
                cli_args.raw_data_root,
                dataset_policy,
                aks_source_release,
                int(
                    policy["exam_term_raw_relation_eda"][
                        "maximum_csv_field_size"
                    ]
                ),
                cli_args.document_limit_per_dataset,
            )

    tables, statistics = build_exam_term_nlp_relation_tables(
        iter_selected_documents(),
        exam_groups,
        target_groups,
        policy,
        noun_policy,
        nlp_policy,
        Kiwi(),
    )
    statistics.update(exam_statistics)
    statistics.update(target_statistics)
    output_directory = Path(cli_args.output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = (
            output_directory
            / str(nlp_policy["outputs"][table_name])
        )
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path)
    manifest = {
        "status": "COMPLETED",
        "stage": "EXAM_TERM_NLP_RELATION_CANDIDATES",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": str(nlp_policy["policy_version"]),
        "selected_datasets": [
            str(dataset["name"]) for dataset in selected_datasets
        ],
        "document_limit_per_dataset": (
            cli_args.document_limit_per_dataset
        ),
        "additional_predicates_only": bool(
            cli_args.additional_predicates_only
        ),
        "minimum_registered_endpoint_count_per_relation": int(
            nlp_policy[
                "minimum_registered_endpoint_count_per_relation"
            ]
        ),
        "llm_used": False,
        "neo4j_load": False,
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory
        / str(nlp_policy["outputs"]["manifest"])
    )
    with manifest_path.open(
        "w",
        encoding="utf-8",
    ) as output_file:
        dump(manifest, output_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """CLI 실행 결과를 출력한다."""
    result = run_exam_term_nlp_relations(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

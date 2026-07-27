from __future__ import annotations

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from hashlib import sha256
from json import dump, dumps, load, loads
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from fact_retrieval.build import build_entity_anchor_tables
from fact_retrieval.external_results import (
    apply_external_verification_results,
)
from fact_retrieval.load import load_fact_retrieval_to_neo4j
from fact_retrieval.retrieve import build_swap_candidates
from fact_retrieval.truth_gate import evaluate_distractor_truth_gate


def calculate_file_sha256(file_path: Path) -> str:
    """입력 snapshot 식별용 SHA-256을 계산한다."""
    hasher = sha256()
    with file_path.open("rb") as input_file:
        for chunk in iter(
            lambda: input_file.read(1024 * 1024),
            b"",
        ):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_policy(policy_path: Path) -> dict:
    """사실 검색 정책을 읽는다."""
    if not policy_path.is_file():
        raise FileNotFoundError(
            f"사실 검색 정책 파일이 없습니다: {policy_path}"
        )
    with policy_path.open("r", encoding="utf-8") as policy_file:
        return load(policy_file)


def parse_arguments() -> Namespace:
    """사실 검색 그래프 통합 runner 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent
    parser = ArgumentParser(
        description=(
            "EntityAnchor, 공식 1-hop 관계, RAG 교체 후보와 "
            "오답 사실 검증 gate를 생성합니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(neo4j_root / "config" / "fact_retrieval.json"),
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
        "--canonical-facts",
        default=str(
            neo4j_root
            / "output"
            / "source_relationships"
            / "canonical_fact_relationships.csv"
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
        "--source-relationships",
        default=str(
            neo4j_root
            / "output"
            / "source_relationships"
            / "source_record_relationships.csv"
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
        "--canonical-topics",
        default=str(
            neo4j_root
            / "output"
            / "final_identity"
            / "neo4j_canonical_to_topic_relationships.csv"
        ),
    )
    parser.add_argument(
        "--canonical-eras",
        default=str(
            neo4j_root
            / "output"
            / "final_identity"
            / "neo4j_canonical_to_era_relationships.csv"
        ),
    )
    parser.add_argument(
        "--exam-term-links",
        default=str(
            neo4j_root
            / "output"
            / "final_identity"
            / "neo4j_exam_term_to_entity_relationships.csv"
        ),
    )
    parser.add_argument(
        "--identity-source-nodes",
        default=str(
            neo4j_root
            / "output"
            / "final_identity"
            / "neo4j_source_record_nodes.csv"
        ),
    )
    parser.add_argument(
        "--candidate-source-records",
        default=str(
            neo4j_root
            / "output"
            / "internal"
            / "entity_resolution"
            / "candidate_source_records.csv"
        ),
    )
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--external-task-offset",
        type=int,
        default=0,
    )
    parser.add_argument(
        "--external-verification-results",
        default="",
    )
    parser.add_argument("--load-neo4j", action="store_true")
    parser.add_argument("--database", default="")
    parser.add_argument("--batch-size", type=int, default=None)
    return parser.parse_args()


def write_jsonl(records: list[dict], output_path: Path) -> None:
    """외부 사실 검증 task를 JSONL로 저장한다."""
    with output_path.open("w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(
                dumps(record, ensure_ascii=False) + "\n"
            )


def read_jsonl(input_path: Path) -> list[dict]:
    """외부 사실 검증 JSONL을 읽는다."""
    records: list[dict] = []
    with input_path.open("r", encoding="utf-8") as input_file:
        for line in input_file:
            if line.strip():
                records.append(loads(line))
    return records


def run_fact_retrieval_pipeline(
    cli_args: Namespace,
) -> dict[str, object]:
    """사실 검색 그래프를 생성하고 Neo4j 적재 가능 여부를 검사한다."""
    project_root = Path(__file__).resolve().parents[3]
    policy = load_policy(Path(cli_args.config))
    input_paths = {
        "canonical_registry": Path(cli_args.canonical_registry),
        "canonical_facts": Path(cli_args.canonical_facts),
        "source_nodes": Path(cli_args.source_nodes),
        "source_relationships": Path(cli_args.source_relationships),
        "source_resolutions": Path(cli_args.source_resolutions),
        "canonical_topics": Path(cli_args.canonical_topics),
        "canonical_eras": Path(cli_args.canonical_eras),
        "exam_term_links": Path(cli_args.exam_term_links),
        "identity_source_nodes": Path(cli_args.identity_source_nodes),
        "candidate_source_records": Path(
            cli_args.candidate_source_records
        ),
    }
    missing_inputs = [
        f"{name}: {path}"
        for name, path in input_paths.items()
        if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "사실 검색 그래프 입력이 없습니다: "
            + ", ".join(missing_inputs)
        )
    output_directory = (
        project_root / policy["outputs"]["default_directory"]
    )
    if cli_args.output_dir:
        output_directory = Path(cli_args.output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)

    canonical_registry = pd.read_csv(
        input_paths["canonical_registry"],
        dtype=str,
    ).fillna("")
    canonical_facts = pd.read_csv(
        input_paths["canonical_facts"],
        dtype=str,
    ).fillna("")
    source_nodes = pd.read_csv(
        input_paths["source_nodes"],
        dtype=str,
    ).fillna("")
    source_relationships = pd.read_csv(
        input_paths["source_relationships"],
        dtype=str,
    ).fillna("")
    source_resolutions = pd.read_csv(
        input_paths["source_resolutions"],
        dtype=str,
    ).fillna("")
    canonical_topics = pd.read_csv(
        input_paths["canonical_topics"],
        dtype=str,
    ).fillna("")
    canonical_eras = pd.read_csv(
        input_paths["canonical_eras"],
        dtype=str,
    ).fillna("")
    exam_term_links = pd.read_csv(
        input_paths["exam_term_links"],
        dtype=str,
    ).fillna("")
    identity_source_nodes = pd.read_csv(
        input_paths["identity_source_nodes"],
        dtype=str,
    ).fillna("")
    candidate_source_records = pd.read_csv(
        input_paths["candidate_source_records"],
        dtype=str,
    ).fillna("")
    accepted_exam_links = exam_term_links[
        exam_term_links["match_status"].eq("ACCEPTED")
    ]
    exam_canonical_ids = set(
        accepted_exam_links["canonical_id"]
    )

    anchor_tables = build_entity_anchor_tables(
        canonical_registry,
        canonical_facts,
        source_nodes,
        source_relationships,
        source_resolutions,
        canonical_topics,
        canonical_eras,
        policy,
    )
    swap_candidates = build_swap_candidates(
        canonical_facts,
        anchor_tables["anchor_nodes"],
        anchor_tables["anchor_fact_relationships"],
        policy,
        exam_canonical_ids=exam_canonical_ids,
    )
    truth_gate_results, external_task_backlog = (
        evaluate_distractor_truth_gate(
            swap_candidates,
            canonical_facts,
            policy,
        )
    )
    if cli_args.external_task_offset < 0:
        raise ValueError("external task offset은 0 이상이어야 합니다.")
    external_task_batch_size = int(
        policy["truth_gate"]["external_task_batch_size"]
    )
    external_task_end = (
        cli_args.external_task_offset + external_task_batch_size
    )
    external_tasks = external_task_backlog[
        cli_args.external_task_offset : external_task_end
    ]
    verification_results: list[dict] = []
    verification_results_requested = bool(
        cli_args.external_verification_results
    )
    if verification_results_requested:
        verification_result_path = Path(
            cli_args.external_verification_results
        )
        if not verification_result_path.is_file():
            raise FileNotFoundError(
                "외부 사실 검증 결과 파일이 없습니다: "
                f"{verification_result_path}"
            )
        verification_results = read_jsonl(
            verification_result_path
        )
    final_truth_gate_results, verification_application = (
        apply_external_verification_results(
            truth_gate_results,
            external_task_backlog,
            verification_results,
            policy,
        )
    )
    if not verification_results_requested:
        verification_application["status"] = "NOT_REQUESTED"
    source_node_columns = [
        "source_record_id",
        "source",
        "source_key",
        "source_release",
        "source_metadata_json",
    ]
    combined_source_nodes = pd.concat(
        [
            source_nodes.reindex(columns=source_node_columns),
            identity_source_nodes.reindex(
                columns=source_node_columns
            ),
            candidate_source_records.reindex(
                columns=source_node_columns
            ),
        ],
        ignore_index=True,
    ).fillna("")
    combined_source_nodes = combined_source_nodes.drop_duplicates(
        "source_record_id",
        keep="first",
    )
    used_source_ids = set(
        anchor_tables["source_anchor_links"]["source_record_id"]
    )
    supporting_source_nodes = combined_source_nodes[
        combined_source_nodes["source_record_id"].isin(used_source_ids)
    ].copy()
    output_tables = {
        **anchor_tables,
        "supporting_source_nodes": supporting_source_nodes,
        "swap_candidates": swap_candidates,
        "truth_gate_results": truth_gate_results,
        "final_truth_gate_results": final_truth_gate_results,
    }
    output_paths: dict[str, str] = {}
    for table_name, table in output_tables.items():
        output_path = (
            output_directory / policy["outputs"][table_name]
        )
        table.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )
        output_paths[table_name] = str(output_path)
    external_task_path = (
        output_directory
        / policy["outputs"]["external_verification_tasks"]
    )
    write_jsonl(external_tasks, external_task_path)
    output_paths["external_verification_tasks"] = str(
        external_task_path
    )
    external_backlog_path = (
        output_directory
        / policy["outputs"]["external_verification_backlog"]
    )
    write_jsonl(
        external_task_backlog,
        external_backlog_path,
    )
    output_paths["external_verification_backlog"] = str(
        external_backlog_path
    )

    load_tables = {
        "canonical_facts": canonical_facts,
        "anchor_nodes": anchor_tables["anchor_nodes"],
        "supporting_source_nodes": supporting_source_nodes,
        "canonical_anchor_links": anchor_tables[
            "canonical_anchor_links"
        ],
        "source_anchor_links": anchor_tables["source_anchor_links"],
        "anchor_facts": anchor_tables[
            "anchor_fact_relationships"
        ],
    }
    load_result = load_fact_retrieval_to_neo4j(
        load_tables,
        canonical_registry,
        policy,
        project_root,
        output_directory,
        database=cli_args.database,
        batch_size=cli_args.batch_size,
        dry_run=not cli_args.load_neo4j,
    )
    gate_status_counts = {
        str(status): int(count)
        for status, count in truth_gate_results[
            "truth_gate_status"
        ].value_counts().items()
    }
    final_gate_status_counts = {
        str(status): int(count)
        for status, count in final_truth_gate_results[
            "truth_gate_status"
        ].value_counts().items()
    }
    canonical_anchor_ids = set(
        anchor_tables["anchor_nodes"][
            anchor_tables["anchor_nodes"]["anchor_kind"].eq(
                policy["anchor_projection"][
                    "canonical_anchor_kind"
                ]
            )
        ]["canonical_id"]
    )
    candidate_correct_fact_ids = set(
        swap_candidates["correct_canonical_relationship_id"]
    )
    candidate_proposed_triples = set(
        zip(
            swap_candidates["proposed_start_canonical_id"],
            swap_candidates["relation_type"],
            swap_candidates["proposed_end_canonical_id"],
        )
    )
    input_snapshots = {
        name: {
            "path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "sha256": calculate_file_sha256(path),
        }
        for name, path in input_paths.items()
    }
    if verification_results_requested:
        input_snapshots["external_verification_results"] = {
            "path": str(verification_result_path.resolve()),
            "size_bytes": verification_result_path.stat().st_size,
            "sha256": calculate_file_sha256(
                verification_result_path
            ),
        }
    manifest = {
        "status": "COMPLETED",
        "stage": "FACT_RETRIEVAL_PIPELINE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": str(policy["policy_version"]),
        "input_snapshots": input_snapshots,
        "database_load_requested": bool(cli_args.load_neo4j),
        "canonical_fact_count": len(canonical_facts),
        "anchor_node_count": len(anchor_tables["anchor_nodes"]),
        "canonical_anchor_count": int(
            anchor_tables["anchor_nodes"]["anchor_kind"]
            .eq(policy["anchor_projection"]["canonical_anchor_kind"])
            .sum()
        ),
        "official_source_anchor_count": int(
            anchor_tables["anchor_nodes"]["anchor_kind"]
            .eq(policy["anchor_projection"]["source_anchor_kind"])
            .sum()
        ),
        "anchor_fact_count": len(
            anchor_tables["anchor_fact_relationships"]
        ),
        "primary_anchor_fact_count": int(
            anchor_tables["anchor_fact_relationships"][
                "search_status"
            ]
            .eq(
                policy["anchor_projection"][
                    "canonical_search_status"
                ]
            )
            .sum()
        ),
        "fallback_anchor_fact_count": int(
            anchor_tables["anchor_fact_relationships"][
                "search_status"
            ]
            .eq(
                policy["anchor_projection"][
                    "source_neighbor_search_status"
                ]
            )
            .sum()
        ),
        "swap_candidate_count": len(swap_candidates),
        "quality_summary": {
            "accepted_exam_term_count": int(
                accepted_exam_links["exam_term_id"].nunique()
            ),
            "accepted_exam_canonical_count": len(
                exam_canonical_ids
            ),
            "exam_canonical_with_anchor_count": len(
                exam_canonical_ids.intersection(
                    canonical_anchor_ids
                )
            ),
            "exam_canonical_without_anchor_count": len(
                exam_canonical_ids.difference(
                    canonical_anchor_ids
                )
            ),
            "candidate_correct_fact_count": len(
                candidate_correct_fact_ids
            ),
            "unique_proposed_triple_count": len(
                candidate_proposed_triples
            ),
            "candidate_rows_reusing_proposed_triple_count": (
                len(swap_candidates)
                - len(candidate_proposed_triples)
            ),
            "candidate_fallback_path_count": int(
                swap_candidates["fallback_graph_edge_count"]
                .astype(int)
                .gt(0)
                .sum()
            ),
        },
        "truth_gate_status_counts": gate_status_counts,
        "final_truth_gate_status_counts": (
            final_gate_status_counts
        ),
        "external_verification_application": (
            verification_application
        ),
        "external_verification_task_count": len(external_tasks),
        "external_verification_backlog_count": len(
            external_task_backlog
        ),
        "external_verification_task_offset": (
            cli_args.external_task_offset
        ),
        "external_verification_remaining_count": max(
            len(external_task_backlog) - external_task_end,
            0,
        ),
        "load_result": load_result,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory / policy["outputs"]["manifest"]
    )
    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main() -> None:
    """통합 실행 결과를 JSON으로 출력한다."""
    result = run_fact_retrieval_pipeline(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

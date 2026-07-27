from __future__ import annotations

from argparse import ArgumentParser, Namespace
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from json import dump, dumps, load, loads
from pathlib import Path
import re

import pandas as pd


def parse_arguments() -> Namespace:
    """Read fact graph EDA input and output paths."""
    neo4j_root = Path(__file__).resolve().parent
    output_root = neo4j_root / "output"
    parser = ArgumentParser(
        description=(
            "Split assembled fact candidates into trusted load data and "
            "human-review queues without calling an LLM or Neo4j."
        )
    )
    parser.add_argument(
        "--config",
        default=str(neo4j_root / "config" / "fact_graph_pipeline.json"),
    )
    parser.add_argument(
        "--candidate-csv",
        default=str(
            output_root
            / "exam_anchor_fact_graph"
            / "all_fact_graph_candidates.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(output_root / "fact_graph_eda"),
    )
    return parser.parse_args()


def normalize_name(value: str) -> str:
    """Normalize a display name only for duplicate candidate retrieval."""
    normalized = re.sub(r"\s+", "", str(value)).casefold()
    return re.sub(r"[^0-9a-z가-힣一-龥]", "", normalized)


def make_stable_id(prefix: str, *values: str) -> str:
    """Create a stable review identifier from immutable source values."""
    payload = "\u241f".join(str(value) for value in values)
    return f"{prefix}:{sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def parse_json_list(value: object) -> list[str]:
    """Parse a JSON list while tolerating an empty CSV cell."""
    if not str(value).strip():
        return []
    parsed = loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON list, received: {value}")
    return [str(item) for item in parsed]


def preserve_review_decisions(
    generated: pd.DataFrame,
    existing: pd.DataFrame,
    id_column: str,
    decision_columns: list[str],
) -> pd.DataFrame:
    """Preserve decisions only when the immutable review fingerprint matches."""
    if existing.empty or generated.empty:
        return generated
    required_columns = {
        id_column,
        "review_fingerprint",
        *decision_columns,
    }
    if not required_columns.issubset(existing.columns):
        return generated
    existing_by_id = existing.set_index(id_column).to_dict("index")
    preserved = generated.copy()
    for row_index, row in preserved.iterrows():
        old_row = existing_by_id.get(str(row[id_column]))
        if old_row is None:
            continue
        if str(old_row["review_fingerprint"]) != str(
            row["review_fingerprint"]
        ):
            continue
        for column in decision_columns:
            preserved.at[row_index, column] = str(old_row[column])
    return preserved


def build_review_tables(
    candidates: pd.DataFrame,
    policy: dict[str, object],
    existing_entity_review: pd.DataFrame | None = None,
    existing_relation_review: pd.DataFrame | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """Build entity-duplicate and NLP-relation review queues."""
    required_columns = {
        "fact_graph_candidate_id",
        "start_node_id",
        "start_node_kind",
        "start_display_name",
        "start_entity_type",
        "relation_type",
        "end_node_id",
        "end_node_kind",
        "end_display_name",
        "end_entity_type",
        "candidate_tier",
        "evidence_ids_json",
        "evidence_records_json",
        "evidence_metadata_complete",
        "source_datasets_json",
    }
    missing_columns = sorted(required_columns.difference(candidates.columns))
    if missing_columns:
        raise ValueError(
            "Fact candidate CSV is missing columns: "
            + ", ".join(missing_columns)
        )

    trusted_tiers = {
        str(value) for value in policy["trusted_candidate_tiers"]
    }
    reviewed_tiers = {
        str(value) for value in policy["reviewed_candidate_tiers"]
    }
    auto_accepted_tiers = {
        str(value)
        for value in policy["auto_accepted_candidate_tiers"]
    }
    review_routing = policy["review_routing"]
    provisional_status = str(
        policy["trust_policy"]["provisional_status"]
    )
    stable_node_kinds = {
        str(value)
        for value in review_routing["stable_node_kinds"]
    }
    unresolved_node_kinds = {
        str(value)
        for value in review_routing["unresolved_node_kinds"]
    }
    endpoint_priority_minimum = int(
        review_routing[
            "endpoint_priority_minimum_relation_count"
        ]
    )
    endpoint_priority_statuses = {
        str(value)
        for value in review_routing[
            "endpoint_priority_verification_statuses"
        ]
    }
    node_details: dict[str, dict[str, object]] = {}
    node_ids_by_normalized_name: dict[str, set[str]] = defaultdict(set)
    existing_ids_by_normalized_name: dict[str, set[str]] = defaultdict(set)

    for row in candidates.to_dict("records"):
        relation_id = str(row["fact_graph_candidate_id"])
        relation_type = str(row["relation_type"])
        candidate_tier = str(row["candidate_tier"])
        source_datasets = parse_json_list(row["source_datasets_json"])
        endpoints = [
            (
                "start",
                "end",
                "OUT",
            ),
            (
                "end",
                "start",
                "IN",
            ),
        ]
        for endpoint, neighbor, direction in endpoints:
            node_id = str(row[f"{endpoint}_node_id"])
            node_kind = str(row[f"{endpoint}_node_kind"])
            display_name = str(row[f"{endpoint}_display_name"])
            entity_type = str(row[f"{endpoint}_entity_type"])
            normalized_name = normalize_name(display_name)
            neighbor_signature = ":".join(
                [
                    direction,
                    relation_type,
                    str(row[f"{neighbor}_node_kind"]),
                    normalize_name(str(row[f"{neighbor}_display_name"])),
                ]
            )
            if node_id not in node_details:
                node_details[node_id] = {
                    "node_kind": node_kind,
                    "display_name": display_name,
                    "entity_type": entity_type,
                    "normalized_name": normalized_name,
                    "relation_ids": set(),
                    "candidate_tiers": set(),
                    "source_datasets": set(),
                    "neighbor_signatures": set(),
                    "verification_statuses": set(),
                }
            detail = node_details[node_id]
            detail["relation_ids"].add(relation_id)
            detail["candidate_tiers"].add(candidate_tier)
            detail["source_datasets"].update(source_datasets)
            detail["neighbor_signatures"].add(neighbor_signature)
            verification_status = str(
                row.get("verification_status", "")
            )
            if verification_status:
                detail["verification_statuses"].add(
                    verification_status
                )
            if node_kind in unresolved_node_kinds:
                node_ids_by_normalized_name[normalized_name].add(node_id)
            elif node_kind not in unresolved_node_kinds:
                existing_ids_by_normalized_name[normalized_name].add(node_id)

    entity_review_rows: list[dict[str, object]] = []
    priority_node_ids = {
        node_id
        for node_id, detail in node_details.items()
        if str(detail["node_kind"]) in unresolved_node_kinds
        and (
            len(detail["relation_ids"]) >= endpoint_priority_minimum
            or bool(
                set(detail["verification_statuses"]).intersection(
                    endpoint_priority_statuses
                )
            )
        )
    }
    duplicate_group_count = 0
    duplicate_group_node_ids: set[str] = set()
    exact_existing_match_node_ids: set[str] = set()
    for normalized_name, open_node_ids in sorted(
        node_ids_by_normalized_name.items()
    ):
        existing_target_ids = sorted(
            existing_ids_by_normalized_name.get(normalized_name, set())
        )
        is_duplicate_group = len(open_node_ids) > 1
        if is_duplicate_group:
            duplicate_group_count += 1
            duplicate_group_node_ids.update(open_node_ids)
        if existing_target_ids:
            exact_existing_match_node_ids.update(open_node_ids)
        review_node_ids = set(open_node_ids).intersection(
            priority_node_ids
        )
        if not review_node_ids:
            continue
        duplicate_group_id = make_stable_id(
            "entity-review-group",
            normalized_name,
        )
        for node_id in sorted(review_node_ids):
            detail = node_details[node_id]
            priority_reasons: list[str] = []
            if len(detail["relation_ids"]) >= endpoint_priority_minimum:
                priority_reasons.append("HIGH_RELATION_FREQUENCY")
            if set(detail["verification_statuses"]).intersection(
                endpoint_priority_statuses
            ):
                priority_reasons.append(
                    "CORROBORATED_RELATION_ENDPOINT"
                )
            fingerprint_values = [
                node_id,
                normalized_name,
                str(detail["entity_type"]),
                dumps(existing_target_ids, ensure_ascii=False),
                dumps(
                    sorted(detail["neighbor_signatures"]),
                    ensure_ascii=False,
                ),
                dumps(priority_reasons, ensure_ascii=False),
            ]
            entity_review_rows.append(
                {
                    "entity_review_id": make_stable_id(
                        "entity-review",
                        node_id,
                        normalized_name,
                    ),
                    "duplicate_group_id": duplicate_group_id,
                    "normalized_name": normalized_name,
                    "node_id": node_id,
                    "node_kind": str(detail["node_kind"]),
                    "display_name": str(detail["display_name"]),
                    "entity_type": str(detail["entity_type"]),
                    "duplicate_group_node_count": len(open_node_ids),
                    "relation_count": len(detail["relation_ids"]),
                    "candidate_tiers_json": dumps(
                        sorted(detail["candidate_tiers"]),
                        ensure_ascii=False,
                    ),
                    "source_datasets_json": dumps(
                        sorted(detail["source_datasets"]),
                        ensure_ascii=False,
                    ),
                    "neighbor_signatures_json": dumps(
                        sorted(detail["neighbor_signatures"]),
                        ensure_ascii=False,
                    ),
                    "verification_statuses_json": dumps(
                        sorted(detail["verification_statuses"]),
                        ensure_ascii=False,
                    ),
                    "priority_reasons_json": dumps(
                        priority_reasons,
                        ensure_ascii=False,
                    ),
                    "existing_target_node_ids_json": dumps(
                        existing_target_ids,
                        ensure_ascii=False,
                    ),
                    "review_fingerprint": make_stable_id(
                        "fingerprint",
                        *fingerprint_values,
                    ),
                    "review_decision": "",
                    "review_target_node_id": "",
                    "review_note": "",
                }
            )

    entity_columns = [
        "entity_review_id",
        "duplicate_group_id",
        "normalized_name",
        "node_id",
        "node_kind",
        "display_name",
        "entity_type",
        "duplicate_group_node_count",
        "relation_count",
        "candidate_tiers_json",
        "source_datasets_json",
        "neighbor_signatures_json",
        "verification_statuses_json",
        "priority_reasons_json",
        "existing_target_node_ids_json",
        "review_fingerprint",
        "review_decision",
        "review_target_node_id",
        "review_note",
    ]
    entity_review = pd.DataFrame(
        entity_review_rows,
        columns=entity_columns,
    )
    existing_entity_table = pd.DataFrame()
    if existing_entity_review is not None:
        existing_entity_table = existing_entity_review
    entity_review = preserve_review_decisions(
        entity_review,
        existing_entity_table,
        "entity_review_id",
        [
            "review_decision",
            "review_target_node_id",
            "review_note",
        ],
    )

    relation_review_rows: list[dict[str, object]] = []
    deferred_relation_rows: list[dict[str, object]] = []
    for row in candidates.to_dict("records"):
        if str(row["candidate_tier"]) not in reviewed_tiers:
            continue
        start_node_kind = str(row["start_node_kind"])
        end_node_kind = str(row["end_node_kind"])
        relation_record = {
            "fact_graph_candidate_id": str(
                row["fact_graph_candidate_id"]
            ),
            "relation_display": " -[".join(
                [
                    str(row["start_display_name"]),
                    (
                        str(row["relation_type"])
                        + "]-> "
                        + str(row["end_display_name"])
                    ),
                ]
            ),
            "start_node_id": str(row["start_node_id"]),
            "start_node_kind": start_node_kind,
            "start_entity_type": str(row["start_entity_type"]),
            "relation_type": str(row["relation_type"]),
            "end_node_id": str(row["end_node_id"]),
            "end_node_kind": end_node_kind,
            "end_entity_type": str(row["end_entity_type"]),
            "candidate_tier": str(row["candidate_tier"]),
            "verification_status": str(
                row.get("verification_status", "")
            ),
            "evidence_count": str(row.get("evidence_count", "")),
            "evidence_ids_json": str(
                row.get("evidence_ids_json", "[]")
            ),
            "source_datasets_json": str(
                row["source_datasets_json"]
            ),
        }
        if (
            start_node_kind not in stable_node_kinds
            or end_node_kind not in stable_node_kinds
        ):
            deferred_relation_rows.append(
                {
                    **relation_record,
                    "load_trust_status": provisional_status,
                    "default_retrieval_eligible": False,
                    "defer_reason": str(
                        review_routing[
                            "deferred_relation_reason"
                        ]
                    ),
                }
            )
            continue
        fingerprint_values = [
            str(row[column])
            for column in [
                "fact_graph_candidate_id",
                "start_node_id",
                "relation_type",
                "end_node_id",
                "candidate_tier",
                "evidence_ids_json",
            ]
            if column in row
        ]
        relation_review_rows.append(
            {
                "relation_review_id": make_stable_id(
                    "relation-review",
                    str(row["fact_graph_candidate_id"]),
                ),
                **relation_record,
                "review_fingerprint": make_stable_id(
                    "fingerprint",
                    *fingerprint_values,
                ),
                "review_decision": "",
                "review_note": "",
            }
        )
    relation_columns = [
        "relation_review_id",
        "fact_graph_candidate_id",
        "relation_display",
        "start_node_id",
        "start_node_kind",
        "start_entity_type",
        "relation_type",
        "end_node_id",
        "end_node_kind",
        "end_entity_type",
        "candidate_tier",
        "verification_status",
        "evidence_count",
        "evidence_ids_json",
        "source_datasets_json",
        "review_fingerprint",
        "review_decision",
        "review_note",
    ]
    relation_review = pd.DataFrame(
        relation_review_rows,
        columns=relation_columns,
    )
    deferred_relations = pd.DataFrame(
        deferred_relation_rows,
        columns=[
            column
            for column in relation_columns
            if column
            not in {
                "relation_review_id",
                "review_fingerprint",
                "review_decision",
                "review_note",
            }
        ]
        + [
            "load_trust_status",
            "default_retrieval_eligible",
            "defer_reason",
        ],
    )
    existing_relation_table = pd.DataFrame()
    if existing_relation_review is not None:
        existing_relation_table = existing_relation_review
    relation_review = preserve_review_decisions(
        relation_review,
        existing_relation_table,
        "relation_review_id",
        ["review_decision", "review_note"],
    )

    entity_pending_count = int(
        entity_review["review_decision"].astype(str).str.strip().eq("").sum()
    )
    relation_pending_count = int(
        relation_review["review_decision"].astype(str).str.strip().eq("").sum()
    )
    incomplete_evidence_mask = (
        candidates["evidence_metadata_complete"]
        .astype(str)
        .str.casefold()
        .ne("true")
        | candidates["evidence_ids_json"].astype(str).isin(["", "[]"])
    )
    statistics = {
        "fact_graph_candidate_count": len(candidates),
        "trusted_load_candidate_count": int(
            candidates["candidate_tier"].isin(trusted_tiers).sum()
        ),
        "reviewed_relation_candidate_count": len(relation_review),
        "deferred_relation_candidate_count": len(
            deferred_relations
        ),
        "provisional_load_candidate_count": len(
            deferred_relations
        ),
        "manual_review_candidate_count": (
            len(entity_review) + len(relation_review)
        ),
        "auto_accepted_relation_candidate_count": int(
            candidates["candidate_tier"].isin(
                auto_accepted_tiers
            ).sum()
        ),
        "unresolved_endpoint_node_count": len(
            {
                node_id
                for node_id, detail in node_details.items()
                if str(detail["node_kind"]) in unresolved_node_kinds
            }
        ),
        "priority_endpoint_review_node_count": len(priority_node_ids),
        "duplicate_name_group_count": duplicate_group_count,
        "duplicate_name_group_node_count": len(duplicate_group_node_ids),
        "exact_existing_name_match_node_count": len(
            exact_existing_match_node_ids
        ),
        "entity_review_node_count": len(entity_review),
        "entity_review_pending_count": entity_pending_count,
        "relation_review_pending_count": relation_pending_count,
        "incomplete_evidence_candidate_count": int(
            incomplete_evidence_mask.sum()
        ),
        "trusted_incomplete_evidence_candidate_count": int(
            (
                incomplete_evidence_mask
                & candidates["candidate_tier"].isin(trusted_tiers)
            ).sum()
        ),
        "semantic_duplicate_candidate_count": int(
            candidates.duplicated(
                [
                    "start_node_id",
                    "relation_type",
                    "end_node_id",
                ],
                keep=False,
            ).sum()
        ),
        "semantic_duplicate_group_count": int(
            candidates.groupby(
                [
                    "start_node_id",
                    "relation_type",
                    "end_node_id",
                ]
            ).size().gt(1).sum()
        ),
        "llm_used": False,
        "neo4j_load": False,
    }
    return {
        "entity_review": entity_review,
        "relation_review": relation_review,
        "deferred_relations": deferred_relations,
    }, statistics


def read_existing_review(path: Path) -> pd.DataFrame:
    """Read an existing review CSV so completed decisions survive reruns."""
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def run_fact_graph_eda_pipeline(cli_args: Namespace) -> dict[str, object]:
    """Build and save the reproducible human-review package."""
    with Path(cli_args.config).open("r", encoding="utf-8") as input_file:
        policy = load(input_file)
    output_directory = Path(cli_args.output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_names = policy["eda_outputs"]
    entity_path = output_directory / str(output_names["entity_review"])
    relation_path = output_directory / str(output_names["relation_review"])
    deferred_path = output_directory / str(
        output_names["deferred_relations"]
    )
    candidates = pd.read_csv(
        cli_args.candidate_csv,
        dtype=str,
        keep_default_na=False,
    )
    tables, statistics = build_review_tables(
        candidates,
        policy,
        read_existing_review(entity_path),
        read_existing_review(relation_path),
    )
    tables["entity_review"].to_csv(
        entity_path,
        index=False,
        encoding="utf-8-sig",
    )
    tables["relation_review"].to_csv(
        relation_path,
        index=False,
        encoding="utf-8-sig",
    )
    tables["deferred_relations"].to_csv(
        deferred_path,
        index=False,
        encoding="utf-8-sig",
    )
    summary_path = output_directory / str(output_names["summary"])
    pd.DataFrame(
        [
            {
                "metric": key.upper(),
                "value": value,
            }
            for key, value in statistics.items()
        ]
    ).to_csv(summary_path, index=False, encoding="utf-8-sig")
    review_is_complete = (
        statistics["entity_review_pending_count"] == 0
        and statistics["relation_review_pending_count"] == 0
    )
    status = "READY_FOR_HUMAN_REVIEW"
    if review_is_complete:
        status = "REVIEW_COMPLETED"
    manifest = {
        "status": status,
        "stage": "FACT_GRAPH_EDA",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": str(policy["policy_version"]),
        "source_candidate_csv": str(Path(cli_args.candidate_csv).resolve()),
        "statistics": statistics,
        "output_paths": {
            "entity_review": str(entity_path.resolve()),
            "relation_review": str(relation_path.resolve()),
            "deferred_relations": str(deferred_path.resolve()),
            "summary": str(summary_path.resolve()),
        },
    }
    manifest_path = output_directory / str(output_names["manifest"])
    with manifest_path.open("w", encoding="utf-8") as output_file:
        dump(manifest, output_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path.resolve())
    return manifest


def main() -> None:
    """Run the fact graph EDA pipeline."""
    result = run_fact_graph_eda_pipeline(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

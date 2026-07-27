from __future__ import annotations

from argparse import ArgumentParser, Namespace
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from json import dump, dumps, load, loads
import os
from pathlib import Path
import re

import pandas as pd

from run_fact_graph_eda_pipeline import build_review_tables


def parse_arguments() -> Namespace:
    """Read fact graph load-plan inputs and Neo4j execution options."""
    neo4j_root = Path(__file__).resolve().parent
    output_root = neo4j_root / "output"
    parser = ArgumentParser(
        description=(
            "Build a validated fact graph load plan. Neo4j is changed only "
            "when --execute-neo4j is supplied."
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
        "--entity-review-csv",
        default=str(
            output_root
            / "fact_graph_eda"
            / "entity_resolution_human_review.csv"
        ),
    )
    parser.add_argument(
        "--relation-review-csv",
        default=str(
            output_root
            / "fact_graph_eda"
            / "nlp_relation_human_review.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(output_root / "fact_graph_load"),
    )
    parser.add_argument(
        "--mode",
        choices=[
            "trusted_only",
            "trusted_and_provisional",
            "reviewed_all",
        ],
        default="trusted_and_provisional",
    )
    parser.add_argument(
        "--execute-neo4j",
        action="store_true",
    )
    return parser.parse_args()


def make_load_id(
    start_node_id: str,
    relation_type: str,
    end_node_id: str,
) -> str:
    """Create one stable ID for each semantic fact relationship."""
    payload = "\u241f".join(
        [start_node_id, relation_type, end_node_id]
    )
    digest = sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"fact-relationship:{digest}"


def make_graph_relationship_id(
    start_label: str,
    start_id: str,
    relation_type: str,
    end_label: str,
    end_id: str,
) -> str:
    """Create an idempotent ID for one schema relationship."""
    payload = "\u241f".join(
        [
            start_label,
            start_id,
            relation_type,
            end_label,
            end_id,
        ]
    )
    digest = sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"graph-relationship:{digest}"


def parse_json_list(value: object) -> list[str]:
    """Parse an evidence or source list stored in a CSV cell."""
    if not str(value).strip():
        return []
    parsed = loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON list, received: {value}")
    return [str(item) for item in parsed]


def parse_json_records(value: object) -> list[dict[str, str]]:
    """Parse a list of evidence metadata objects from a CSV cell."""
    if not str(value).strip():
        return []
    parsed = loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError(
            f"Expected a JSON record list, received: {value}"
        )
    records: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError(
                f"Expected evidence metadata object, received: {item}"
            )
        records.append(
            {str(key): str(field) for key, field in item.items()}
        )
    return records


def collect_node_metadata(
    candidates: pd.DataFrame,
    policy: dict[str, object],
) -> dict[str, dict[str, str]]:
    """Collect display metadata using configured node-kind precedence."""
    precedence = {
        str(node_kind): index
        for index, node_kind in enumerate(
            policy["node_kind_precedence"]
        )
    }
    metadata: dict[str, dict[str, str]] = {}
    for row in candidates.to_dict("records"):
        for endpoint in ["start", "end"]:
            node_id = str(row[f"{endpoint}_node_id"])
            node_metadata = {
                "node_kind": str(row[f"{endpoint}_node_kind"]),
                "display_name": str(row[f"{endpoint}_display_name"]),
                "entity_type": str(row[f"{endpoint}_entity_type"]),
            }
            existing = metadata.get(node_id)
            if existing is None:
                metadata[node_id] = node_metadata
                continue
            existing_priority = precedence.get(
                existing["node_kind"],
                len(precedence),
            )
            candidate_priority = precedence.get(
                node_metadata["node_kind"],
                len(precedence),
            )
            if candidate_priority < existing_priority:
                metadata[node_id] = node_metadata
    return metadata


def build_entity_mapping(
    entity_review: pd.DataFrame,
    selected_node_ids: set[str],
    all_node_ids: set[str],
    policy: dict[str, object],
) -> tuple[dict[str, str | None], dict[str, int]]:
    """Validate human entity decisions and resolve merge chains."""
    decisions = policy["entity_review_decisions"]
    keep_decision = str(decisions["keep"])
    merge_decision = str(decisions["merge"])
    link_decision = str(decisions["link"])
    exclude_decision = str(decisions["exclude"])
    allowed_decisions = {
        keep_decision,
        merge_decision,
        link_decision,
        exclude_decision,
    }
    direct_mapping: dict[str, str | None] = {}
    pending_node_ids: list[str] = []
    invalid_rows: list[str] = []
    for row in entity_review.to_dict("records"):
        node_id = str(row["node_id"])
        if node_id not in selected_node_ids:
            continue
        decision = str(row["review_decision"]).strip()
        target_node_id = str(row["review_target_node_id"]).strip()
        if not decision:
            pending_node_ids.append(node_id)
            continue
        if decision not in allowed_decisions:
            invalid_rows.append(str(row["entity_review_id"]))
            continue
        if decision == keep_decision:
            direct_mapping[node_id] = node_id
        elif decision == exclude_decision:
            direct_mapping[node_id] = None
        elif decision in {merge_decision, link_decision}:
            if (
                not target_node_id
                or target_node_id == node_id
                or target_node_id not in all_node_ids
            ):
                invalid_rows.append(str(row["entity_review_id"]))
                continue
            direct_mapping[node_id] = target_node_id
    if pending_node_ids:
        raise ValueError(
            "Entity review is pending for selected nodes: "
            + ", ".join(sorted(set(pending_node_ids))[:20])
        )
    if invalid_rows:
        raise ValueError(
            "Invalid entity review rows: "
            + ", ".join(sorted(set(invalid_rows))[:20])
        )

    resolved_mapping: dict[str, str | None] = {}
    for source_node_id in direct_mapping:
        visited: set[str] = set()
        current_node_id: str | None = source_node_id
        while (
            current_node_id is not None
            and current_node_id in direct_mapping
        ):
            if current_node_id in visited:
                raise ValueError(
                    "Entity merge cycle found at node: "
                    + current_node_id
                )
            visited.add(current_node_id)
            next_node_id = direct_mapping[current_node_id]
            if next_node_id == current_node_id:
                break
            current_node_id = next_node_id
        resolved_mapping[source_node_id] = current_node_id
    statistics = {
        "reviewed_entity_mapping_count": len(resolved_mapping),
        "excluded_entity_count": sum(
            value is None for value in resolved_mapping.values()
        ),
        "redirected_entity_count": sum(
            value is not None and key != value
            for key, value in resolved_mapping.items()
        ),
    }
    return resolved_mapping, statistics


def select_relation_candidates(
    candidates: pd.DataFrame,
    relation_review: pd.DataFrame,
    policy: dict[str, object],
    mode: str,
) -> tuple[pd.DataFrame, list[dict[str, str]], dict[str, int]]:
    """Select trusted facts and, when requested, approved NLP facts."""
    trusted_tiers = {
        str(value) for value in policy["trusted_candidate_tiers"]
    }
    reviewed_tiers = {
        str(value) for value in policy["reviewed_candidate_tiers"]
    }
    stable_node_kinds = {
        str(value)
        for value in policy["review_routing"]["stable_node_kinds"]
    }
    trust_policy = policy["trust_policy"]
    verified_status = str(trust_policy["verified_status"])
    reviewed_status = str(trust_policy["reviewed_status"])
    provisional_status = str(trust_policy["provisional_status"])
    trusted = candidates[
        candidates["candidate_tier"].isin(trusted_tiers)
    ].copy()
    trusted["load_trust_status"] = verified_status
    exclusions: list[dict[str, str]] = []
    if mode == "trusted_only":
        for row in candidates[
            candidates["candidate_tier"].isin(reviewed_tiers)
        ].to_dict("records"):
            exclusions.append(
                {
                    "fact_graph_candidate_id": str(
                        row["fact_graph_candidate_id"]
                    ),
                    "reason": "MODE_EXCLUDED_REVIEWED_TIER",
                }
            )
        return trusted, exclusions, {
            "trusted_selected_count": len(trusted),
            "provisional_selected_count": 0,
            "reviewed_approved_count": 0,
            "reviewed_rejected_count": 0,
            "deferred_endpoint_relation_count": int(
                (
                    candidates["candidate_tier"].isin(reviewed_tiers)
                    & (
                        ~candidates["start_node_kind"].isin(
                            stable_node_kinds
                        )
                        | ~candidates["end_node_kind"].isin(
                            stable_node_kinds
                        )
                    )
                ).sum()
            ),
        }

    if mode == "trusted_and_provisional":
        reviewed_candidates = candidates[
            candidates["candidate_tier"].isin(reviewed_tiers)
        ].copy()
        unresolved_endpoint_mask = (
            ~reviewed_candidates["start_node_kind"].isin(
                stable_node_kinds
            )
            | ~reviewed_candidates["end_node_kind"].isin(
                stable_node_kinds
            )
        )
        provisional = reviewed_candidates[
            unresolved_endpoint_mask
        ].copy()
        provisional["load_trust_status"] = provisional_status
        for row in reviewed_candidates[
            ~unresolved_endpoint_mask
        ].to_dict("records"):
            exclusions.append(
                {
                    "fact_graph_candidate_id": str(
                        row["fact_graph_candidate_id"]
                    ),
                    "reason": "RELATION_REVIEW_REQUIRED",
                }
            )
        selected = pd.concat(
            [trusted, provisional],
            ignore_index=True,
        )
        return selected, exclusions, {
            "trusted_selected_count": len(trusted),
            "provisional_selected_count": len(provisional),
            "reviewed_approved_count": 0,
            "reviewed_rejected_count": 0,
            "deferred_endpoint_relation_count": 0,
        }

    review_by_candidate_id = {
        str(row["fact_graph_candidate_id"]): row
        for row in relation_review.to_dict("records")
    }
    approve_decision = str(
        policy["relation_review_decisions"]["approve"]
    )
    reject_decision = str(
        policy["relation_review_decisions"]["reject"]
    )
    approved_rows: list[dict[str, object]] = []
    pending_ids: list[str] = []
    invalid_ids: list[str] = []
    rejected_count = 0
    deferred_endpoint_count = 0
    for row in candidates[
        candidates["candidate_tier"].isin(reviewed_tiers)
    ].to_dict("records"):
        candidate_id = str(row["fact_graph_candidate_id"])
        if (
            str(row["start_node_kind"]) not in stable_node_kinds
            or str(row["end_node_kind"]) not in stable_node_kinds
        ):
            deferred_endpoint_count += 1
            exclusions.append(
                {
                    "fact_graph_candidate_id": candidate_id,
                    "reason": str(
                        policy["review_routing"][
                            "deferred_relation_reason"
                        ]
                    ),
                }
            )
            continue
        review = review_by_candidate_id.get(candidate_id)
        decision = ""
        if review is not None:
            decision = str(review["review_decision"]).strip()
        if not decision:
            pending_ids.append(candidate_id)
        elif decision == approve_decision:
            approved_row = dict(row)
            approved_row["load_trust_status"] = reviewed_status
            approved_rows.append(approved_row)
        elif decision == reject_decision:
            rejected_count += 1
            exclusions.append(
                {
                    "fact_graph_candidate_id": candidate_id,
                    "reason": "HUMAN_REJECTED_RELATION",
                }
            )
        elif decision not in {approve_decision, reject_decision}:
            invalid_ids.append(candidate_id)
    if pending_ids:
        raise ValueError(
            "Relation review is pending for candidate IDs: "
            + ", ".join(sorted(pending_ids)[:20])
        )
    if invalid_ids:
        raise ValueError(
            "Invalid relation review decisions for candidate IDs: "
            + ", ".join(sorted(invalid_ids)[:20])
        )
    approved = pd.DataFrame(
        approved_rows,
        columns=[*candidates.columns, "load_trust_status"],
    )
    selected = pd.concat([trusted, approved], ignore_index=True)
    return selected, exclusions, {
        "trusted_selected_count": len(trusted),
        "provisional_selected_count": 0,
        "reviewed_approved_count": len(approved),
        "reviewed_rejected_count": rejected_count,
        "deferred_endpoint_relation_count": (
            deferred_endpoint_count
        ),
    }


def build_load_tables(
    candidates: pd.DataFrame,
    entity_review: pd.DataFrame,
    relation_review: pd.DataFrame,
    policy: dict[str, object],
    mode: str,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """Project reviewed candidates into idempotent Neo4j node and edge rows."""
    if candidates["fact_graph_candidate_id"].duplicated().any():
        raise ValueError("Duplicate fact_graph_candidate_id values found.")
    node_metadata = collect_node_metadata(candidates, policy)
    selected, exclusions, selection_statistics = (
        select_relation_candidates(
            candidates,
            relation_review,
            policy,
            mode,
        )
    )
    selected_node_ids = set(selected["start_node_id"]).union(
        set(selected["end_node_id"])
    )
    entity_mapping, entity_statistics = build_entity_mapping(
        entity_review,
        selected_node_ids,
        set(node_metadata),
        policy,
    )

    semantic_relations: dict[
        tuple[str, str, str],
        dict[str, object],
    ] = {}
    mapping_review_rows: list[dict[str, object]] = []
    excluded_entity_relationship_count = 0
    mapped_relationship_count = 0
    self_relation_after_mapping_count = 0
    missing_evidence_candidate_count = 0
    evidence_policy = policy["evidence_policy"]
    require_evidence_ids = bool(
        evidence_policy["require_evidence_ids"]
    )
    require_complete_metadata = bool(
        evidence_policy["require_complete_metadata"]
    )
    self_relation_mapping_status = str(
        policy["mapping_review_statuses"]["self_relation"]
    )
    trust_policy = policy["trust_policy"]
    verified_status = str(trust_policy["verified_status"])
    reviewed_status = str(trust_policy["reviewed_status"])
    provisional_status = str(trust_policy["provisional_status"])
    default_retrieval_statuses = {
        str(value)
        for value in trust_policy["default_retrieval_statuses"]
    }
    for row in selected.to_dict("records"):
        candidate_id = str(row["fact_graph_candidate_id"])
        evidence_ids = parse_json_list(
            row.get("evidence_ids_json", "[]")
        )
        evidence_records = parse_json_records(
            row.get("evidence_records_json", "[]")
        )
        evidence_records_by_id = {
            record.get("evidence_id", ""): record
            for record in evidence_records
            if record.get("evidence_id", "")
        }
        evidence_metadata_complete = str(
            row.get("evidence_metadata_complete", "")
        ).casefold() == "true"
        evidence_is_missing = (
            require_evidence_ids and not evidence_ids
        )
        if require_complete_metadata:
            evidence_is_missing = evidence_is_missing or (
                set(evidence_ids) != set(evidence_records_by_id)
                or not evidence_metadata_complete
                or any(
                    not (
                        record.get("source_record_id", "")
                        or record.get("source_document_id", "")
                        or record.get("source_url", "")
                        or record.get("source_text", "")
                    )
                    for record in evidence_records
                )
            )
        if evidence_is_missing:
            missing_evidence_candidate_count += 1
            exclusions.append(
                {
                    "fact_graph_candidate_id": candidate_id,
                    "reason": "MISSING_OR_INCOMPLETE_EVIDENCE",
                }
            )
            continue
        original_start_node_id = str(row["start_node_id"])
        original_end_node_id = str(row["end_node_id"])
        start_node_id = entity_mapping.get(
            original_start_node_id,
            original_start_node_id,
        )
        end_node_id = entity_mapping.get(
            original_end_node_id,
            original_end_node_id,
        )
        if start_node_id is None or end_node_id is None:
            excluded_entity_relationship_count += 1
            exclusions.append(
                {
                    "fact_graph_candidate_id": candidate_id,
                    "reason": "EXCLUDED_ENTITY_ENDPOINT",
                }
            )
            continue
        relation_type = str(row["relation_type"])
        if start_node_id == end_node_id:
            self_relation_after_mapping_count += 1
            exclusions.append(
                {
                    "fact_graph_candidate_id": candidate_id,
                    "reason": self_relation_mapping_status,
                }
            )
            mapping_review_rows.append(
                {
                    "mapping_review_id": (
                        "mapping-review:"
                        + sha256(
                            candidate_id.encode("utf-8")
                        ).hexdigest()[:24]
                    ),
                    "fact_graph_candidate_id": candidate_id,
                    "original_start_node_id": original_start_node_id,
                    "original_end_node_id": original_end_node_id,
                    "mapped_node_id": start_node_id,
                    "relation_type": relation_type,
                    "candidate_tier": str(row["candidate_tier"]),
                    "source_datasets_json": str(
                        row["source_datasets_json"]
                    ),
                    "evidence_ids_json": str(
                        row.get("evidence_ids_json", "[]")
                    ),
                    "review_reason": self_relation_mapping_status,
                    "review_decision": "",
                    "review_note": "",
                }
            )
            continue
        entity_mapping_applied = (
            start_node_id != original_start_node_id
            or end_node_id != original_end_node_id
        )
        if entity_mapping_applied:
            mapped_relationship_count += 1
        relation_key = (
            start_node_id,
            relation_type,
            end_node_id,
        )
        if relation_key not in semantic_relations:
            semantic_relations[relation_key] = {
                "fact_graph_candidate_ids": set(),
                "candidate_tiers": set(),
                "source_datasets": set(),
                "evidence_ids": set(),
                "evidence_records": {},
                "verification_statuses": set(),
                "load_trust_statuses": set(),
                "assertion_records": [],
                "mapped_assertion_count": 0,
            }
        semantic_relation = semantic_relations[relation_key]
        semantic_relation["fact_graph_candidate_ids"].add(candidate_id)
        semantic_relation["candidate_tiers"].add(
            str(row["candidate_tier"])
        )
        source_datasets = parse_json_list(
            row["source_datasets_json"]
        )
        anchor_exam_term_ids = parse_json_list(
            row.get("anchor_exam_term_ids_json", "[]")
        )
        semantic_relation["source_datasets"].update(source_datasets)
        semantic_relation["evidence_ids"].update(evidence_ids)
        semantic_relation["evidence_records"].update(
            evidence_records_by_id
        )
        verification_status = str(
            row.get("verification_status", "")
        )
        if verification_status:
            semantic_relation["verification_statuses"].add(
                verification_status
            )
        load_trust_status = str(row["load_trust_status"])
        semantic_relation["load_trust_statuses"].add(
            load_trust_status
        )
        semantic_relation["assertion_records"].append(
            {
                "fact_graph_candidate_id": candidate_id,
                "original_start_node_id": original_start_node_id,
                "original_end_node_id": original_end_node_id,
                "entity_mapping_applied": entity_mapping_applied,
                "candidate_tier": str(row["candidate_tier"]),
                "relation_origin": str(
                    row.get("relation_origin", "")
                ),
                "source_datasets": source_datasets,
                "evidence_ids": evidence_ids,
                "evidence_count": str(
                    row.get("evidence_count", "")
                ),
                "anchor_exam_term_ids": anchor_exam_term_ids,
                "verification_status": verification_status,
                "load_trust_status": load_trust_status,
            }
        )
        if entity_mapping_applied:
            semantic_relation["mapped_assertion_count"] += 1

    node_relation_counts: dict[str, int] = defaultdict(int)
    node_assertion_counts: dict[str, int] = defaultdict(int)
    fact_rows: list[dict[str, object]] = []
    evidence_by_id: dict[str, dict[str, str]] = {}
    evidence_fact_ids: dict[str, set[str]] = defaultdict(set)
    for relation_key, semantic_relation in sorted(
        semantic_relations.items()
    ):
        start_node_id, relation_type, end_node_id = relation_key
        fact_id = make_load_id(
            start_node_id,
            relation_type,
            end_node_id,
        )
        assertion_records = sorted(
            semantic_relation["assertion_records"],
            key=lambda item: str(item["fact_graph_candidate_id"]),
        )
        assertion_count = len(assertion_records)
        load_trust_statuses = set(
            semantic_relation["load_trust_statuses"]
        )
        if verified_status in load_trust_statuses:
            fact_trust_status = verified_status
        elif reviewed_status in load_trust_statuses:
            fact_trust_status = reviewed_status
        elif provisional_status in load_trust_statuses:
            fact_trust_status = provisional_status
        else:
            raise ValueError(
                "Fact has no configured load trust status: "
                + fact_id
            )
        node_relation_counts[start_node_id] += 1
        node_relation_counts[end_node_id] += 1
        node_assertion_counts[start_node_id] += assertion_count
        node_assertion_counts[end_node_id] += assertion_count
        for evidence_id in sorted(semantic_relation["evidence_ids"]):
            evidence_record = dict(
                semantic_relation["evidence_records"][evidence_id]
            )
            evidence_record["evidence_id"] = evidence_id
            existing_evidence = evidence_by_id.get(evidence_id)
            if existing_evidence is None:
                evidence_by_id[evidence_id] = evidence_record
            elif existing_evidence is not None:
                for field, value in evidence_record.items():
                    existing_value = existing_evidence.get(field, "")
                    if not existing_value and value:
                        existing_evidence[field] = value
                    elif (
                        field
                        in {
                            "source_record_id",
                            "source_document_id",
                            "source_text",
                            "evidence_kind",
                        }
                        and existing_value
                        and value
                        and existing_value != value
                    ):
                        raise ValueError(
                            "Conflicting metadata for evidence ID: "
                            + evidence_id
                        )
            evidence_fact_ids[evidence_id].add(fact_id)
        fact_rows.append(
            {
                "fact_id": fact_id,
                "subject_node_id": start_node_id,
                "predicate": relation_type,
                "object_node_id": end_node_id,
                "assertion_count": assertion_count,
                "fact_graph_candidate_ids_json": dumps(
                    sorted(
                        semantic_relation[
                            "fact_graph_candidate_ids"
                        ]
                    ),
                    ensure_ascii=False,
                ),
                "candidate_tiers_json": dumps(
                    sorted(semantic_relation["candidate_tiers"]),
                    ensure_ascii=False,
                ),
                "source_datasets_json": dumps(
                    sorted(semantic_relation["source_datasets"]),
                    ensure_ascii=False,
                ),
                "evidence_ids_json": dumps(
                    sorted(semantic_relation["evidence_ids"]),
                    ensure_ascii=False,
                ),
                "verification_statuses_json": dumps(
                    sorted(
                        semantic_relation["verification_statuses"]
                    ),
                    ensure_ascii=False,
                ),
                "assertion_records_json": dumps(
                    assertion_records,
                    ensure_ascii=False,
                ),
                "mapped_assertion_count": int(
                    semantic_relation["mapped_assertion_count"]
                ),
                "trust_status": fact_trust_status,
                "default_retrieval_eligible": (
                    fact_trust_status in default_retrieval_statuses
                ),
            }
        )
    loaded_assertion_count = sum(
        int(row["assertion_count"]) for row in fact_rows
    )
    accounted_assertion_count = (
        loaded_assertion_count
        + excluded_entity_relationship_count
        + self_relation_after_mapping_count
        + missing_evidence_candidate_count
    )
    if accounted_assertion_count != len(selected):
        raise ValueError(
            "Original relationship assertions were lost during mapping."
        )
    facts = pd.DataFrame(
        fact_rows,
        columns=[
            "fact_id",
            "subject_node_id",
            "predicate",
            "object_node_id",
            "assertion_count",
            "fact_graph_candidate_ids_json",
            "candidate_tiers_json",
            "source_datasets_json",
            "evidence_ids_json",
            "verification_statuses_json",
            "assertion_records_json",
            "mapped_assertion_count",
            "trust_status",
            "default_retrieval_eligible",
        ],
    )
    evidence_rows: list[dict[str, object]] = []
    for evidence_id, evidence_record in sorted(evidence_by_id.items()):
        evidence_rows.append(
            {
                "evidence_id": evidence_id,
                "source_record_id": evidence_record.get(
                    "source_record_id",
                    "",
                ),
                "source_dataset": evidence_record.get(
                    "source_dataset",
                    "",
                ),
                "source_document_id": evidence_record.get(
                    "source_document_id",
                    "",
                ),
                "source_url": evidence_record.get("source_url", ""),
                "source_text": evidence_record.get("source_text", ""),
                "evidence_kind": evidence_record.get(
                    "evidence_kind",
                    "",
                ),
                "source_release": evidence_record.get(
                    "source_release",
                    "",
                ),
                "evidence_urls_json": evidence_record.get(
                    "evidence_urls_json",
                    "[]",
                ),
                "detail_urls_json": evidence_record.get(
                    "detail_urls_json",
                    "[]",
                ),
                "scopes_json": evidence_record.get(
                    "scopes_json",
                    "[]",
                ),
                "supported_fact_count": len(
                    evidence_fact_ids[evidence_id]
                ),
            }
        )
    evidence = pd.DataFrame(
        evidence_rows,
        columns=[
            "evidence_id",
            "source_record_id",
            "source_dataset",
            "source_document_id",
            "source_url",
            "source_text",
            "evidence_kind",
            "source_release",
            "evidence_urls_json",
            "detail_urls_json",
            "scopes_json",
            "supported_fact_count",
        ],
    )
    mapping_review = pd.DataFrame(
        mapping_review_rows,
        columns=[
            "mapping_review_id",
            "fact_graph_candidate_id",
            "original_start_node_id",
            "original_end_node_id",
            "mapped_node_id",
            "relation_type",
            "candidate_tier",
            "source_datasets_json",
            "evidence_ids_json",
            "review_reason",
            "review_decision",
            "review_note",
        ],
    )

    loaded_node_ids = set(facts["subject_node_id"]).union(
        set(facts["object_node_id"])
    )
    evidence_source_ids = {
        str(value)
        for value in evidence["source_record_id"]
        if str(value)
    }
    loaded_node_ids.update(evidence_source_ids)
    node_rows: list[dict[str, object]] = []
    for node_id in sorted(loaded_node_ids):
        metadata = node_metadata.get(node_id)
        if metadata is None:
            if node_id not in evidence_source_ids:
                raise ValueError(
                    "Mapped target node metadata is missing: " + node_id
                )
            metadata = {
                "node_kind": "SOURCE_RECORD",
                "display_name": node_id,
                "entity_type": "SourceRecord",
            }
        node_rows.append(
            {
                "node_id": node_id,
                "node_kind": metadata["node_kind"],
                "display_name": metadata["display_name"],
                "entity_type": metadata["entity_type"],
                "relationship_count": node_relation_counts[node_id],
                "relationship_assertion_count": (
                    node_assertion_counts[node_id]
                ),
                "resolution_status": (
                    provisional_status
                    if metadata["node_kind"]
                    in {
                        str(value)
                        for value in policy["review_routing"][
                            "unresolved_node_kinds"
                        ]
                    }
                    else verified_status
                ),
            }
        )
    nodes = pd.DataFrame(
        node_rows,
        columns=[
            "node_id",
            "node_kind",
            "display_name",
            "entity_type",
            "relationship_count",
            "relationship_assertion_count",
            "resolution_status",
        ],
    )
    exclusion_table = pd.DataFrame(
        exclusions,
        columns=["fact_graph_candidate_id", "reason"],
    )
    schema = policy["neo4j"]["schema"]
    relationship_types = schema["relationship_types"]
    entity_label = str(schema["entity_base_label"])
    fact_label = str(schema["fact_label"])
    evidence_label = str(schema["evidence_label"])
    graph_relationships: dict[
        tuple[str, str, str, str, str],
        dict[str, str],
    ] = {}
    for row in facts.to_dict("records"):
        fact_id = str(row["fact_id"])
        for start_label, start_id, relation_type, end_label, end_id in [
            (
                fact_label,
                fact_id,
                str(relationship_types["subject"]),
                entity_label,
                str(row["subject_node_id"]),
            ),
            (
                fact_label,
                fact_id,
                str(relationship_types["object"]),
                entity_label,
                str(row["object_node_id"]),
            ),
        ]:
            relationship_key = (
                start_label,
                start_id,
                relation_type,
                end_label,
                end_id,
            )
            graph_relationships[relationship_key] = {
                "graph_relationship_id": (
                    make_graph_relationship_id(*relationship_key)
                ),
                "start_label": start_label,
                "start_id": start_id,
                "relation_type": relation_type,
                "end_label": end_label,
                "end_id": end_id,
            }
        for evidence_id in parse_json_list(
            row["evidence_ids_json"]
        ):
            relationship_key = (
                fact_label,
                fact_id,
                str(relationship_types["supported_by"]),
                evidence_label,
                evidence_id,
            )
            graph_relationships[relationship_key] = {
                "graph_relationship_id": (
                    make_graph_relationship_id(*relationship_key)
                ),
                "start_label": fact_label,
                "start_id": fact_id,
                "relation_type": str(
                    relationship_types["supported_by"]
                ),
                "end_label": evidence_label,
                "end_id": evidence_id,
            }
    for row in evidence.to_dict("records"):
        source_record_id = str(row["source_record_id"])
        if not source_record_id:
            continue
        relationship_key = (
            evidence_label,
            str(row["evidence_id"]),
            str(relationship_types["from_source"]),
            entity_label,
            source_record_id,
        )
        graph_relationships[relationship_key] = {
            "graph_relationship_id": (
                make_graph_relationship_id(*relationship_key)
            ),
            "start_label": evidence_label,
            "start_id": str(row["evidence_id"]),
            "relation_type": str(
                relationship_types["from_source"]
            ),
            "end_label": entity_label,
            "end_id": source_record_id,
        }
    relationships = pd.DataFrame(
        list(graph_relationships.values()),
        columns=[
            "graph_relationship_id",
            "start_label",
            "start_id",
            "relation_type",
            "end_label",
            "end_id",
        ],
    )
    if facts["fact_id"].duplicated().any():
        raise ValueError("Duplicate Fact IDs found.")
    if evidence["evidence_id"].duplicated().any():
        raise ValueError("Duplicate EvidenceSpan IDs found.")
    if relationships["graph_relationship_id"].duplicated().any():
        raise ValueError("Duplicate graph relationship IDs found.")
    entity_ids = set(nodes["node_id"])
    missing_entity_ids = set(facts["subject_node_id"]).union(
        set(facts["object_node_id"])
    ).difference(entity_ids)
    missing_evidence_ids = {
        evidence_id
        for value in facts["evidence_ids_json"]
        for evidence_id in parse_json_list(value)
    }.difference(set(evidence["evidence_id"]))
    if missing_entity_ids or missing_evidence_ids:
        raise ValueError(
            "The Fact graph contains a missing endpoint or evidence node."
        )
    statistics: dict[str, object] = {
        **selection_statistics,
        **entity_statistics,
        "input_candidate_count": len(candidates),
        "selected_candidate_count": len(selected),
        "relationship_assertion_count_before_mapping": len(selected),
        "explicitly_excluded_relationship_count": (
            excluded_entity_relationship_count
        ),
        "self_relation_mapping_review_count": len(mapping_review),
        "loaded_relationship_assertion_count": loaded_assertion_count,
        "accounted_relationship_assertion_count": (
            accounted_assertion_count
        ),
        "relationship_assertion_count_preserved": (
            accounted_assertion_count == len(selected)
        ),
        "mapped_relationship_count": mapped_relationship_count,
        "semantic_relationship_merge_count": (
            loaded_assertion_count - len(facts)
        ),
        "load_node_count": len(nodes),
        "load_fact_count": len(facts),
        "load_evidence_count": len(evidence),
        "load_relationship_count": len(facts),
        "verified_fact_count": int(
            facts["trust_status"].eq(verified_status).sum()
        ),
        "reviewed_fact_count": int(
            facts["trust_status"].eq(reviewed_status).sum()
        ),
        "provisional_fact_count": int(
            facts["trust_status"].eq(provisional_status).sum()
        ),
        "default_retrieval_fact_count": int(
            facts["default_retrieval_eligible"].sum()
        ),
        "provisional_node_count": int(
            nodes["resolution_status"].eq(provisional_status).sum()
        ),
        "load_graph_relationship_count": len(relationships),
        "excluded_candidate_count": len(exclusion_table),
        "missing_evidence_candidate_count": (
            missing_evidence_candidate_count
        ),
        "self_relation_after_mapping_count": (
            self_relation_after_mapping_count
        ),
        "missing_endpoint_count": len(missing_entity_ids),
        "missing_evidence_node_count": len(missing_evidence_ids),
        "mode": mode,
    }
    return {
        "nodes": nodes,
        "facts": facts,
        "evidence": evidence,
        "relationships": relationships,
        "mapping_review": mapping_review,
        "exclusions": exclusion_table,
    }, statistics


def load_to_neo4j(
    tables: dict[str, pd.DataFrame],
    policy: dict[str, object],
) -> dict[str, int]:
    """Idempotently upsert Entity, Fact, and EvidenceSpan tables."""
    try:
        from neo4j import GraphDatabase
    except ImportError as error:
        raise RuntimeError(
            "The neo4j package is required for --execute-neo4j."
        ) from error

    neo4j_policy = policy["neo4j"]
    project_root = Path(__file__).resolve().parents[3]
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env")
    uri = os.environ.get(
        str(neo4j_policy["uri_environment_variable"]),
        str(neo4j_policy["default_uri"]),
    )
    username = os.environ.get(
        str(neo4j_policy["username_environment_variable"]),
        str(neo4j_policy["default_username"]),
    )
    password = os.environ.get(
        str(neo4j_policy["password_environment_variable"]),
        "",
    )
    if not password:
        password = os.environ.get(
            str(
                neo4j_policy[
                    "password_fallback_environment_variable"
                ]
            ),
            "",
        )
    database = os.environ.get(
        str(neo4j_policy["database_environment_variable"]),
        str(neo4j_policy["default_database"]),
    )
    if not uri or not username or not password:
        raise RuntimeError(
            "Neo4j URI, username, and password environment variables "
            "must be configured."
        )
    schema = neo4j_policy["schema"]
    entity_label = str(schema["entity_base_label"])
    fact_label = str(schema["fact_label"])
    evidence_label = str(schema["evidence_label"])
    node_kind_labels = {
        str(key): str(value)
        for key, value in schema["node_kind_labels"].items()
    }
    configured_labels = {
        entity_label,
        fact_label,
        evidence_label,
        *node_kind_labels.values(),
    }
    for label in configured_labels:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", label) is None:
            raise ValueError("Unsafe Neo4j node label: " + label)
    batch_size = int(neo4j_policy["batch_size"])
    nodes = tables["nodes"]
    facts = tables["facts"]
    evidence = tables["evidence"]
    relationships = tables["relationships"]
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        with driver.session(database=database) as session:
            constraints = [
                (
                    str(schema["constraint_names"]["entity"]),
                    entity_label,
                    "node_id",
                ),
                (
                    str(schema["constraint_names"]["fact"]),
                    fact_label,
                    "fact_id",
                ),
                (
                    str(schema["constraint_names"]["evidence"]),
                    evidence_label,
                    "evidence_id",
                ),
            ]
            for constraint_name, label, property_name in constraints:
                if (
                    re.fullmatch(
                        r"[A-Za-z][A-Za-z0-9_]*",
                        constraint_name,
                    )
                    is None
                ):
                    raise ValueError(
                        "Unsafe Neo4j constraint name: "
                        + constraint_name
                    )
                session.run(
                    (
                        f"CREATE CONSTRAINT {constraint_name} "
                        f"IF NOT EXISTS FOR (n:{label}) "
                        f"REQUIRE n.{property_name} IS UNIQUE"
                    )
                ).consume()
            for node_kind, group in nodes.groupby(
                "node_kind",
                sort=True,
            ):
                kind_label = node_kind_labels.get(str(node_kind))
                if kind_label is None:
                    raise ValueError(
                        "No Neo4j label configured for node kind: "
                        + str(node_kind)
                    )
                node_query = (
                    f"UNWIND $rows AS row "
                    f"MERGE (n:{entity_label} "
                    "{node_id: row.node_id}) "
                    f"SET n:{kind_label}, "
                    "n.node_kind = row.node_kind, "
                    "n.display_name = row.display_name, "
                    "n.entity_type = row.entity_type, "
                    "n.relationship_count = row.relationship_count, "
                    "n.relationship_assertion_count = "
                    "row.relationship_assertion_count, "
                    "n.resolution_status = row.resolution_status"
                )
                node_rows = group.to_dict("records")
                for offset in range(0, len(node_rows), batch_size):
                    session.run(
                        node_query,
                        rows=node_rows[offset : offset + batch_size],
                    ).consume()
            fact_query = (
                f"UNWIND $rows AS row "
                f"MERGE (f:{fact_label} {{fact_id: row.fact_id}}) "
                "SET f.predicate = row.predicate, "
                "f.assertion_count = row.assertion_count, "
                "f.fact_graph_candidate_ids_json = "
                "row.fact_graph_candidate_ids_json, "
                "f.candidate_tiers_json = row.candidate_tiers_json, "
                "f.source_datasets_json = row.source_datasets_json, "
                "f.evidence_ids_json = row.evidence_ids_json, "
                "f.verification_statuses_json = "
                "row.verification_statuses_json, "
                "f.assertion_records_json = "
                "row.assertion_records_json, "
                "f.mapped_assertion_count = row.mapped_assertion_count, "
                "f.trust_status = row.trust_status, "
                "f.default_retrieval_eligible = "
                "row.default_retrieval_eligible"
            )
            fact_rows = facts.to_dict("records")
            for offset in range(0, len(fact_rows), batch_size):
                session.run(
                    fact_query,
                    rows=fact_rows[offset : offset + batch_size],
                ).consume()
            evidence_query = (
                f"UNWIND $rows AS row "
                f"MERGE (e:{evidence_label} "
                "{evidence_id: row.evidence_id}) "
                "SET e.source_record_id = row.source_record_id, "
                "e.source_dataset = row.source_dataset, "
                "e.source_document_id = row.source_document_id, "
                "e.source_url = row.source_url, "
                "e.source_text = row.source_text, "
                "e.evidence_kind = row.evidence_kind, "
                "e.source_release = row.source_release, "
                "e.evidence_urls_json = row.evidence_urls_json, "
                "e.detail_urls_json = row.detail_urls_json, "
                "e.scopes_json = row.scopes_json, "
                "e.supported_fact_count = row.supported_fact_count"
            )
            evidence_rows = evidence.to_dict("records")
            for offset in range(0, len(evidence_rows), batch_size):
                session.run(
                    evidence_query,
                    rows=evidence_rows[offset : offset + batch_size],
                ).consume()
            relationship_groups = relationships.groupby(
                [
                    "start_label",
                    "relation_type",
                    "end_label",
                ],
                sort=True,
            )
            for (
                start_label,
                relation_type,
                end_label,
            ), group in relationship_groups:
                if (
                    re.fullmatch(
                        r"[A-Z][A-Z0-9_]*",
                        str(relation_type),
                    )
                    is None
                ):
                    raise ValueError(
                        "Unsafe Neo4j relationship type: "
                        + str(relation_type)
                    )
                start_property = "node_id"
                if str(start_label) == fact_label:
                    start_property = "fact_id"
                elif str(start_label) == evidence_label:
                    start_property = "evidence_id"
                end_property = "node_id"
                if str(end_label) == fact_label:
                    end_property = "fact_id"
                elif str(end_label) == evidence_label:
                    end_property = "evidence_id"
                relationship_query = (
                    f"UNWIND $rows AS row "
                    f"MATCH (s:{start_label} "
                    f"{{{start_property}: row.start_id}}) "
                    f"MATCH (e:{end_label} "
                    f"{{{end_property}: row.end_id}}) "
                    f"MERGE (s)-[r:{relation_type} "
                    "{graph_relationship_id: "
                    "row.graph_relationship_id}]->(e)"
                )
                relation_rows = group.to_dict("records")
                for offset in range(0, len(relation_rows), batch_size):
                    session.run(
                        relationship_query,
                        rows=relation_rows[
                            offset : offset + batch_size
                        ],
                    ).consume()
    finally:
        driver.close()
    return {
        "loaded_node_count": len(nodes),
        "loaded_fact_count": len(facts),
        "loaded_evidence_count": len(evidence),
        "loaded_relationship_count": len(relationships),
    }


def read_review_csv(path: str) -> pd.DataFrame:
    """Read a review CSV and fail clearly when it is absent."""
    review_path = Path(path)
    if not review_path.is_file():
        return pd.DataFrame()
    return pd.read_csv(
        review_path,
        dtype=str,
        keep_default_na=False,
    )


def run_fact_graph_load_pipeline(cli_args: Namespace) -> dict[str, object]:
    """Validate review state, write load tables, and optionally load Neo4j."""
    with Path(cli_args.config).open("r", encoding="utf-8") as input_file:
        policy = load(input_file)
    candidates = pd.read_csv(
        cli_args.candidate_csv,
        dtype=str,
        keep_default_na=False,
    )
    supplied_entity_review = read_review_csv(cli_args.entity_review_csv)
    supplied_relation_review = read_review_csv(cli_args.relation_review_csv)
    if cli_args.mode == "reviewed_all":
        validated_reviews, _ = build_review_tables(
            candidates,
            policy,
            supplied_entity_review,
            supplied_relation_review,
        )
        entity_review = validated_reviews["entity_review"]
        relation_review = validated_reviews["relation_review"]
    elif cli_args.mode in {
        "trusted_only",
        "trusted_and_provisional",
    }:
        entity_review = pd.DataFrame()
        relation_review = pd.DataFrame()
    tables, statistics = build_load_tables(
        candidates,
        entity_review,
        relation_review,
        policy,
        cli_args.mode,
    )
    output_directory = Path(cli_args.output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_names = policy["load_outputs"]
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = output_directory / str(output_names[table_name])
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path.resolve())

    neo4j_statistics: dict[str, int] = {}
    if cli_args.execute_neo4j:
        neo4j_statistics = load_to_neo4j(
            tables,
            policy,
        )
    status = "LOAD_PLAN_READY"
    if cli_args.execute_neo4j:
        status = "COMPLETED"
    manifest = {
        "status": status,
        "stage": "FACT_GRAPH_LOAD",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": str(policy["policy_version"]),
        "neo4j_load": bool(cli_args.execute_neo4j),
        "statistics": {
            **statistics,
            **neo4j_statistics,
        },
        "output_paths": output_paths,
    }
    manifest_path = output_directory / str(output_names["manifest"])
    with manifest_path.open("w", encoding="utf-8") as output_file:
        dump(manifest, output_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path.resolve())
    return manifest


def main() -> None:
    """Run the fact graph load pipeline."""
    result = run_fact_graph_load_pipeline(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

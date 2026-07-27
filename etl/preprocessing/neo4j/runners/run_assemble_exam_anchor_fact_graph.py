from __future__ import annotations

import _bootstrap

from argparse import ArgumentParser, Namespace
from collections import defaultdict
from datetime import datetime, timezone
from json import dump, dumps, load, loads
from pathlib import Path

import pandas as pd


def parse_arguments() -> Namespace:
    """Read fact graph assembly input and output paths."""
    neo4j_root = Path(__file__).resolve().parent.parent
    output_root = neo4j_root / "output"
    parser = ArgumentParser(
        description=(
            "Assemble structured, canonical, and NLP fact candidates "
            "without calling an LLM or loading Neo4j."
        )
    )
    parser.add_argument(
        "--config",
        default=str(
            neo4j_root
            / "config"
            / "exam_anchor_fact_graph_assembly.json"
        ),
    )
    parser.add_argument(
        "--exam-links",
        default=str(
            output_root
            / "final_identity"
            / "neo4j_exam_term_to_entity_relationships.csv"
        ),
    )
    parser.add_argument(
        "--source-links",
        default=str(
            output_root
            / "final_identity"
            / "neo4j_source_to_entity_relationships.csv"
        ),
    )
    parser.add_argument(
        "--source-nodes",
        default=str(
            output_root
            / "source_relationships"
            / "source_record_nodes.csv"
        ),
    )
    parser.add_argument(
        "--source-relationships",
        default=str(
            output_root
            / "source_relationships"
            / "source_record_relationships.csv"
        ),
    )
    parser.add_argument(
        "--canonical-registry",
        default=str(
            output_root
            / "final_identity"
            / "canonical_entity_registry.csv"
        ),
    )
    parser.add_argument(
        "--canonical-facts",
        default=str(
            output_root
            / "source_relationships"
            / "canonical_fact_relationships.csv"
        ),
    )
    parser.add_argument(
        "--description-mentions",
        default=str(
            output_root
            / "source_relationships"
            / "description_mention_candidates.csv"
        ),
    )
    parser.add_argument(
        "--nlp-strict",
        default=str(
            output_root
            / "exam_term_nlp_relation_gate"
            / "safe_relation_candidates.csv"
        ),
    )
    parser.add_argument(
        "--nlp-type-review",
        default=str(
            output_root
            / "exam_term_nlp_relation_gate"
            / "type_review_relation_candidates.csv"
        ),
    )
    parser.add_argument(
        "--nlp-evidence",
        default=str(
            output_root
            / "exam_term_nlp_relation_gate"
            / "relation_gate_evidence_audit.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(output_root / "exam_anchor_fact_graph"),
    )
    return parser.parse_args()


def parse_json_list(value: object) -> list[str]:
    """Read one JSON list from a staging CSV cell."""
    if not str(value).strip():
        return []
    parsed = loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON list, received: {value}")
    return [str(item) for item in parsed]


def make_source_relation_evidence(
    row: dict[str, object],
) -> dict[str, str]:
    """Convert one structured source assertion into evidence metadata."""
    evidence_urls = parse_json_list(row.get("evidence_urls_json", "[]"))
    detail_urls = parse_json_list(row.get("detail_urls_json", "[]"))
    source_url = ""
    if evidence_urls:
        source_url = evidence_urls[0]
    elif detail_urls:
        source_url = detail_urls[0]
    return {
        "evidence_id": str(row["source_relationship_id"]),
        "source_record_id": str(row["start_source_record_id"]),
        "source_dataset": str(row.get("source_dataset", "")),
        "source_document_id": str(row["start_source_record_id"]),
        "source_url": source_url,
        "source_text": "",
        "evidence_kind": "STRUCTURED_SOURCE_ASSERTION",
        "source_release": str(row.get("source_release", "")),
        "raw_relation_type": str(row.get("raw_relation_type", "")),
        "relation_qualifiers_json": str(
            row.get("relation_qualifiers_json", "[]")
        ),
        "evidence_urls_json": dumps(
            evidence_urls,
            ensure_ascii=False,
        ),
        "detail_urls_json": dumps(detail_urls, ensure_ascii=False),
        "scopes_json": str(row.get("scopes_json", "[]")),
    }


def make_description_evidence(
    row: dict[str, object],
) -> dict[str, str]:
    """Convert one official description mention into evidence metadata."""
    evidence_url = str(row.get("evidence_url", ""))
    return {
        "evidence_id": str(row["description_mention_id"]),
        "source_record_id": str(row.get("source_record_id", "")),
        "source_dataset": str(row.get("source", "")),
        "source_document_id": str(row.get("source_record_id", "")),
        "source_url": evidence_url,
        "source_text": str(row.get("evidence_sentence", "")),
        "evidence_kind": "OFFICIAL_DESCRIPTION",
        "source_release": str(row.get("source_release", "")),
        "evidence_urls_json": dumps(
            [evidence_url] if evidence_url else [],
            ensure_ascii=False,
        ),
        "detail_urls_json": dumps(
            [evidence_url] if evidence_url else [],
            ensure_ascii=False,
        ),
        "scopes_json": dumps(
            [str(row.get("evidence_field", ""))]
            if str(row.get("evidence_field", ""))
            else [],
            ensure_ascii=False,
        ),
    }


def make_nlp_evidence(
    row: dict[str, object],
    source_url: str = "",
) -> dict[str, str]:
    """Convert one gated official-text clause into evidence metadata."""
    return {
        "evidence_id": str(row["nlp_relation_evidence_id"]),
        "source_record_id": str(row.get("source_document_id", "")),
        "source_dataset": str(row.get("source_dataset", "")),
        "source_document_id": str(row.get("source_document_id", "")),
        "source_url": source_url,
        "source_text": str(row.get("atomic_clause_text", "")),
        "evidence_kind": "OFFICIAL_TEXT_CLAUSE",
        "source_release": "",
        "evidence_urls_json": dumps(
            [source_url] if source_url else [],
            ensure_ascii=False,
        ),
        "detail_urls_json": dumps(
            [source_url] if source_url else [],
            ensure_ascii=False,
        ),
        "scopes_json": "[]",
    }


def serialize_evidence_records(
    evidence_ids: list[str],
    evidence_by_id: dict[str, dict[str, str]],
) -> tuple[str, bool]:
    """Serialize evidence in ID order and report metadata completeness."""
    records = [
        evidence_by_id[evidence_id]
        for evidence_id in sorted(set(evidence_ids))
        if evidence_id in evidence_by_id
    ]
    is_complete = len(records) == len(set(evidence_ids))
    return dumps(records, ensure_ascii=False), is_complete


def read_selected_nlp_evidence(
    evidence_path: str,
    evidence_ids: set[str],
    chunk_size: int,
) -> pd.DataFrame:
    """Read only selected evidence rows from the large gate audit CSV."""
    if not evidence_ids:
        return pd.DataFrame()
    selected_chunks: list[pd.DataFrame] = []
    use_columns = [
        "nlp_relation_evidence_id",
        "source_dataset",
        "source_document_id",
        "atomic_clause_text",
    ]
    for chunk in pd.read_csv(
        evidence_path,
        dtype=str,
        keep_default_na=False,
        usecols=use_columns,
        chunksize=chunk_size,
    ):
        selected = chunk[
            chunk["nlp_relation_evidence_id"].isin(evidence_ids)
        ]
        if not selected.empty:
            selected_chunks.append(selected)
    if not selected_chunks:
        return pd.DataFrame(columns=use_columns)
    return pd.concat(selected_chunks, ignore_index=True).drop_duplicates(
        "nlp_relation_evidence_id"
    )


def build_fact_graph_tables(
    exam_links: pd.DataFrame,
    source_links: pd.DataFrame,
    source_nodes: pd.DataFrame,
    source_relationships: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    canonical_facts: pd.DataFrame,
    nlp_strict: pd.DataFrame,
    nlp_type_review: pd.DataFrame,
    policy: dict[str, object],
    description_mentions: pd.DataFrame | None = None,
    nlp_evidence: pd.DataFrame | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """Build a tiered fact graph around accepted exam anchors."""
    accepted_status = str(policy["accepted_match_status"])
    tiers = policy["candidate_tiers"]
    exam_ids_by_canonical: dict[str, set[str]] = defaultdict(set)
    for row in exam_links.to_dict("records"):
        if str(row["match_status"]) != accepted_status:
            continue
        exam_ids_by_canonical[str(row["canonical_id"])].add(
            str(row["exam_term_id"])
        )

    canonical_ids_by_source: dict[str, set[str]] = defaultdict(set)
    sources_by_canonical: dict[str, set[str]] = defaultdict(set)
    for row in source_links.to_dict("records"):
        if str(row["match_status"]) != accepted_status:
            continue
        source_id = str(row["source_record_id"])
        canonical_id = str(row["canonical_id"])
        canonical_ids_by_source[source_id].add(canonical_id)
        sources_by_canonical[canonical_id].add(source_id)
    conflicting_source_ids = sorted(
        source_id
        for source_id, canonical_ids in canonical_ids_by_source.items()
        if len(canonical_ids) > 1
    )
    if conflicting_source_ids:
        raise ValueError(
            "Accepted source records resolve to multiple canonical IDs: "
            + ", ".join(conflicting_source_ids[:20])
        )
    canonical_by_source = {
        source_id: next(iter(canonical_ids))
        for source_id, canonical_ids in canonical_ids_by_source.items()
    }
    anchor_sources = {
        source_id
        for canonical_id in exam_ids_by_canonical
        for source_id in sources_by_canonical.get(
            canonical_id,
            set(),
        )
    }

    excluded_source_types = {
        str(value)
        for value in policy[
            "excluded_structured_relation_types"
        ]
    }
    source_fact_rows = [
        row
        for row in source_relationships.to_dict("records")
        if str(row["relation_type"]) not in excluded_source_types
        and (
            not bool(policy["exclude_self_relations"])
            or str(row["start_source_record_id"])
            != str(row["end_source_record_id"])
        )
    ]
    adjacency: dict[str, set[str]] = defaultdict(set)
    for row in source_fact_rows:
        start_id = str(row["start_source_record_id"])
        end_id = str(row["end_source_record_id"])
        adjacency[start_id].add(end_id)
        adjacency[end_id].add(start_id)

    distances = {source_id: 0 for source_id in anchor_sources}
    nearest_anchor = {
        source_id: source_id for source_id in anchor_sources
    }
    frontier = set(anchor_sources)
    maximum_hops = int(
        policy["maximum_structured_source_hops"]
    )
    for depth in range(1, maximum_hops + 1):
        next_frontier: set[str] = set()
        for source_id in sorted(frontier):
            for neighbor_id in sorted(
                adjacency.get(source_id, set())
            ):
                if neighbor_id in distances:
                    continue
                distances[neighbor_id] = depth
                nearest_anchor[neighbor_id] = nearest_anchor[
                    source_id
                ]
                next_frontier.add(neighbor_id)
        frontier = next_frontier

    source_node_by_id = {
        str(row["source_record_id"]): row
        for row in source_nodes.to_dict("records")
    }
    canonical_node_by_id = {
        str(row["canonical_id"]): row
        for row in canonical_registry.to_dict("records")
    }
    canonical_source_evidence_ids = {
        evidence_id
        for row in canonical_facts.to_dict("records")
        for evidence_id in parse_json_list(
            row.get("source_relationship_ids_json", "[]")
        )
    }
    source_evidence_by_id = {
        str(row["source_relationship_id"]): (
            make_source_relation_evidence(row)
        )
        for row in source_fact_rows
        if str(row["source_relationship_id"])
        in canonical_source_evidence_ids
    }
    description_table = pd.DataFrame()
    if description_mentions is not None:
        description_table = description_mentions
    description_evidence_by_id = {
        str(row["description_mention_id"]): (
            make_description_evidence(row)
        )
        for row in description_table.to_dict("records")
    }
    canonical_evidence_by_id = {
        **source_evidence_by_id,
        **description_evidence_by_id,
    }
    nlp_evidence_table = pd.DataFrame()
    if nlp_evidence is not None:
        nlp_evidence_table = nlp_evidence
    nlp_evidence_by_id = {
        str(row["nlp_relation_evidence_id"]): make_nlp_evidence(row)
        for row in nlp_evidence_table.to_dict("records")
    }
    source_type_mapping = {
        str(key): str(value)
        for key, value in policy[
            "source_record_type_to_entity_type"
        ].items()
    }
    structured_rows: list[dict[str, object]] = []
    structured_self_relation_after_resolution_count = 0
    structured_canonical_endpoint_count = 0
    for row in source_fact_rows:
        start_id = str(row["start_source_record_id"])
        end_id = str(row["end_source_record_id"])
        start_distance = distances.get(start_id)
        end_distance = distances.get(end_id)
        known_distances = [
            value
            for value in [start_distance, end_distance]
            if value is not None
        ]
        if not known_distances:
            continue
        relation_hops = min(known_distances) + 1
        if relation_hops > maximum_hops:
            continue
        nearest_endpoint = start_id
        if (
            end_distance is not None
            and (
                start_distance is None
                or end_distance < start_distance
            )
        ):
            nearest_endpoint = end_id
        root_source_id = nearest_anchor[nearest_endpoint]
        root_canonical_id = canonical_by_source.get(
            root_source_id,
            "",
        )
        anchor_exam_ids = sorted(
            exam_ids_by_canonical.get(
                root_canonical_id,
                set(),
            )
        )
        start_node = source_node_by_id[start_id]
        end_node = source_node_by_id[end_id]
        resolved_start_id = canonical_by_source.get(start_id, start_id)
        resolved_end_id = canonical_by_source.get(end_id, end_id)
        if (
            bool(policy["exclude_self_relations"])
            and resolved_start_id == resolved_end_id
        ):
            structured_self_relation_after_resolution_count += 1
            continue
        start_node_kind = "SOURCE_RECORD"
        start_display_name = str(start_node["display_name"])
        start_entity_type = source_type_mapping.get(
            str(start_node["record_type"]),
            "Unknown",
        )
        if resolved_start_id != start_id:
            canonical_start = canonical_node_by_id[resolved_start_id]
            start_node_kind = "CANONICAL"
            start_display_name = str(canonical_start["display_name"])
            start_entity_type = str(canonical_start["entity_type"])
            structured_canonical_endpoint_count += 1
        end_node_kind = "SOURCE_RECORD"
        end_display_name = str(end_node["display_name"])
        end_entity_type = source_type_mapping.get(
            str(end_node["record_type"]),
            "Unknown",
        )
        if resolved_end_id != end_id:
            canonical_end = canonical_node_by_id[resolved_end_id]
            end_node_kind = "CANONICAL"
            end_display_name = str(canonical_end["display_name"])
            end_entity_type = str(canonical_end["entity_type"])
            structured_canonical_endpoint_count += 1
        evidence_ids = [str(row["source_relationship_id"])]
        evidence_records_json, evidence_complete = (
            serialize_evidence_records(
                evidence_ids,
                {
                    evidence_ids[0]: make_source_relation_evidence(
                        row
                    )
                },
            )
        )
        structured_rows.append(
            {
                "fact_graph_candidate_id": (
                    "structured:"
                    + str(row["source_relationship_id"])
                ),
                "start_node_id": resolved_start_id,
                "start_node_kind": start_node_kind,
                "start_display_name": start_display_name,
                "start_entity_type": start_entity_type,
                "relation_type": str(row["relation_type"]),
                "end_node_id": resolved_end_id,
                "end_node_kind": end_node_kind,
                "end_display_name": end_display_name,
                "end_entity_type": end_entity_type,
                "candidate_tier": str(
                    tiers["structured_source"]
                ),
                "relation_origin": "STRUCTURED_SOURCE",
                "minimum_exam_anchor_hops": relation_hops,
                "anchor_exam_term_ids_json": dumps(
                    anchor_exam_ids,
                    ensure_ascii=False,
                ),
                "evidence_count": len(evidence_ids),
                "evidence_ids_json": dumps(
                    evidence_ids,
                    ensure_ascii=False,
                ),
                "evidence_records_json": evidence_records_json,
                "evidence_metadata_complete": evidence_complete,
                "source_datasets_json": dumps(
                    [str(row["source_dataset"])],
                    ensure_ascii=False,
                ),
                "verification_status": str(
                    row["verification_status"]
                ),
                "auto_load_eligible": False,
                "llm_used": False,
                "neo4j_load": False,
                "policy_version": str(
                    policy["policy_version"]
                ),
            }
        )

    excluded_canonical_types = {
        str(value)
        for value in policy[
            "excluded_canonical_relation_types"
        ]
    }
    canonical_rows: list[dict[str, object]] = []
    for row in canonical_facts.to_dict("records"):
        relation_type = str(row["relation_type"])
        if relation_type in excluded_canonical_types:
            continue
        start_id = str(row["start_canonical_id"])
        end_id = str(row["end_canonical_id"])
        if (
            bool(policy["exclude_self_relations"])
            and start_id == end_id
        ):
            continue
        start_node = canonical_node_by_id[start_id]
        end_node = canonical_node_by_id[end_id]
        anchor_exam_ids = sorted(
            exam_ids_by_canonical.get(start_id, set()).union(
                exam_ids_by_canonical.get(end_id, set())
            )
        )
        source_evidence_ids = parse_json_list(
            row.get("source_relationship_ids_json", "[]")
        )
        description_evidence_ids = parse_json_list(
            row.get("description_mention_ids_json", "[]")
        )
        evidence_ids = sorted(
            set(source_evidence_ids).union(
                description_evidence_ids
            )
        )
        evidence_records_json, evidence_complete = (
            serialize_evidence_records(
                evidence_ids,
                canonical_evidence_by_id,
            )
        )
        canonical_rows.append(
            {
                "fact_graph_candidate_id": (
                    "canonical:"
                    + str(row["canonical_relationship_id"])
                ),
                "start_node_id": start_id,
                "start_node_kind": "CANONICAL",
                "start_display_name": str(
                    start_node["display_name"]
                ),
                "start_entity_type": str(
                    start_node["entity_type"]
                ),
                "relation_type": relation_type,
                "end_node_id": end_id,
                "end_node_kind": "CANONICAL",
                "end_display_name": str(
                    end_node["display_name"]
                ),
                "end_entity_type": str(
                    end_node["entity_type"]
                ),
                "candidate_tier": str(
                    tiers["canonical_fact"]
                ),
                "relation_origin": "CANONICAL_FACT",
                "minimum_exam_anchor_hops": (
                    1 if anchor_exam_ids else ""
                ),
                "anchor_exam_term_ids_json": dumps(
                    anchor_exam_ids,
                    ensure_ascii=False,
                ),
                "evidence_count": len(evidence_ids),
                "evidence_ids_json": dumps(
                    evidence_ids,
                    ensure_ascii=False,
                ),
                "evidence_records_json": evidence_records_json,
                "evidence_metadata_complete": evidence_complete,
                "source_datasets_json": str(
                    row["source_datasets_json"]
                ),
                "verification_status": str(
                    row["verification_status"]
                ),
                "auto_load_eligible": False,
                "llm_used": False,
                "neo4j_load": False,
                "policy_version": str(
                    policy["policy_version"]
                ),
            }
        )

    nlp_rows: list[dict[str, object]] = []
    auto_accept_policy = policy["nlp_auto_accept"]
    stable_node_kinds = {
        str(value)
        for value in auto_accept_policy["stable_node_kinds"]
    }
    for table, tier, can_auto_accept in [
        (nlp_strict, str(tiers["nlp_strict"]), True),
        (
            nlp_type_review,
            str(tiers["nlp_type_review"]),
            False,
        ),
    ]:
        for row in table.to_dict("records"):
            if (
                bool(policy["exclude_self_relations"])
                and str(row["start_node_id"])
                == str(row["end_node_id"])
            ):
                continue
            evidence_ids = parse_json_list(
                row.get("evidence_ids_json", "[]")
            )
            representative_document_id = str(
                row.get("representative_source_document_id", "")
            )
            representative_url = str(
                row.get("representative_source_url", "")
            )
            candidate_evidence_by_id: dict[
                str,
                dict[str, str],
            ] = {}
            for evidence_id in evidence_ids:
                evidence_record = nlp_evidence_by_id.get(evidence_id)
                if evidence_record is None:
                    continue
                candidate_record = dict(evidence_record)
                if (
                    representative_url
                    and candidate_record["source_document_id"]
                    == representative_document_id
                ):
                    candidate_record["source_url"] = representative_url
                    candidate_record["evidence_urls_json"] = dumps(
                        [representative_url],
                        ensure_ascii=False,
                    )
                    candidate_record["detail_urls_json"] = dumps(
                        [representative_url],
                        ensure_ascii=False,
                    )
                candidate_evidence_by_id[evidence_id] = candidate_record
            if (
                evidence_ids
                and not candidate_evidence_by_id
                and str(row.get("representative_atomic_clause", ""))
            ):
                fallback_row = {
                    "nlp_relation_evidence_id": evidence_ids[0],
                    "source_dataset": str(
                        row.get("representative_source_dataset", "")
                    ),
                    "source_document_id": representative_document_id,
                    "atomic_clause_text": str(
                        row.get("representative_atomic_clause", "")
                    ),
                }
                candidate_evidence_by_id[evidence_ids[0]] = (
                    make_nlp_evidence(
                        fallback_row,
                        representative_url,
                    )
                )
            evidence_records_json, evidence_complete = (
                serialize_evidence_records(
                    evidence_ids,
                    candidate_evidence_by_id,
                )
            )
            candidate_tier = tier
            if (
                can_auto_accept
                and str(row["gate_status"])
                == str(auto_accept_policy["verification_status"])
                and str(row["start_node_kind"])
                in stable_node_kinds
                and str(row["end_node_kind"]) in stable_node_kinds
            ):
                candidate_tier = str(
                    tiers["nlp_corroborated_stable"]
                )
            nlp_rows.append(
                {
                    "fact_graph_candidate_id": (
                        "nlp:"
                        + str(
                            row[
                                "safe_relation_candidate_id"
                            ]
                        )
                    ),
                    "start_node_id": str(row["start_node_id"]),
                    "start_node_kind": str(
                        row["start_node_kind"]
                    ),
                    "start_display_name": str(
                        row["start_display_name"]
                    ),
                    "start_entity_type": str(
                        row["start_entity_type"]
                    ),
                    "relation_type": str(row["relation_type"]),
                    "end_node_id": str(row["end_node_id"]),
                    "end_node_kind": str(row["end_node_kind"]),
                    "end_display_name": str(
                        row["end_display_name"]
                    ),
                    "end_entity_type": str(
                        row["end_entity_type"]
                    ),
                    "candidate_tier": candidate_tier,
                    "relation_origin": "NLP_OFFICIAL_TEXT",
                    "minimum_exam_anchor_hops": 1,
                    "anchor_exam_term_ids_json": str(
                        row["anchor_exam_term_ids_json"]
                    ),
                    "evidence_count": len(evidence_ids),
                    "evidence_ids_json": dumps(
                        evidence_ids,
                        ensure_ascii=False,
                    ),
                    "evidence_records_json": evidence_records_json,
                    "evidence_metadata_complete": evidence_complete,
                    "source_datasets_json": str(
                        row["source_datasets_json"]
                    ),
                    "verification_status": str(
                        row["gate_status"]
                    ),
                    "auto_load_eligible": False,
                    "llm_used": False,
                    "neo4j_load": False,
                    "policy_version": str(
                        policy["policy_version"]
                    ),
                }
            )

    structured_table = pd.DataFrame(structured_rows)
    canonical_table = pd.DataFrame(canonical_rows)
    nlp_table = pd.DataFrame(nlp_rows)
    all_table = pd.concat(
        [structured_table, canonical_table, nlp_table],
        ignore_index=True,
    )
    statistics: dict[str, object] = {
        "accepted_exam_canonical_count": len(
            exam_ids_by_canonical
        ),
        "structured_anchor_source_count": len(anchor_sources),
        "maximum_structured_source_hops": maximum_hops,
        "structured_source_fact_count": len(structured_table),
        "structured_canonical_endpoint_count": (
            structured_canonical_endpoint_count
        ),
        "structured_self_relation_after_resolution_count": (
            structured_self_relation_after_resolution_count
        ),
        "canonical_core_fact_count": len(canonical_table),
        "nlp_strict_fact_count": len(nlp_strict),
        "nlp_type_review_fact_count": len(nlp_type_review),
        "all_fact_graph_candidate_count": len(all_table),
        "candidate_tier_counts": {
            str(key): int(value)
            for key, value in all_table[
                "candidate_tier"
            ].value_counts().items()
        },
        "evidence_metadata_incomplete_candidate_count": int(
            all_table["evidence_metadata_complete"].astype(str).ne(
                "True"
            ).sum()
        ),
        "semantic_duplicate_candidate_count": int(
            all_table.duplicated(
                [
                    "start_node_id",
                    "relation_type",
                    "end_node_id",
                ],
                keep=False,
            ).sum()
        ),
        "semantic_duplicate_group_count": int(
            all_table.groupby(
                [
                    "start_node_id",
                    "relation_type",
                    "end_node_id",
                ]
            ).size().gt(1).sum()
        ),
        "both_nlp_endpoints_open_count": int(
            (
                nlp_table["start_node_kind"].eq(
                    "OPEN_ENTITY_CANDIDATE"
                )
                & nlp_table["end_node_kind"].eq(
                    "OPEN_ENTITY_CANDIDATE"
                )
            ).sum()
        ),
        "llm_used": False,
        "neo4j_load": False,
    }
    return {
        "structured_source": structured_table,
        "canonical_fact": canonical_table,
        "nlp": nlp_table,
        "all": all_table,
    }, statistics


def run_assembly(cli_args: Namespace) -> dict[str, object]:
    """Load current outputs, assemble tiers, and save CSV files."""
    with Path(cli_args.config).open(
        "r",
        encoding="utf-8",
    ) as input_file:
        policy = load(input_file)
    nlp_strict = pd.read_csv(
        cli_args.nlp_strict,
        dtype=str,
    ).fillna("")
    nlp_type_review = pd.read_csv(
        cli_args.nlp_type_review,
        dtype=str,
    ).fillna("")
    nlp_evidence_ids = {
        evidence_id
        for table in [nlp_strict, nlp_type_review]
        for value in table.get(
            "evidence_ids_json",
            pd.Series(dtype=str),
        )
        for evidence_id in parse_json_list(value)
    }
    nlp_evidence = read_selected_nlp_evidence(
        cli_args.nlp_evidence,
        nlp_evidence_ids,
        int(policy["nlp_evidence_read_chunk_size"]),
    )
    tables, statistics = build_fact_graph_tables(
        pd.read_csv(cli_args.exam_links, dtype=str).fillna(""),
        pd.read_csv(cli_args.source_links, dtype=str).fillna(""),
        pd.read_csv(cli_args.source_nodes, dtype=str).fillna(""),
        pd.read_csv(
            cli_args.source_relationships,
            dtype=str,
        ).fillna(""),
        pd.read_csv(
            cli_args.canonical_registry,
            dtype=str,
        ).fillna(""),
        pd.read_csv(cli_args.canonical_facts, dtype=str).fillna(
            ""
        ),
        nlp_strict,
        nlp_type_review,
        policy,
        description_mentions=pd.read_csv(
            cli_args.description_mentions,
            dtype=str,
        ).fillna(""),
        nlp_evidence=nlp_evidence,
    )
    output_directory = Path(cli_args.output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = (
            output_directory
            / str(policy["outputs"][table_name])
        )
        table.to_csv(
            output_path,
            index=False,
            encoding="utf-8-sig",
        )
        output_paths[table_name] = str(output_path)
    summary_rows = [
        {
            "metric": key.upper(),
            "value": (
                dumps(value, ensure_ascii=False)
                if isinstance(value, dict)
                else value
            ),
        }
        for key, value in statistics.items()
    ]
    summary_path = (
        output_directory
        / str(policy["outputs"]["summary"])
    )
    pd.DataFrame(summary_rows).to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )
    output_paths["summary"] = str(summary_path)
    manifest = {
        "status": "COMPLETED",
        "stage": "EXAM_ANCHOR_FACT_GRAPH_ASSEMBLY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": str(policy["policy_version"]),
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory
        / str(policy["outputs"]["manifest"])
    )
    with manifest_path.open(
        "w",
        encoding="utf-8",
    ) as output_file:
        dump(
            manifest,
            output_file,
            ensure_ascii=False,
            indent=2,
        )
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """Run fact graph assembly."""
    result = run_assembly(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

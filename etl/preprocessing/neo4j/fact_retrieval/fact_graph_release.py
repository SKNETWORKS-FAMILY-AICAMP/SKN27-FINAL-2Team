from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        return list(csv.DictReader(input_file))


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as input_file:
        value = json.load(input_file)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            value = json.loads(stripped)
            if not isinstance(value, dict):
                raise ValueError(
                    f"JSONL row must be an object: {path}:{line_number}"
                )
            rows.append(value)
    return rows


def write_csv_rows(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        with temporary_path.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as output_file:
            writer = csv.DictWriter(
                output_file,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(path)
    finally:
        if temporary_path.is_file():
            temporary_path.unlink()


def normalize_search_text(value: str) -> str:
    return "".join(
        character.casefold()
        for character in value
        if character.isalnum()
    )


def normalize_endpoint_display_name(value: str) -> str:
    base_name = re.split(r"[\r\n(（]", value, maxsplit=1)[0]
    return normalize_search_text(base_name)


def normalized_name_variants(value: str) -> set[str]:
    stripped = value.strip()
    if not stripped:
        return set()
    variants = {normalize_search_text(stripped)}
    base_name = re.split(r"[\r\n(（]", stripped, maxsplit=1)[0]
    variants.add(normalize_search_text(base_name))
    return {variant for variant in variants if variant}


def stable_identifier(prefix: str, value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def build_fact_graph_release(
    output_root: Path,
    config: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    input_paths = {
        name: output_root / relative_path
        for name, relative_path in config["input_paths"].items()
    }
    missing_paths = [
        str(path)
        for path in input_paths.values()
        if not path.is_file()
    ]
    if missing_paths:
        raise FileNotFoundError(
            "Missing fact graph release inputs: " + ", ".join(missing_paths)
        )

    graph_release_id = str(config["graph_release_id"])
    relation_pattern = re.compile(str(config["relation_type_pattern"]))
    resolved_node_kind = str(config["resolved_node_kind"])
    provisional_source_kinds = set(config["provisional_source_node_kinds"])
    accepted_review_statuses = set(
        config["accepted_relation_review_statuses"]
    )
    contextual_merge = config["contextual_merge"]
    fact_projection_deduplication = config[
        "fact_projection_deduplication"
    ]
    terminal_fact_retrieval = config["terminal_fact_retrieval"]
    exact_search_policy = config["exact_search_policy"]
    canonical_endpoint_resolution = config[
        "canonical_endpoint_resolution"
    ]
    accepted_match_status = str(config["accepted_match_status"])
    symmetric_predicates = set(
        fact_projection_deduplication["symmetric_predicates"]
    )
    if (
        bool(terminal_fact_retrieval["enabled"])
        and not bool(
            terminal_fact_retrieval[
                "require_at_least_one_canonical_endpoint"
            ]
        )
    ):
        raise ValueError(
            "Terminal retrieval requires at least one canonical endpoint"
        )
    if (
        bool(terminal_fact_retrieval["enabled"])
        and not bool(
            terminal_fact_retrieval["block_provisional_traversal"]
        )
    ):
        raise ValueError(
            "Terminal retrieval must block provisional traversal"
        )

    identity_conflict_config = read_json(
        input_paths["identity_conflict_decisions"]
    )
    identity_conflict_rows = identity_conflict_config.get("decisions", [])
    if not isinstance(identity_conflict_rows, list):
        raise ValueError("Identity conflict decisions must be a list")
    allowed_identity_decisions = {"KEEP", "QUARANTINE", "REDIRECT"}
    identity_decision_by_source_node_id: dict[str, dict[str, str]] = {}
    for row in identity_conflict_rows:
        source_node_id = row["source_node_id"]
        decision = row["decision"]
        if decision not in allowed_identity_decisions:
            raise ValueError(
                f"Unsupported identity conflict decision {decision!r}: "
                f"{source_node_id}"
            )
        if source_node_id in identity_decision_by_source_node_id:
            raise ValueError(
                f"Duplicate identity conflict decision: {source_node_id}"
            )
        identity_decision_by_source_node_id[source_node_id] = row
    quarantined_source_node_ids = {
        source_node_id
        for source_node_id, row in identity_decision_by_source_node_id.items()
        if row["decision"] == "QUARANTINE"
    }
    redirected_source_node_ids = {
        source_node_id: row["preferred_source_node_id"]
        for source_node_id, row in identity_decision_by_source_node_id.items()
        if row["decision"] == "REDIRECT"
    }

    canonical_rows = read_csv_rows(input_paths["canonical_entities"])
    canonical_by_id = {
        row["canonical_id"]: row
        for row in canonical_rows
    }
    canonical_registry_rows = read_csv_rows(
        input_paths["canonical_registry"]
    )
    source_node_by_id = {
        row["source_record_id"]: row
        for row in read_csv_rows(input_paths["source_nodes"])
    }
    evidence_by_id = {
        row["evidence_id"]: row
        for row in read_csv_rows(input_paths["evidence"])
    }

    canonical_name_ids_by_key: dict[
        tuple[str, str],
        set[str],
    ] = {}
    canonical_alias_ids_by_key: dict[
        tuple[str, str],
        set[str],
    ] = {}
    qualified_aliases_by_canonical_id: dict[str, set[str]] = {}
    for canonical in canonical_rows:
        if canonical["lifecycle_status"] != "ACTIVE":
            continue
        canonical_id = canonical["canonical_id"]
        entity_type = canonical["entity_type"]
        for normalized_name in normalized_name_variants(
            canonical["display_name"]
        ):
            canonical_name_ids_by_key.setdefault(
                (normalized_name, entity_type),
                set(),
            ).add(canonical_id)

    metadata_name_fields = canonical_endpoint_resolution[
        "metadata_name_fields"
    ]
    metadata_alias_fields = canonical_endpoint_resolution[
        "metadata_alias_fields"
    ]
    metadata_qualifier_fields = canonical_endpoint_resolution[
        "metadata_qualifier_fields"
    ]
    for registry_row in canonical_registry_rows:
        canonical_id = registry_row["canonical_id"]
        canonical = canonical_by_id.get(canonical_id)
        if canonical is None:
            continue
        if canonical["lifecycle_status"] != "ACTIVE":
            continue
        entity_type = canonical["entity_type"]
        source_node_ids = json.loads(
            registry_row["identity_member_source_ids_json"]
        )
        for source_node_id in source_node_ids:
            source_node = source_node_by_id.get(source_node_id)
            if source_node is None:
                continue
            metadata = json.loads(
                source_node.get("source_metadata_json", "{}") or "{}"
            )
            source_names = {
                source_node.get("display_name", ""),
                canonical["display_name"],
            }
            for field_name in metadata_name_fields:
                value = metadata.get(field_name)
                if value:
                    source_names.add(str(value))
            for field_name in metadata_alias_fields:
                values = metadata.get(field_name, [])
                if isinstance(values, list):
                    source_names.update(str(value) for value in values)
            for source_name in source_names:
                for normalized_name in normalized_name_variants(
                    source_name
                ):
                    canonical_alias_ids_by_key.setdefault(
                        (normalized_name, entity_type),
                        set(),
                    ).add(canonical_id)
            qualifiers = {
                str(metadata[field_name]).strip()
                for field_name in metadata_qualifier_fields
                if metadata.get(field_name)
            }
            for qualifier in qualifiers:
                for source_name in source_names:
                    qualified_name = f"{qualifier} {source_name}"
                    for normalized_name in normalized_name_variants(
                        qualified_name
                    ):
                        canonical_alias_ids_by_key.setdefault(
                            (normalized_name, entity_type),
                            set(),
                        ).add(canonical_id)
                        qualified_aliases_by_canonical_id.setdefault(
                            canonical_id,
                            set(),
                        ).add(normalized_name)

    graph_node_by_id = {
        row["node_id"]: row
        for row in read_csv_rows(input_paths["fact_graph_nodes"])
    }
    for source_node_id, preferred_source_node_id in (
        redirected_source_node_ids.items()
    ):
        if source_node_id not in graph_node_by_id:
            raise ValueError(
                f"Redirect source node is absent: {source_node_id}"
            )
        if preferred_source_node_id not in graph_node_by_id:
            raise ValueError(
                "Redirect target node is absent: "
                f"{preferred_source_node_id}"
            )
        if preferred_source_node_id in quarantined_source_node_ids:
            raise ValueError(
                "Redirect target is quarantined: "
                f"{preferred_source_node_id}"
            )
        if preferred_source_node_id in redirected_source_node_ids:
            raise ValueError(
                "Chained identity redirects are not supported: "
                f"{source_node_id} -> {preferred_source_node_id}"
            )
    candidate_by_id = {
        row["fact_graph_candidate_id"]: row
        for row in read_csv_rows(input_paths["all_fact_graph_candidates"])
    }
    fact_by_id = {
        row["fact_id"]: row
        for row in read_csv_rows(input_paths["fact_graph_facts"])
    }
    source_relationship_by_id = {
        row["source_relationship_id"]: row
        for row in read_csv_rows(input_paths["source_relationships"])
    }
    relation_normalization = config["relation_normalization"]
    predicate_aliases = relation_normalization["predicate_aliases"]
    predicate_qualifiers = relation_normalization[
        "predicate_qualifiers"
    ]
    task_by_id = {
        row["relation_review_task_id"]: row
        for row in read_jsonl_rows(input_paths["relation_review_tasks"])
    }

    accepted_decision_by_fact_id: dict[str, dict[str, str]] = {}
    accepted_task_by_fact_id: dict[str, dict[str, Any]] = {}
    for decision in read_csv_rows(input_paths["relation_review_decisions"]):
        if decision["final_status"] not in accepted_review_statuses:
            continue
        task = task_by_id.get(decision["relation_review_task_id"])
        if task is None:
            raise ValueError(
                "Relation review decision has no matching task: "
                f"{decision['relation_review_task_id']}"
            )
        fact_id = decision["fact_id"]
        if fact_id in accepted_decision_by_fact_id:
            raise ValueError(f"Duplicate accepted relation fact: {fact_id}")
        accepted_decision_by_fact_id[fact_id] = decision
        accepted_task_by_fact_id[fact_id] = task

    endpoint_metadata: dict[str, dict[str, str]] = {}
    for candidate in candidate_by_id.values():
        for side in ("start", "end"):
            node_id = candidate[f"{side}_node_id"]
            metadata = {
                "node_kind": candidate[f"{side}_node_kind"],
                "display_name": candidate[f"{side}_display_name"],
                "entity_type": candidate[f"{side}_entity_type"],
            }
            current = endpoint_metadata.get(node_id)
            if current is None:
                endpoint_metadata[node_id] = metadata
                continue
            if current["node_kind"] != metadata["node_kind"]:
                source_kind_conflict = {
                    current["node_kind"],
                    metadata["node_kind"],
                }.issubset(provisional_source_kinds)
                if not source_kind_conflict:
                    raise ValueError(
                        f"Conflicting endpoint node kinds for {node_id}: "
                        f"{current['node_kind']} != {metadata['node_kind']}"
                    )
            if current["display_name"] == node_id and metadata["display_name"]:
                current["display_name"] = metadata["display_name"]
            if (
                current["entity_type"] in {"", "Unknown"}
                and metadata["entity_type"] not in {"", "Unknown"}
            ):
                current["entity_type"] = metadata["entity_type"]

    for fact_id, task in accepted_task_by_fact_id.items():
        for side in ("start", "end"):
            endpoint = task[side]
            endpoint_metadata[endpoint["node_id"]] = {
                "node_kind": endpoint["node_kind"],
                "display_name": endpoint["display_name"],
                "entity_type": endpoint["proposed_entity_type"],
            }

    eligible_endpoint_node_kinds = set(
        canonical_endpoint_resolution["eligible_node_kinds"]
    )
    unique_name_resolution_blocked_entity_types = set(
        canonical_endpoint_resolution[
            "unique_name_resolution_blocked_entity_types"
        ]
    )

    def resolve_unique_name_type(
        normalized_name: str,
        entity_type: str,
    ) -> tuple[str, str]:
        name_key = (normalized_name, entity_type)
        canonical_name_ids = set()
        canonical_alias_ids = set()
        if bool(
            canonical_endpoint_resolution[
                "allow_unique_canonical_name_type"
            ]
        ):
            canonical_name_ids.update(
                canonical_name_ids_by_key.get(name_key, set())
            )
        if bool(
            canonical_endpoint_resolution[
                "allow_unique_source_alias_type"
            ]
        ):
            canonical_alias_ids.update(
                canonical_alias_ids_by_key.get(name_key, set())
            )
        candidate_canonical_ids = (
            canonical_name_ids | canonical_alias_ids
        )
        if len(candidate_canonical_ids) != 1:
            return "", ""
        canonical_id = next(iter(candidate_canonical_ids))
        if entity_type in unique_name_resolution_blocked_entity_types:
            allow_qualified_alias = bool(
                canonical_endpoint_resolution[
                    "allow_qualified_alias_for_blocked_entity_types"
                ]
            )
            is_qualified_alias = normalized_name in (
                qualified_aliases_by_canonical_id.get(
                    canonical_id,
                    set(),
                )
            )
            if allow_qualified_alias and is_qualified_alias:
                return (
                    canonical_id,
                    "UNIQUE_QUALIFIED_SOURCE_ALIAS_TYPE",
                )
            return "", ""
        if canonical_id in canonical_name_ids:
            return canonical_id, "UNIQUE_CANONICAL_NAME_TYPE"
        return canonical_id, "UNIQUE_SOURCE_ALIAS_TYPE"

    def resolve_candidate_endpoint(
        node_id: str,
        node_kind: str,
        evidence_ids: list[str],
    ) -> tuple[str, str, str]:
        if not bool(canonical_endpoint_resolution["enabled"]):
            return node_id, node_kind, ""
        if node_kind not in eligible_endpoint_node_kinds:
            return node_id, node_kind, ""
        metadata = endpoint_metadata.get(node_id, {})
        graph_node = graph_node_by_id.get(node_id, {})
        display_name = (
            metadata.get("display_name")
            or graph_node.get("display_name")
            or ""
        )
        entity_type = metadata.get("entity_type") or graph_node.get(
            "entity_type",
            "",
        )
        if (
            bool(
                canonical_endpoint_resolution[
                    "require_known_entity_type"
                ]
            )
            and entity_type in {"", "Unknown"}
        ):
            return node_id, node_kind, ""
        normalized_name = normalize_endpoint_display_name(display_name)
        if not normalized_name:
            return node_id, node_kind, ""
        name_key = (normalized_name, entity_type)
        (
            directly_resolved_canonical_id,
            direct_resolution_method,
        ) = resolve_unique_name_type(
            normalized_name,
            entity_type,
        )
        if directly_resolved_canonical_id:
            return (
                directly_resolved_canonical_id,
                resolved_node_kind,
                direct_resolution_method,
            )
        canonical_name_ids = canonical_name_ids_by_key.get(
            name_key,
            set(),
        )
        canonical_alias_ids = canonical_alias_ids_by_key.get(
            name_key,
            set(),
        )
        if not bool(
            canonical_endpoint_resolution[
                "allow_evidence_qualified_alias"
            ]
        ):
            return node_id, node_kind, ""
        candidate_canonical_ids = (
            canonical_name_ids | canonical_alias_ids
        )
        if not candidate_canonical_ids:
            return node_id, node_kind, ""
        normalized_evidence_text = normalize_search_text(
            " ".join(
                evidence_by_id[evidence_id].get("source_text", "")
                for evidence_id in evidence_ids
                if evidence_id in evidence_by_id
            )
        )
        if not normalized_evidence_text:
            return node_id, node_kind, ""
        context_matches = {
            canonical_id
            for canonical_id in candidate_canonical_ids
            if any(
                qualified_alias in normalized_evidence_text
                for qualified_alias in (
                    qualified_aliases_by_canonical_id.get(
                        canonical_id,
                        set(),
                    )
                )
            )
        }
        if len(context_matches) != 1:
            return node_id, node_kind, ""
        return (
            next(iter(context_matches)),
            resolved_node_kind,
            "EVIDENCE_QUALIFIED_SOURCE_ALIAS",
        )

    selected_fact_inputs: list[dict[str, Any]] = []
    quarantined_facts: list[dict[str, Any]] = []
    for fact in fact_by_id.values():
        is_existing_verified = (
            fact["trust_status"] == config["existing_fact_status"]
        )
        decision = accepted_decision_by_fact_id.get(fact["fact_id"])
        if not is_existing_verified and decision is None:
            continue

        subject_source_node_id = fact["subject_node_id"]
        object_source_node_id = fact["object_node_id"]
        evidence_ids = json.loads(fact["evidence_ids_json"])
        subject_node_id = redirected_source_node_ids.get(
            subject_source_node_id,
            subject_source_node_id,
        )
        object_node_id = redirected_source_node_ids.get(
            object_source_node_id,
            object_source_node_id,
        )
        subject_graph_node = graph_node_by_id.get(subject_node_id)
        object_graph_node = graph_node_by_id.get(object_node_id)
        if subject_graph_node is None or object_graph_node is None:
            raise ValueError(
                f"Missing endpoint node for selected fact: {fact['fact_id']}"
            )
        subject_kind = subject_graph_node["node_kind"]
        object_kind = object_graph_node["node_kind"]
        (
            subject_node_id,
            subject_kind,
            subject_endpoint_resolution_method,
        ) = resolve_candidate_endpoint(
            subject_node_id,
            subject_kind,
            evidence_ids,
        )
        (
            object_node_id,
            object_kind,
            object_endpoint_resolution_method,
        ) = resolve_candidate_endpoint(
            object_node_id,
            object_kind,
            evidence_ids,
        )
        source_predicate = fact["predicate"]
        predicate = predicate_aliases.get(
            source_predicate,
            source_predicate,
        )
        if relation_pattern.fullmatch(predicate) is None:
            raise ValueError(
                f"Unsafe relationship type {predicate!r}: {fact['fact_id']}"
            )
        source_relationships = [
            source_relationship_by_id[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id in source_relationship_by_id
        ]
        raw_relation_types = sorted(
            {
                row["raw_relation_type"]
                for row in source_relationships
                if row.get("raw_relation_type", "")
            }
        )
        relation_qualifiers = {
            str(qualifier)
            for qualifier in predicate_qualifiers.get(
                source_predicate,
                [],
            )
        }
        for source_relationship in source_relationships:
            relation_qualifiers.update(
                json.loads(
                    source_relationship.get(
                        "relation_qualifiers_json",
                        "[]",
                    )
                    or "[]"
                )
            )

        selected_input = {
            "fact": fact,
            "decision": decision,
            "subject_kind": subject_kind,
            "object_kind": object_kind,
            "subject_identity_node_id": subject_node_id,
            "object_identity_node_id": object_node_id,
            "subject_endpoint_resolution_method": (
                subject_endpoint_resolution_method
            ),
            "object_endpoint_resolution_method": (
                object_endpoint_resolution_method
            ),
            "predicate": predicate,
            "source_predicates_json": json.dumps(
                [source_predicate],
                ensure_ascii=False,
            ),
            "raw_relation_types_json": json.dumps(
                raw_relation_types,
                ensure_ascii=False,
            ),
            "relation_qualifiers_json": json.dumps(
                sorted(relation_qualifiers),
                ensure_ascii=False,
            ),
        }
        quarantined_endpoints = sorted(
            {
                subject_source_node_id,
                object_source_node_id,
            }
            & quarantined_source_node_ids
        )
        if quarantined_endpoints:
            reason_codes = sorted(
                {
                    identity_decision_by_source_node_id[node_id][
                        "reason_code"
                    ]
                    for node_id in quarantined_endpoints
                }
            )
            quarantined_facts.append(
                {
                    "fact_id": fact["fact_id"],
                    "subject_node_id": subject_source_node_id,
                    "predicate": predicate,
                    "object_node_id": object_source_node_id,
                    "assertion_count": fact["assertion_count"],
                    "trust_status": fact["trust_status"],
                    "reason_codes_json": json.dumps(
                        reason_codes,
                        ensure_ascii=False,
                    ),
                    "quarantined_source_node_ids_json": json.dumps(
                        quarantined_endpoints,
                        ensure_ascii=False,
                    ),
                    "evidence_ids_json": fact["evidence_ids_json"],
                    "source_datasets_json": fact["source_datasets_json"],
                    "graph_release_id": graph_release_id,
                }
            )
            continue
        redirected_endpoints = sorted(
            {
                subject_source_node_id,
                object_source_node_id,
            }
            & redirected_source_node_ids.keys()
        )
        candidate_resolved_endpoints = sorted(
            source_node_id
            for source_node_id, resolution_method in (
                (
                    subject_source_node_id,
                    subject_endpoint_resolution_method,
                ),
                (
                    object_source_node_id,
                    object_endpoint_resolution_method,
                ),
            )
            if resolution_method
        )
        if (
            subject_node_id == object_node_id
            and (redirected_endpoints or candidate_resolved_endpoints)
        ):
            reason_codes = sorted(
                {
                    identity_decision_by_source_node_id[node_id][
                        "reason_code"
                    ]
                    for node_id in redirected_endpoints
                }
            )
            if candidate_resolved_endpoints:
                reason_codes.append(
                    "CANONICAL_ENDPOINT_RESOLUTION_SELF_RELATION"
                )
            if redirected_endpoints:
                reason_codes.append(
                    "IDENTITY_REDIRECT_SELF_RELATION"
                )
            reason_codes = sorted(set(reason_codes))
            quarantined_endpoint_ids = sorted(
                set(redirected_endpoints)
                | set(candidate_resolved_endpoints)
            )
            quarantined_facts.append(
                {
                    "fact_id": fact["fact_id"],
                    "subject_node_id": subject_source_node_id,
                    "predicate": predicate,
                    "object_node_id": object_source_node_id,
                    "assertion_count": fact["assertion_count"],
                    "trust_status": fact["trust_status"],
                    "reason_codes_json": json.dumps(
                        reason_codes,
                        ensure_ascii=False,
                    ),
                    "quarantined_source_node_ids_json": json.dumps(
                        quarantined_endpoint_ids,
                        ensure_ascii=False,
                    ),
                    "evidence_ids_json": fact["evidence_ids_json"],
                    "source_datasets_json": fact[
                        "source_datasets_json"
                    ],
                    "graph_release_id": graph_release_id,
                }
            )
            continue
        selected_fact_inputs.append(selected_input)

    contextual_groups: dict[
        tuple[str, str, str, str, str],
        dict[str, Any],
    ] = {}
    provisional_occurrences_by_node: dict[
        str,
        set[tuple[str, str]],
    ] = {}
    for item in selected_fact_inputs:
        fact = item["fact"]
        for side, node_kind in (
            ("subject", item["subject_kind"]),
            ("object", item["object_kind"]),
        ):
            if node_kind == resolved_node_kind:
                continue
            node_id = item[f"{side}_identity_node_id"]
            provisional_occurrences_by_node.setdefault(
                node_id,
                set(),
            ).add((fact["fact_id"], side))
    if bool(contextual_merge["enabled"]):
        anchor_entity_type = str(
            contextual_merge["anchor_entity_type"]
        )
        minimum_name_length = int(
            contextual_merge["minimum_normalized_name_length"]
        )
        for item in selected_fact_inputs:
            fact = item["fact"]
            subject_kind = item["subject_kind"]
            object_kind = item["object_kind"]
            anchor_id = ""
            direction = ""
            provisional_side = ""
            provisional_node_id = ""
            provisional_kind = ""
            if (
                subject_kind == resolved_node_kind
                and object_kind != resolved_node_kind
                and canonical_by_id[
                    item["subject_identity_node_id"]
                ]["entity_type"]
                == anchor_entity_type
            ):
                anchor_id = item["subject_identity_node_id"]
                direction = "OUT"
                provisional_side = "object"
                provisional_node_id = item["object_identity_node_id"]
                provisional_kind = object_kind
            elif (
                object_kind == resolved_node_kind
                and subject_kind != resolved_node_kind
                and canonical_by_id[
                    item["object_identity_node_id"]
                ]["entity_type"]
                == anchor_entity_type
            ):
                anchor_id = item["object_identity_node_id"]
                direction = "IN"
                provisional_side = "subject"
                provisional_node_id = item["subject_identity_node_id"]
                provisional_kind = subject_kind
            if not anchor_id:
                continue

            metadata = endpoint_metadata.get(provisional_node_id, {})
            graph_node = graph_node_by_id[provisional_node_id]
            display_name = (
                metadata.get("display_name")
                or graph_node["display_name"]
                or provisional_node_id
            )
            entity_type = (
                metadata.get("entity_type")
                or graph_node["entity_type"]
                or "Unknown"
            )
            normalized_name = normalize_search_text(display_name)
            if (
                entity_type in {"", "Unknown"}
                or len(normalized_name) < minimum_name_length
            ):
                continue

            group_key = (
                anchor_id,
                (
                    "*"
                    if bool(
                        contextual_merge[
                            "group_across_directions"
                        ]
                    )
                    else direction
                ),
                (
                    "*"
                    if bool(
                        contextual_merge[
                            "group_across_predicates"
                        ]
                    )
                    else item["predicate"]
                ),
                normalized_name,
                entity_type,
            )
            group = contextual_groups.setdefault(
                group_key,
                {
                    "anchor_id": anchor_id,
                    "directions": set(),
                    "predicates": set(),
                    "normalized_name": normalized_name,
                    "entity_type": entity_type,
                    "display_names": set(),
                    "source_nodes": {},
                    "members": [],
                    "member_source_node_by_occurrence": {},
                },
            )
            group["directions"].add(direction)
            group["predicates"].add(item["predicate"])
            group["display_names"].add(display_name)
            group["source_nodes"][provisional_node_id] = provisional_kind
            occurrence = (fact["fact_id"], provisional_side)
            group["members"].append(occurrence)
            group["member_source_node_by_occurrence"][
                occurrence
            ] = provisional_node_id

    minimum_source_count = int(
        contextual_merge["minimum_distinct_source_node_count"]
    )
    merge_scope = str(contextual_merge["merge_scope"])
    contextual_entity_id_by_endpoint: dict[tuple[str, str], str] = {}
    contextual_entity_metadata: dict[str, dict[str, Any]] = {}
    for group_key, group in contextual_groups.items():
        group_occurrences_by_source_node: dict[
            str,
            set[tuple[str, str]],
        ] = {}
        for occurrence, source_node_id in group[
            "member_source_node_by_occurrence"
        ].items():
            group_occurrences_by_source_node.setdefault(
                source_node_id,
                set(),
            ).add(occurrence)
        safe_source_node_ids = set(group_occurrences_by_source_node)
        if bool(
            contextual_merge["require_exclusive_source_membership"]
        ):
            safe_source_node_ids = {
                source_node_id
                for source_node_id, occurrences
                in group_occurrences_by_source_node.items()
                if occurrences
                == provisional_occurrences_by_node[source_node_id]
            }
        source_nodes = {
            source_node_id: source_node_kind
            for source_node_id, source_node_kind
            in group["source_nodes"].items()
            if source_node_id in safe_source_node_ids
        }
        if len(source_nodes) < minimum_source_count:
            continue
        safe_members = [
            occurrence
            for occurrence in group["members"]
            if group["member_source_node_by_occurrence"][occurrence]
            in safe_source_node_ids
        ]
        contextual_entity_id = stable_identifier(
            "contextual-entity",
            {
                "scope": merge_scope,
                "anchor_id": group_key[0],
                "direction_scope": group_key[1],
                "predicate_scope": group_key[2],
                "normalized_name": group_key[3],
                "entity_type": group_key[4],
            },
        )
        display_names = sorted(
            group["display_names"],
            key=lambda value: (len(value), value.casefold()),
        )
        contextual_entity_metadata[contextual_entity_id] = {
            **group,
            "source_nodes": source_nodes,
            "members": safe_members,
            "display_name": display_names[0],
            "context_direction": (
                next(iter(group["directions"]))
                if len(group["directions"]) == 1
                else "MULTIPLE"
            ),
            "context_predicate": (
                next(iter(group["predicates"]))
                if len(group["predicates"]) == 1
                else "MULTIPLE"
            ),
        }
        for member in safe_members:
            contextual_entity_id_by_endpoint[member] = contextual_entity_id

    selected_facts: list[dict[str, Any]] = []
    selected_fact_ids: set[str] = set()
    for item in selected_fact_inputs:
        fact = item["fact"]
        decision = item["decision"]
        subject_kind = item["subject_kind"]
        object_kind = item["object_kind"]
        subject_node_id = item["subject_identity_node_id"]
        object_node_id = item["object_identity_node_id"]
        predicate = item["predicate"]

        subject_entity_id = subject_node_id
        if subject_kind != resolved_node_kind:
            subject_entity_id = contextual_entity_id_by_endpoint.get(
                (fact["fact_id"], "subject"),
                f"provisional:{subject_kind.casefold()}:{subject_node_id}",
            )
        object_entity_id = object_node_id
        if object_kind != resolved_node_kind:
            object_entity_id = contextual_entity_id_by_endpoint.get(
                (fact["fact_id"], "object"),
                f"provisional:{object_kind.casefold()}:{object_node_id}",
            )

        endpoints_resolved = (
            subject_kind == resolved_node_kind
            and object_kind == resolved_node_kind
        )
        relation_status = "VERIFIED"
        review_model = ""
        review_rationale = ""
        review_reason_codes_json = "[]"
        if decision is not None:
            relation_status = "REVIEWED_APPROVED"
            review_model = decision["evaluation_model"]
            review_rationale = decision["evaluation_rationale"]
            review_reason_codes_json = decision["evaluation_reason_codes"]

        selected_fact_ids.add(fact["fact_id"])
        selected_facts.append(
            {
                "fact_id": fact["fact_id"],
                "subject_entity_id": subject_entity_id,
                "subject_node_kind": subject_kind,
                "subject_source_node_id": fact["subject_node_id"],
                "subject_identity_node_id": subject_node_id,
                "subject_endpoint_resolution_method": item[
                    "subject_endpoint_resolution_method"
                ],
                "predicate": predicate,
                "object_entity_id": object_entity_id,
                "object_node_kind": object_kind,
                "object_source_node_id": fact["object_node_id"],
                "object_identity_node_id": object_node_id,
                "object_endpoint_resolution_method": item[
                    "object_endpoint_resolution_method"
                ],
                "assertion_count": fact["assertion_count"],
                "relation_status": relation_status,
                "endpoint_status": (
                    "RESOLVED" if endpoints_resolved else "UNRESOLVED"
                ),
                "retrieval_eligible": str(
                    endpoints_resolved
                    and fact["default_retrieval_eligible"] == "True"
                ).lower(),
                "candidate_retrieval_eligible": "true",
                "terminal_retrieval_eligible": str(
                    bool(terminal_fact_retrieval["enabled"])
                    and (
                        subject_kind == resolved_node_kind
                        or object_kind == resolved_node_kind
                    )
                ).lower(),
                "multi_hop_eligible": str(endpoints_resolved).lower(),
                "evidence_ids_json": fact["evidence_ids_json"],
                "source_datasets_json": fact["source_datasets_json"],
                "candidate_tiers_json": fact["candidate_tiers_json"],
                "source_predicates_json": item[
                    "source_predicates_json"
                ],
                "raw_relation_types_json": item[
                    "raw_relation_types_json"
                ],
                "relation_qualifiers_json": item[
                    "relation_qualifiers_json"
                ],
                "endpoint_projection_status": "",
                "endpoint_projection_reference_fact_id": "",
                "review_model": review_model,
                "review_rationale": review_rationale,
                "review_reason_codes_json": review_reason_codes_json,
                "graph_release_id": graph_release_id,
            }
        )

    expected_fact_count = (
        sum(
            1
            for fact in fact_by_id.values()
            if fact["trust_status"] == config["existing_fact_status"]
        )
        + len(accepted_decision_by_fact_id)
        - len(quarantined_facts)
    )
    if len(selected_facts) != expected_fact_count:
        raise ValueError(
            f"Selected fact count mismatch: "
            f"{len(selected_facts)} != {expected_fact_count}"
        )
    if len(selected_fact_ids) != len(selected_facts):
        raise ValueError("Selected fact IDs are not unique")

    projected_entity_metadata: dict[str, dict[str, str]] = {
        canonical_id: {
            "display_name": row["display_name"],
            "entity_type": row["entity_type"],
        }
        for canonical_id, row in canonical_by_id.items()
    }
    for entity_id, metadata in contextual_entity_metadata.items():
        projected_entity_metadata[entity_id] = {
            "display_name": metadata["display_name"],
            "entity_type": metadata["entity_type"],
        }
    for fact in selected_facts:
        for side in ("subject", "object"):
            entity_id = fact[f"{side}_entity_id"]
            if entity_id in projected_entity_metadata:
                continue
            source_node_id = fact[f"{side}_identity_node_id"]
            metadata = endpoint_metadata.get(source_node_id, {})
            graph_node = graph_node_by_id.get(source_node_id, {})
            projected_entity_metadata[entity_id] = {
                "display_name": (
                    metadata.get("display_name")
                    or graph_node.get("display_name")
                    or source_node_id
                ),
                "entity_type": (
                    metadata.get("entity_type")
                    or graph_node.get("entity_type")
                    or "Unknown"
                ),
            }

    if bool(fact_projection_deduplication["enabled"]):
        projection_groups: dict[
            tuple[
                str,
                tuple[tuple[str, str], tuple[str, str]],
            ],
            list[dict[str, Any]],
        ] = {}
        for fact in selected_facts:
            subject_metadata = projected_entity_metadata[
                fact["subject_entity_id"]
            ]
            object_metadata = projected_entity_metadata[
                fact["object_entity_id"]
            ]
            subject_token = (
                normalize_endpoint_display_name(
                    subject_metadata["display_name"]
                ),
                subject_metadata["entity_type"],
            )
            object_token = (
                normalize_endpoint_display_name(
                    object_metadata["display_name"]
                ),
                object_metadata["entity_type"],
            )
            if not subject_token[0] or not object_token[0]:
                continue
            signature = (subject_token, object_token)
            if fact["predicate"] in symmetric_predicates:
                signature = tuple(sorted(signature))
            projection_key = (fact["predicate"], signature)
            projection_groups.setdefault(projection_key, []).append(fact)

        for (predicate, _), duplicate_facts in projection_groups.items():
            if len(duplicate_facts) < 2:
                continue
            canonical_facts = [
                fact
                for fact in duplicate_facts
                if fact["subject_entity_id"] in canonical_by_id
                and fact["object_entity_id"] in canonical_by_id
            ]
            if (
                bool(
                    fact_projection_deduplication[
                        "require_canonical_representative"
                    ]
                )
                and not canonical_facts
            ):
                continue
            if not canonical_facts:
                continue

            canonical_facts_by_identity_pair: dict[
                tuple[str, str],
                list[dict[str, Any]],
            ] = {}
            canonical_evidence_by_identity_pair: dict[
                tuple[str, str],
                set[str],
            ] = {}
            for canonical_fact in canonical_facts:
                identity_pair = (
                    canonical_fact["subject_entity_id"],
                    canonical_fact["object_entity_id"],
                )
                if predicate in symmetric_predicates:
                    identity_pair = tuple(sorted(identity_pair))
                canonical_facts_by_identity_pair.setdefault(
                    identity_pair,
                    [],
                ).append(canonical_fact)
                canonical_evidence_by_identity_pair.setdefault(
                    identity_pair,
                    set(),
                ).update(
                    json.loads(canonical_fact["evidence_ids_json"])
                )

            for fact in duplicate_facts:
                fact_evidence_ids = set(
                    json.loads(fact["evidence_ids_json"])
                )
                if not fact_evidence_ids:
                    continue
                matching_identity_pairs = {
                    identity_pair
                    for identity_pair, canonical_evidence_ids
                    in canonical_evidence_by_identity_pair.items()
                    if fact_evidence_ids & canonical_evidence_ids
                }
                if len(matching_identity_pairs) != 1:
                    continue
                identity_pair = next(iter(matching_identity_pairs))
                representative_fact = min(
                    canonical_facts_by_identity_pair[identity_pair],
                    key=lambda row: row["fact_id"],
                )
                representative_pair = (
                    representative_fact["subject_entity_id"],
                    representative_fact["object_entity_id"],
                )
                representative_identity_pair = representative_pair
                if predicate in symmetric_predicates:
                    representative_identity_pair = tuple(
                        sorted(representative_pair)
                    )
                current_pair = (
                    fact["subject_entity_id"],
                    fact["object_entity_id"],
                )
                normalized_current_pair = current_pair
                if predicate in symmetric_predicates:
                    normalized_current_pair = tuple(
                        sorted(current_pair)
                    )
                if (
                    normalized_current_pair
                    == representative_identity_pair
                    and current_pair == representative_pair
                ):
                    continue
                fact["subject_entity_id"] = representative_pair[0]
                fact["object_entity_id"] = representative_pair[1]
                fact["endpoint_status"] = representative_fact[
                    "endpoint_status"
                ]
                fact["retrieval_eligible"] = representative_fact[
                    "retrieval_eligible"
                ]
                fact["candidate_retrieval_eligible"] = (
                    representative_fact[
                        "candidate_retrieval_eligible"
                    ]
                )
                fact["terminal_retrieval_eligible"] = (
                    representative_fact[
                        "terminal_retrieval_eligible"
                    ]
                )
                fact["multi_hop_eligible"] = representative_fact[
                    "multi_hop_eligible"
                ]
                fact["endpoint_projection_status"] = (
                    "CANONICAL_DUPLICATE_COLLAPSED"
                )
                fact["endpoint_projection_reference_fact_id"] = (
                    representative_fact["fact_id"]
                )

    for fact in selected_facts:
        has_canonical_endpoint = (
            fact["subject_entity_id"] in canonical_by_id
            or fact["object_entity_id"] in canonical_by_id
        )
        fact["terminal_retrieval_eligible"] = str(
            bool(terminal_fact_retrieval["enabled"])
            and has_canonical_endpoint
        ).lower()

    used_contextual_entity_ids = {
        entity_id
        for fact in selected_facts
        for entity_id in (
            fact["subject_entity_id"],
            fact["object_entity_id"],
        )
        if entity_id in contextual_entity_metadata
    }
    contextual_entity_metadata = {
        entity_id: metadata
        for entity_id, metadata in contextual_entity_metadata.items()
        if entity_id in used_contextual_entity_ids
    }

    semantic_relation_groups: dict[
        tuple[str, str, str],
        list[dict[str, Any]],
    ] = {}
    for fact in selected_facts:
        subject_entity_id = fact["subject_entity_id"]
        object_entity_id = fact["object_entity_id"]
        is_symmetric = (
            bool(
                fact_projection_deduplication[
                    "collapse_symmetric_semantic_relations"
                ]
            )
            and fact["predicate"] in symmetric_predicates
        )
        if is_symmetric:
            (
                subject_entity_id,
                object_entity_id,
            ) = sorted((subject_entity_id, object_entity_id))
        relation_key = (
            subject_entity_id,
            fact["predicate"],
            object_entity_id,
        )
        semantic_relation_groups.setdefault(relation_key, []).append(fact)

    semantic_relations: list[dict[str, Any]] = []
    for relation_key, group_facts in semantic_relation_groups.items():
        relation_statuses = sorted(
            {fact["relation_status"] for fact in group_facts}
        )
        evidence_ids = sorted(
            {
                evidence_id
                for fact in group_facts
                for evidence_id in json.loads(fact["evidence_ids_json"])
            }
        )
        source_datasets = sorted(
            {
                source_dataset
                for fact in group_facts
                for source_dataset in json.loads(
                    fact["source_datasets_json"]
                )
            }
        )
        review_models = sorted(
            {
                fact["review_model"]
                for fact in group_facts
                if fact["review_model"]
            }
        )
        source_predicates = sorted(
            {
                source_predicate
                for fact in group_facts
                for source_predicate in json.loads(
                    fact["source_predicates_json"]
                )
            }
        )
        raw_relation_types = sorted(
            {
                raw_relation_type
                for fact in group_facts
                for raw_relation_type in json.loads(
                    fact["raw_relation_types_json"]
                )
            }
        )
        relation_qualifiers = sorted(
            {
                qualifier
                for fact in group_facts
                for qualifier in json.loads(
                    fact["relation_qualifiers_json"]
                )
            }
        )
        fact_ids = sorted(fact["fact_id"] for fact in group_facts)
        semantic_relations.append(
            {
                "semantic_relation_id": stable_identifier(
                    "semantic-relation",
                    {
                        "subject_entity_id": relation_key[0],
                        "predicate": relation_key[1],
                        "object_entity_id": relation_key[2],
                    },
                ),
                "subject_entity_id": relation_key[0],
                "predicate": relation_key[1],
                "object_entity_id": relation_key[2],
                "directionality": (
                    "SYMMETRIC"
                    if relation_key[1] in symmetric_predicates
                    else "DIRECTED"
                ),
                "representative_fact_id": fact_ids[0],
                "fact_ids_json": json.dumps(
                    fact_ids,
                    ensure_ascii=False,
                ),
                "fact_count": len(fact_ids),
                "assertion_count": sum(
                    int(fact["assertion_count"])
                    for fact in group_facts
                ),
                "relation_status": (
                    relation_statuses[0]
                    if len(relation_statuses) == 1
                    else "MIXED"
                ),
                "relation_statuses_json": json.dumps(
                    relation_statuses,
                    ensure_ascii=False,
                ),
                "endpoint_status": (
                    "RESOLVED"
                    if all(
                        fact["endpoint_status"] == "RESOLVED"
                        for fact in group_facts
                    )
                    else "UNRESOLVED"
                ),
                "retrieval_eligible": str(
                    any(
                        fact["retrieval_eligible"] == "true"
                        for fact in group_facts
                    )
                ).lower(),
                "candidate_retrieval_eligible": str(
                    any(
                        fact["candidate_retrieval_eligible"] == "true"
                        for fact in group_facts
                    )
                ).lower(),
                "terminal_retrieval_eligible": str(
                    any(
                        fact["terminal_retrieval_eligible"] == "true"
                        for fact in group_facts
                    )
                ).lower(),
                "multi_hop_eligible": str(
                    any(
                        fact["multi_hop_eligible"] == "true"
                        for fact in group_facts
                    )
                ).lower(),
                "evidence_ids_json": json.dumps(
                    evidence_ids,
                    ensure_ascii=False,
                ),
                "source_datasets_json": json.dumps(
                    source_datasets,
                    ensure_ascii=False,
                ),
                "source_predicates_json": json.dumps(
                    source_predicates,
                    ensure_ascii=False,
                ),
                "raw_relation_types_json": json.dumps(
                    raw_relation_types,
                    ensure_ascii=False,
                ),
                "relation_qualifiers_json": json.dumps(
                    relation_qualifiers,
                    ensure_ascii=False,
                ),
                "review_models_json": json.dumps(
                    review_models,
                    ensure_ascii=False,
                ),
                "graph_release_id": graph_release_id,
            }
        )

    entities: dict[str, dict[str, Any]] = {}
    for canonical in canonical_rows:
        canonical_id = canonical["canonical_id"]
        active = canonical["lifecycle_status"] == "ACTIVE"
        entities[canonical_id] = {
            "entity_id": canonical_id,
            "entity_kind": "CANONICAL",
            "display_name": canonical["display_name"],
            "normalized_search_text": normalize_search_text(
                canonical["display_name"]
            ),
            "exact_search_eligible": "false",
            "exact_search_status": "PENDING_POLICY",
            "exact_search_candidate_count": 0,
            "entity_type": canonical["entity_type"],
            "resolution_status": "RESOLVED",
            "retrieval_eligible": str(active).lower(),
            "anchor_eligible": str(active).lower(),
            "multi_hop_eligible": str(active).lower(),
            "source_node_kind": "CANONICAL",
            "source_node_id": canonical_id,
            "source_node_ids_json": json.dumps([canonical_id]),
            "source_node_kinds_json": json.dumps(["CANONICAL"]),
            "source_member_count": 1,
            "merge_scope": "NONE",
            "context_anchor_id": "",
            "context_direction": "",
            "context_predicate": "",
            "context_directions_json": "[]",
            "context_predicates_json": "[]",
            "lifecycle_status": canonical["lifecycle_status"],
            "identity_confidence": canonical["identity_confidence"],
            "source_support_count": canonical["source_support_count"],
            "graph_release_id": graph_release_id,
        }

    for entity_id, metadata in contextual_entity_metadata.items():
        source_nodes = metadata["source_nodes"]
        source_node_ids = sorted(source_nodes)
        source_node_kinds = sorted(set(source_nodes.values()))
        entities[entity_id] = {
            "entity_id": entity_id,
            "entity_kind": "PROVISIONAL",
            "display_name": metadata["display_name"],
            "normalized_search_text": metadata["normalized_name"],
            "exact_search_eligible": "false",
            "exact_search_status": "PROVISIONAL_BLOCKED",
            "exact_search_candidate_count": 0,
            "entity_type": metadata["entity_type"],
            "resolution_status": "UNRESOLVED",
            "retrieval_eligible": "false",
            "anchor_eligible": "false",
            "multi_hop_eligible": "false",
            "source_node_kind": "CONTEXTUAL_GROUP",
            "source_node_id": "",
            "source_node_ids_json": json.dumps(
                source_node_ids,
                ensure_ascii=False,
            ),
            "source_node_kinds_json": json.dumps(
                source_node_kinds,
                ensure_ascii=False,
            ),
            "source_member_count": len(source_node_ids),
            "merge_scope": merge_scope,
            "context_anchor_id": metadata["anchor_id"],
            "context_direction": metadata["context_direction"],
            "context_predicate": metadata["context_predicate"],
            "context_directions_json": json.dumps(
                sorted(metadata["directions"]),
                ensure_ascii=False,
            ),
            "context_predicates_json": json.dumps(
                sorted(metadata["predicates"]),
                ensure_ascii=False,
            ),
            "lifecycle_status": "PROVISIONAL",
            "identity_confidence": "",
            "source_support_count": "",
            "graph_release_id": graph_release_id,
        }

    for fact in selected_facts:
        endpoint_values = (
            (
                fact["subject_entity_id"],
                fact["subject_node_kind"],
                fact["subject_identity_node_id"],
            ),
            (
                fact["object_entity_id"],
                fact["object_node_kind"],
                fact["object_identity_node_id"],
            ),
        )
        for entity_id, node_kind, fallback_node_id in endpoint_values:
            if entity_id in canonical_by_id:
                continue
            if node_kind == resolved_node_kind:
                raise ValueError(
                    f"Canonical endpoint is absent from registry: {entity_id}"
                )
            if entity_id in contextual_entity_metadata:
                continue
            raw_node_id = fallback_node_id
            metadata = endpoint_metadata.get(raw_node_id)
            graph_node = graph_node_by_id.get(raw_node_id)
            if metadata is None and graph_node is None:
                raise ValueError(
                    f"Missing provisional endpoint metadata: {raw_node_id}"
                )
            display_name = raw_node_id
            entity_type = "Unknown"
            if graph_node is not None:
                display_name = graph_node["display_name"] or raw_node_id
                entity_type = graph_node["entity_type"] or "Unknown"
            if metadata is not None:
                display_name = metadata["display_name"] or display_name
                entity_type = metadata["entity_type"] or entity_type
            entities[entity_id] = {
                "entity_id": entity_id,
                "entity_kind": "PROVISIONAL",
                "display_name": display_name,
                "normalized_search_text": normalize_search_text(display_name),
                "exact_search_eligible": "false",
                "exact_search_status": "PROVISIONAL_BLOCKED",
                "exact_search_candidate_count": 0,
                "entity_type": entity_type,
                "resolution_status": "UNRESOLVED",
                "retrieval_eligible": "false",
                "anchor_eligible": "false",
                "multi_hop_eligible": "false",
                "source_node_kind": node_kind,
                "source_node_id": raw_node_id,
                "source_node_ids_json": json.dumps([raw_node_id]),
                "source_node_kinds_json": json.dumps([node_kind]),
                "source_member_count": 1,
                "merge_scope": "NONE",
                "context_anchor_id": "",
                "context_direction": "",
                "context_predicate": "",
                "context_directions_json": "[]",
                "context_predicates_json": "[]",
                "lifecycle_status": "PROVISIONAL",
                "identity_confidence": "",
                "source_support_count": "",
                "graph_release_id": graph_release_id,
            }

    entity_ids_by_source_node_id: dict[str, set[str]] = {}
    for entity in entities.values():
        for source_node_id in json.loads(
            entity["source_node_ids_json"]
        ):
            entity_ids_by_source_node_id.setdefault(
                source_node_id,
                set(),
            ).add(entity["entity_id"])
    duplicated_source_node_ids = {
        source_node_id: entity_ids
        for source_node_id, entity_ids
        in entity_ids_by_source_node_id.items()
        if len(entity_ids) > 1
    }
    if duplicated_source_node_ids:
        raise ValueError(
            "A source node is represented by multiple GraphEntity nodes: "
            + json.dumps(
                {
                    source_node_id: sorted(entity_ids)
                    for source_node_id, entity_ids
                    in duplicated_source_node_ids.items()
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    for quarantined_fact in quarantined_facts:
        evidence_ids = json.loads(
            quarantined_fact["evidence_ids_json"]
        )
        missing_evidence_ids = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in evidence_by_id
        ]
        if missing_evidence_ids:
            raise ValueError(
                "Missing quarantined fact evidence: "
                + ", ".join(missing_evidence_ids)
            )
        quarantined_fact["evidence_records_json"] = json.dumps(
            [
                evidence_by_id[evidence_id]
                for evidence_id in evidence_ids
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
    selected_evidence_ids: set[str] = set()
    fact_evidence_links: list[dict[str, Any]] = []
    for fact in selected_facts:
        evidence_ids = json.loads(fact["evidence_ids_json"])
        for evidence_id in evidence_ids:
            if evidence_id not in evidence_by_id:
                raise ValueError(
                    f"Missing evidence {evidence_id}: {fact['fact_id']}"
                )
            selected_evidence_ids.add(evidence_id)
            fact_evidence_links.append(
                {
                    "fact_id": fact["fact_id"],
                    "evidence_id": evidence_id,
                    "graph_release_id": graph_release_id,
                }
            )
    evidence_rows = [
        {
            **evidence_by_id[evidence_id],
            "graph_release_id": graph_release_id,
        }
        for evidence_id in sorted(selected_evidence_ids)
    ]

    source_records: dict[str, dict[str, Any]] = {
        row["source_record_id"]: {
            **row,
            "identity_status": "ACTIVE",
            "identity_reason_code": "",
            "preferred_source_node_id": "",
            "identity_evidence_urls_json": "[]",
            "graph_release_id": graph_release_id,
        }
        for row in read_csv_rows(input_paths["source_records"])
    }
    evidence_source_links: list[dict[str, Any]] = []
    for evidence in evidence_rows:
        source_record_id = evidence["source_record_id"]
        if source_record_id:
            source_records[source_record_id] = {
                "source_record_id": source_record_id,
                "source": evidence["source_dataset"],
                "source_key": evidence["source_document_id"],
                "source_release": evidence["source_release"],
                "source_metadata_json": json.dumps(
                    {
                        "source_url": evidence["source_url"],
                        "evidence_kind": evidence["evidence_kind"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "identity_status": "ACTIVE",
                "identity_reason_code": "",
                "preferred_source_node_id": "",
                "identity_evidence_urls_json": "[]",
                "graph_release_id": graph_release_id,
            }
            evidence_source_links.append(
                {
                    "evidence_id": evidence["evidence_id"],
                    "source_record_id": source_record_id,
                    "graph_release_id": graph_release_id,
                }
            )

    provisional_source_links: list[dict[str, Any]] = []
    for entity in entities.values():
        source_node_ids = json.loads(entity["source_node_ids_json"])
        source_node_kinds = json.loads(entity["source_node_kinds_json"])
        source_kind_by_id: dict[str, str] = {}
        if entity["source_node_kind"] == "CONTEXTUAL_GROUP":
            source_kind_by_id = contextual_entity_metadata[
                entity["entity_id"]
            ]["source_nodes"]
        elif source_node_ids:
            source_kind_by_id[source_node_ids[0]] = source_node_kinds[0]
        for source_record_id, source_node_kind in source_kind_by_id.items():
            if source_node_kind not in provisional_source_kinds:
                continue
            if source_record_id not in source_records:
                source_records[source_record_id] = {
                    "source_record_id": source_record_id,
                    "source": source_node_kind,
                    "source_key": source_record_id,
                    "source_release": "",
                    "source_metadata_json": "{}",
                    "identity_status": "ACTIVE",
                    "identity_reason_code": "",
                    "preferred_source_node_id": "",
                    "identity_evidence_urls_json": "[]",
                    "graph_release_id": graph_release_id,
                }
            provisional_source_links.append(
                {
                    "entity_id": entity["entity_id"],
                    "source_record_id": source_record_id,
                    "graph_release_id": graph_release_id,
                }
            )

    for fact in selected_facts:
        source_endpoints = (
            (
                fact["subject_source_node_id"],
                fact["subject_node_kind"],
            ),
            (
                fact["object_source_node_id"],
                fact["object_node_kind"],
            ),
        )
        for source_record_id, source_node_kind in source_endpoints:
            if source_node_kind not in provisional_source_kinds:
                continue
            if source_record_id in source_records:
                continue
            source_records[source_record_id] = {
                "source_record_id": source_record_id,
                "source": source_node_kind,
                "source_key": source_record_id,
                "source_release": "",
                "source_metadata_json": "{}",
                "identity_status": "ACTIVE",
                "identity_reason_code": "",
                "preferred_source_node_id": "",
                "identity_evidence_urls_json": "[]",
                "graph_release_id": graph_release_id,
            }

    identity_conflicts: list[dict[str, Any]] = []
    for source_node_id, decision in identity_decision_by_source_node_id.items():
        graph_node = graph_node_by_id.get(source_node_id, {})
        current_source_record = source_records.get(source_node_id, {})
        identity_status = "ACTIVE"
        if decision["decision"] == "QUARANTINE":
            identity_status = "SOURCE_CONFLICT"
        elif decision["decision"] == "REDIRECT":
            identity_status = "REDIRECTED"
        source_records[source_node_id] = {
            "source_record_id": source_node_id,
            "source": (
                current_source_record.get("source")
                or decision["source"]
            ),
            "source_key": (
                current_source_record.get("source_key")
                or decision["source_key"]
            ),
            "source_release": current_source_record.get(
                "source_release",
                "",
            ),
            "source_metadata_json": current_source_record.get(
                "source_metadata_json",
                "{}",
            ),
            "identity_status": identity_status,
            "identity_reason_code": decision["reason_code"],
            "preferred_source_node_id": decision[
                "preferred_source_node_id"
            ],
            "identity_evidence_urls_json": decision[
                "evidence_urls_json"
            ],
            "graph_release_id": graph_release_id,
        }
        identity_conflicts.append(
            {
                **decision,
                "display_name": graph_node.get("display_name", ""),
                "identity_status": identity_status,
                "graph_release_id": graph_release_id,
            }
        )

    entity_name_rows = read_csv_rows(input_paths["entity_names"])
    entity_name_links = [
        {
            **row,
            "graph_release_id": graph_release_id,
        }
        for row in read_csv_rows(input_paths["entity_name_links"])
        if row["match_status"] == accepted_match_status
        and row["canonical_id"] in canonical_by_id
    ]
    exam_term_rows = read_csv_rows(input_paths["exam_terms"])
    exam_term_links = [
        {
            **row,
            "graph_release_id": graph_release_id,
        }
        for row in read_csv_rows(input_paths["exam_term_links"])
        if row["match_status"] == accepted_match_status
        and row["canonical_id"] in canonical_by_id
    ]
    if bool(
        canonical_endpoint_resolution[
            "promote_unique_exam_term_alias_links"
        ]
    ):
        linked_exam_term_ids = {
            row["exam_term_id"]
            for row in exam_term_links
        }
        for exam_term in exam_term_rows:
            exam_term_id = exam_term["exam_term_id"]
            if exam_term_id in linked_exam_term_ids:
                continue
            proposed_entity_types = json.loads(
                exam_term["entity_type_proposals_json"] or "[]"
            )
            if len(proposed_entity_types) != 1:
                continue
            normalized_term = normalize_endpoint_display_name(
                exam_term["term"]
            )
            name_key = (
                normalized_term,
                str(proposed_entity_types[0]),
            )
            (
                canonical_id,
                resolution_method,
            ) = resolve_unique_name_type(
                name_key[0],
                name_key[1],
            )
            if not canonical_id:
                continue
            exam_term_links.append(
                {
                    "exam_term_id": exam_term_id,
                    "canonical_id": canonical_id,
                    "match_status": accepted_match_status,
                    "method": canonical_endpoint_resolution[
                        "generated_match_method"
                    ],
                    "version": canonical_endpoint_resolution[
                        "generated_match_version"
                    ],
                    "term_decision_id": stable_identifier(
                        "term-decision",
                        {
                            "exam_term_id": exam_term_id,
                            "canonical_id": canonical_id,
                            "resolution_method": resolution_method,
                            "method": canonical_endpoint_resolution[
                                "generated_match_method"
                            ],
                        },
                    ),
                    "graph_release_id": graph_release_id,
                }
            )
            linked_exam_term_ids.add(exam_term_id)
    canonical_ids_by_normalized_name: dict[str, set[str]] = {}
    for canonical in canonical_rows:
        if canonical["lifecycle_status"] != "ACTIVE":
            continue
        normalized_name = normalize_search_text(canonical["display_name"])
        canonical_ids_by_normalized_name.setdefault(
            normalized_name,
            set(),
        ).add(canonical["canonical_id"])

    exam_term_by_id = {
        row["exam_term_id"]: row
        for row in exam_term_rows
    }
    exam_term_targets_by_id: dict[str, set[str]] = {}
    exact_exam_targets_by_normalized_name: dict[str, set[str]] = {}
    for link in exam_term_links:
        term_id = link["exam_term_id"]
        canonical_id = link["canonical_id"]
        exam_term_targets_by_id.setdefault(term_id, set()).add(canonical_id)
        term = exam_term_by_id[term_id]
        normalized_term = normalize_search_text(
            term.get("normalized_term") or term["term"]
        )
        if canonical_id not in canonical_ids_by_normalized_name.get(
            normalized_term,
            set(),
        ):
            continue
        exact_exam_targets_by_normalized_name.setdefault(
            normalized_term,
            set(),
        ).add(canonical_id)

    if bool(exact_search_policy["enabled"]):
        for normalized_name, canonical_ids in (
            canonical_ids_by_normalized_name.items()
        ):
            preferred_targets = exact_exam_targets_by_normalized_name.get(
                normalized_name,
                set(),
            )
            preferred_canonical_id = ""
            if (
                bool(
                    exact_search_policy[
                        "prefer_unique_accepted_exam_term_target"
                    ]
                )
                and len(preferred_targets) == 1
            ):
                preferred_canonical_id = next(iter(preferred_targets))
            for canonical_id in canonical_ids:
                entity = entities[canonical_id]
                entity["exact_search_candidate_count"] = len(canonical_ids)
                if len(canonical_ids) == 1:
                    entity["exact_search_eligible"] = "true"
                    entity["exact_search_status"] = "UNIQUE"
                elif canonical_id == preferred_canonical_id:
                    entity["exact_search_eligible"] = "true"
                    entity["exact_search_status"] = "EXAM_TERM_PREFERRED"
                elif bool(
                    exact_search_policy[
                        "suppress_ambiguous_canonical_names"
                    ]
                ):
                    entity["exact_search_eligible"] = "false"
                    entity["exact_search_status"] = "AMBIGUOUS_SUPPRESSED"
                else:
                    entity["exact_search_eligible"] = "true"
                    entity["exact_search_status"] = "AMBIGUOUS_ALLOWED"
        for canonical in canonical_rows:
            if canonical["lifecycle_status"] == "ACTIVE":
                continue
            entity = entities[canonical["canonical_id"]]
            entity["exact_search_eligible"] = "false"
            entity["exact_search_status"] = "INACTIVE"

    linked_entity_name_ids = {
        row["entity_name_id"]
        for row in entity_name_links
    }
    entity_name_by_id = {
        row["entity_name_id"]: row
        for row in entity_name_rows
    }
    entity_name_targets_by_id: dict[str, set[str]] = {}
    entity_name_targets_by_normalized_name: dict[str, set[str]] = {}
    for link in entity_name_links:
        entity_name_id = link["entity_name_id"]
        canonical_id = link["canonical_id"]
        entity_name_targets_by_id.setdefault(
            entity_name_id,
            set(),
        ).add(canonical_id)
        entity_name = entity_name_by_id[entity_name_id]
        normalized_name = normalize_search_text(
            entity_name.get("normalized_name") or entity_name["name"]
        )
        entity_name_targets_by_normalized_name.setdefault(
            normalized_name,
            set(),
        ).add(canonical_id)

    entity_names: list[dict[str, Any]] = []
    for row in entity_name_rows:
        entity_name_id = row["entity_name_id"]
        if entity_name_id not in linked_entity_name_ids:
            continue
        normalized_name = normalize_search_text(
            row.get("normalized_name") or row["name"]
        )
        target_count = len(
            entity_name_targets_by_normalized_name.get(
                normalized_name,
                set(),
            )
        )
        exact_search_eligible = (
            bool(exact_search_policy["enabled"])
            and target_count == 1
        )
        entity_names.append(
            {
                **row,
                "search_text": row["name"],
                "target_count": target_count,
                "target_resolution_status": (
                    "UNIQUE" if target_count == 1 else "AMBIGUOUS"
                ),
                "exact_search_eligible": str(
                    exact_search_eligible
                ).lower(),
                "retrieval_eligible": str(
                    exact_search_eligible
                ).lower(),
                "graph_release_id": graph_release_id,
            }
        )

    default_fact_entity_ids = {
        entity_id
        for relation in semantic_relations
        if relation["retrieval_eligible"] == "true"
        for entity_id in (
            relation["subject_entity_id"],
            relation["object_entity_id"],
        )
        if entity_id in canonical_by_id
    }
    terminal_fact_entity_ids = {
        entity_id
        for relation in semantic_relations
        if relation["terminal_retrieval_eligible"] == "true"
        for entity_id in (
            relation["subject_entity_id"],
            relation["object_entity_id"],
        )
        if entity_id in canonical_by_id
    }
    exam_terms: list[dict[str, Any]] = []
    for row in exam_term_rows:
        target_ids = exam_term_targets_by_id.get(
            row["exam_term_id"],
            set(),
        )
        target_count = len(target_ids)
        target_resolution_status = "UNRESOLVED"
        if target_count == 1:
            target_resolution_status = "UNIQUE"
        elif target_count > 1:
            target_resolution_status = "AMBIGUOUS"
        exact_search_eligible = (
            bool(exact_search_policy["enabled"])
            and target_count == 1
        )
        target_id = next(iter(target_ids)) if target_count == 1 else ""
        exam_terms.append(
            {
                **row,
                "search_text": row["term"],
                "resolution_status": (
                    "RESOLVED"
                    if target_count == 1
                    else target_resolution_status
                ),
                "target_count": target_count,
                "target_resolution_status": target_resolution_status,
                "exact_search_eligible": str(
                    exact_search_eligible
                ).lower(),
                "retrieval_eligible": str(
                    exact_search_eligible
                ).lower(),
                "fact_retrieval_eligible": str(
                    exact_search_eligible
                    and target_id in default_fact_entity_ids
                ).lower(),
                "terminal_fact_retrieval_eligible": str(
                    exact_search_eligible
                    and target_id in terminal_fact_entity_ids
                ).lower(),
                "graph_release_id": graph_release_id,
            }
        )

    source_resolution_links = [
        {
            **row,
            "graph_release_id": graph_release_id,
        }
        for row in read_csv_rows(input_paths["source_resolution_links"])
        if row["match_status"] == accepted_match_status
        and row["canonical_id"] in canonical_by_id
    ]

    accepted_classification_status = str(
        config["accepted_classification_status"]
    )
    topics = [
        {
            **row,
            "graph_release_id": graph_release_id,
        }
        for row in read_csv_rows(input_paths["topics"])
    ]
    eras = [
        {
            **row,
            "graph_release_id": graph_release_id,
        }
        for row in read_csv_rows(input_paths["eras"])
    ]
    entity_topic_links = [
        {
            **row,
            "graph_release_id": graph_release_id,
        }
        for row in read_csv_rows(input_paths["entity_topic_links"])
        if row["verification_status"] == accepted_classification_status
        and row["canonical_id"] in canonical_by_id
    ]
    entity_era_links = [
        {
            **row,
            "graph_release_id": graph_release_id,
        }
        for row in read_csv_rows(input_paths["entity_era_links"])
        if row["verification_status"] == accepted_classification_status
        and row["canonical_id"] in canonical_by_id
    ]

    entity_type_links = [
        {
            "entity_id": entity["entity_id"],
            "entity_type": entity["entity_type"],
            "graph_release_id": graph_release_id,
        }
        for entity in entities.values()
        if entity["entity_type"] not in {"", "Unknown"}
    ]

    return {
        "entities": sorted(
            entities.values(),
            key=lambda row: row["entity_id"],
        ),
        "facts": sorted(
            selected_facts,
            key=lambda row: row["fact_id"],
        ),
        "semantic_relations": sorted(
            semantic_relations,
            key=lambda row: row["semantic_relation_id"],
        ),
        "evidence": evidence_rows,
        "source_records": sorted(
            source_records.values(),
            key=lambda row: row["source_record_id"],
        ),
        "identity_conflicts": sorted(
            identity_conflicts,
            key=lambda row: row["source_node_id"],
        ),
        "quarantined_facts": sorted(
            quarantined_facts,
            key=lambda row: row["fact_id"],
        ),
        "entity_names": sorted(
            entity_names,
            key=lambda row: row["entity_name_id"],
        ),
        "exam_terms": sorted(
            exam_terms,
            key=lambda row: row["exam_term_id"],
        ),
        "topics": sorted(topics, key=lambda row: row["topic_id"]),
        "eras": sorted(eras, key=lambda row: row["era_id"]),
        "fact_evidence_links": fact_evidence_links,
        "evidence_source_links": evidence_source_links,
        "provisional_source_links": provisional_source_links,
        "entity_name_links": entity_name_links,
        "exam_term_links": exam_term_links,
        "source_resolution_links": source_resolution_links,
        "entity_topic_links": entity_topic_links,
        "entity_era_links": entity_era_links,
        "entity_type_links": entity_type_links,
    }


def write_fact_graph_release(
    package: dict[str, list[dict[str, Any]]],
    output_directory: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    fieldnames = {
        "entities": [
            "entity_id",
            "entity_kind",
            "display_name",
            "normalized_search_text",
            "exact_search_eligible",
            "exact_search_status",
            "exact_search_candidate_count",
            "entity_type",
            "resolution_status",
            "retrieval_eligible",
            "anchor_eligible",
            "multi_hop_eligible",
            "source_node_kind",
            "source_node_id",
            "source_node_ids_json",
            "source_node_kinds_json",
            "source_member_count",
            "merge_scope",
            "context_anchor_id",
            "context_direction",
            "context_predicate",
            "context_directions_json",
            "context_predicates_json",
            "lifecycle_status",
            "identity_confidence",
            "source_support_count",
            "graph_release_id",
        ],
        "facts": [
            "fact_id",
            "subject_entity_id",
            "subject_node_kind",
            "subject_source_node_id",
            "subject_identity_node_id",
            "subject_endpoint_resolution_method",
            "predicate",
            "object_entity_id",
            "object_node_kind",
            "object_source_node_id",
            "object_identity_node_id",
            "object_endpoint_resolution_method",
            "assertion_count",
            "relation_status",
            "endpoint_status",
            "retrieval_eligible",
            "candidate_retrieval_eligible",
            "terminal_retrieval_eligible",
            "multi_hop_eligible",
            "evidence_ids_json",
            "source_datasets_json",
            "candidate_tiers_json",
            "source_predicates_json",
            "raw_relation_types_json",
            "relation_qualifiers_json",
            "endpoint_projection_status",
            "endpoint_projection_reference_fact_id",
            "review_model",
            "review_rationale",
            "review_reason_codes_json",
            "graph_release_id",
        ],
        "semantic_relations": [
            "semantic_relation_id",
            "subject_entity_id",
            "predicate",
            "object_entity_id",
            "directionality",
            "representative_fact_id",
            "fact_ids_json",
            "fact_count",
            "assertion_count",
            "relation_status",
            "relation_statuses_json",
            "endpoint_status",
            "retrieval_eligible",
            "candidate_retrieval_eligible",
            "terminal_retrieval_eligible",
            "multi_hop_eligible",
            "evidence_ids_json",
            "source_datasets_json",
            "source_predicates_json",
            "raw_relation_types_json",
            "relation_qualifiers_json",
            "review_models_json",
            "graph_release_id",
        ],
        "evidence": [
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
            "graph_release_id",
        ],
        "source_records": [
            "source_record_id",
            "source",
            "source_key",
            "source_release",
            "source_metadata_json",
            "identity_status",
            "identity_reason_code",
            "preferred_source_node_id",
            "identity_evidence_urls_json",
            "graph_release_id",
        ],
        "identity_conflicts": [
            "source_node_id",
            "source",
            "source_key",
            "decision",
            "reason_code",
            "preferred_source_node_id",
            "evidence_urls_json",
            "note",
            "display_name",
            "identity_status",
            "graph_release_id",
        ],
        "quarantined_facts": [
            "fact_id",
            "subject_node_id",
            "predicate",
            "object_node_id",
            "assertion_count",
            "trust_status",
            "reason_codes_json",
            "quarantined_source_node_ids_json",
            "evidence_ids_json",
            "evidence_records_json",
            "source_datasets_json",
            "graph_release_id",
        ],
        "entity_names": [
            "entity_name_id",
            "name",
            "normalized_name",
            "name_type",
            "normalization_policy_version",
            "search_text",
            "target_count",
            "target_resolution_status",
            "exact_search_eligible",
            "retrieval_eligible",
            "graph_release_id",
        ],
        "exam_terms": [
            "exam_term_id",
            "term",
            "normalized_term",
            "term_variants_json",
            "resolution_case_ids_json",
            "categories_json",
            "entity_type_proposals_json",
            "problem_count",
            "problem_ids_json",
            "source_link_status",
            "normalization_policy_version",
            "resolution_policy_version",
            "search_text",
            "resolution_status",
            "target_count",
            "target_resolution_status",
            "exact_search_eligible",
            "retrieval_eligible",
            "fact_retrieval_eligible",
            "terminal_fact_retrieval_eligible",
            "graph_release_id",
        ],
        "topics": ["topic_id", "name", "status", "version", "graph_release_id"],
        "eras": ["era_id", "name", "status", "version", "graph_release_id"],
        "fact_evidence_links": [
            "fact_id",
            "evidence_id",
            "graph_release_id",
        ],
        "evidence_source_links": [
            "evidence_id",
            "source_record_id",
            "graph_release_id",
        ],
        "provisional_source_links": [
            "entity_id",
            "source_record_id",
            "graph_release_id",
        ],
        "entity_name_links": [
            "entity_name_id",
            "canonical_id",
            "match_status",
            "method",
            "version",
            "graph_release_id",
        ],
        "exam_term_links": [
            "exam_term_id",
            "canonical_id",
            "match_status",
            "method",
            "version",
            "term_decision_id",
            "graph_release_id",
        ],
        "source_resolution_links": [
            "source_record_id",
            "canonical_id",
            "match_status",
            "method",
            "version",
            "term_decision_id",
            "graph_release_id",
        ],
        "entity_topic_links": [
            "canonical_id",
            "topic_id",
            "verification_status",
            "method",
            "evidence_json",
            "version",
            "graph_release_id",
        ],
        "entity_era_links": [
            "canonical_id",
            "era_id",
            "verification_status",
            "method",
            "evidence_json",
            "version",
            "graph_release_id",
        ],
        "entity_type_links": [
            "entity_id",
            "entity_type",
            "graph_release_id",
        ],
    }

    output_directory.mkdir(parents=True, exist_ok=True)
    for artifact_name in config.get("stale_runtime_artifacts", []):
        artifact_path = Path(str(artifact_name))
        if artifact_path.name != str(artifact_name):
            raise ValueError(
                f"Runtime artifact must be a file name: {artifact_name}"
            )
        stale_artifact_path = output_directory / artifact_path
        if stale_artifact_path.is_file():
            stale_artifact_path.unlink()

    output_paths: dict[str, str] = {}
    for table_name, rows in package.items():
        path = output_directory / f"{table_name}.csv"
        write_csv_rows(path, fieldnames[table_name], rows)
        output_paths[table_name] = path.name

    fact_status_counts = Counter(
        row["relation_status"]
        for row in package["facts"]
    )
    endpoint_status_counts = Counter(
        row["endpoint_status"]
        for row in package["facts"]
    )
    node_status_counts = Counter(
        row["resolution_status"]
        for row in package["entities"]
    )
    contextual_entities = [
        row
        for row in package["entities"]
        if row["merge_scope"] == config["contextual_merge"]["merge_scope"]
    ]
    manifest = {
        "status": "READY_FOR_NEO4J_LOAD",
        "stage": "FACT_GRAPH_RELEASE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": config["policy_version"],
        "graph_release_id": config["graph_release_id"],
        "neo4j_load": False,
        "statistics": {
            "entity_count": len(package["entities"]),
            "canonical_entity_count": node_status_counts["RESOLVED"],
            "provisional_entity_count": node_status_counts["UNRESOLVED"],
            "fact_count": len(package["facts"]),
            "direct_semantic_relation_count": len(
                package["semantic_relations"]
            ),
            "direct_relation_merge_count": (
                len(package["facts"]) - len(package["semantic_relations"])
            ),
            "verified_fact_count": fact_status_counts["VERIFIED"],
            "llm_reviewed_approved_fact_count": fact_status_counts[
                "REVIEWED_APPROVED"
            ],
            "resolved_endpoint_fact_count": endpoint_status_counts["RESOLVED"],
            "unresolved_endpoint_fact_count": endpoint_status_counts[
                "UNRESOLVED"
            ],
            "default_retrieval_fact_count": sum(
                row["retrieval_eligible"] == "true"
                for row in package["facts"]
            ),
            "default_retrieval_semantic_relation_count": sum(
                row["retrieval_eligible"] == "true"
                for row in package["semantic_relations"]
            ),
            "terminal_retrieval_fact_count": sum(
                row["terminal_retrieval_eligible"] == "true"
                for row in package["facts"]
            ),
            "terminal_retrieval_semantic_relation_count": sum(
                row["terminal_retrieval_eligible"] == "true"
                for row in package["semantic_relations"]
            ),
            "candidate_only_fact_count": sum(
                row["retrieval_eligible"] == "false"
                for row in package["facts"]
            ),
            "symmetric_semantic_relation_count": sum(
                row["directionality"] == "SYMMETRIC"
                for row in package["semantic_relations"]
            ),
            "symmetric_semantic_fact_collapse_count": sum(
                int(row["fact_count"]) - 1
                for row in package["semantic_relations"]
                if row["directionality"] == "SYMMETRIC"
            ),
            "evidence_count": len(package["evidence"]),
            "source_record_count": len(package["source_records"]),
            "identity_conflict_decision_count": len(
                package["identity_conflicts"]
            ),
            "quarantined_source_record_count": sum(
                row["decision"] == "QUARANTINE"
                for row in package["identity_conflicts"]
            ),
            "redirected_source_record_count": sum(
                row["decision"] == "REDIRECT"
                for row in package["identity_conflicts"]
            ),
            "quarantined_fact_count": len(package["quarantined_facts"]),
            "canonical_projection_collapsed_fact_count": sum(
                row["endpoint_projection_status"]
                == "CANONICAL_DUPLICATE_COLLAPSED"
                for row in package["facts"]
            ),
            "canonical_endpoint_resolved_fact_count": sum(
                bool(row["subject_endpoint_resolution_method"])
                or bool(row["object_endpoint_resolution_method"])
                for row in package["facts"]
            ),
            "canonical_endpoint_resolved_source_node_count": len(
                {
                    source_node_id
                    for row in package["facts"]
                    for source_node_id, method in (
                        (
                            row["subject_source_node_id"],
                            row[
                                "subject_endpoint_resolution_method"
                            ],
                        ),
                        (
                            row["object_source_node_id"],
                            row[
                                "object_endpoint_resolution_method"
                            ],
                        ),
                    )
                    if method
                }
            ),
            "canonical_endpoint_resolution_method_counts": dict(
                Counter(
                    method
                    for row in package["facts"]
                    for method in (
                        row["subject_endpoint_resolution_method"],
                        row["object_endpoint_resolution_method"],
                    )
                    if method
                )
            ),
            "entity_name_count": len(package["entity_names"]),
            "exact_search_entity_name_count": sum(
                row["exact_search_eligible"] == "true"
                for row in package["entity_names"]
            ),
            "exam_term_count": len(package["exam_terms"]),
            "generated_exam_term_link_count": sum(
                row["method"]
                == config["canonical_endpoint_resolution"][
                    "generated_match_method"
                ]
                for row in package["exam_term_links"]
            ),
            "unique_resolved_exam_term_count": sum(
                row["target_resolution_status"] == "UNIQUE"
                for row in package["exam_terms"]
            ),
            "ambiguous_exam_term_count": sum(
                row["target_resolution_status"] == "AMBIGUOUS"
                for row in package["exam_terms"]
            ),
            "default_fact_covered_exam_term_count": sum(
                row["fact_retrieval_eligible"] == "true"
                for row in package["exam_terms"]
            ),
            "terminal_fact_covered_exam_term_count": sum(
                row["terminal_fact_retrieval_eligible"] == "true"
                for row in package["exam_terms"]
            ),
            "exact_search_canonical_entity_count": sum(
                row["exact_search_eligible"] == "true"
                for row in package["entities"]
                if row["entity_kind"] == "CANONICAL"
            ),
            "ambiguous_suppressed_canonical_entity_count": sum(
                row["exact_search_status"] == "AMBIGUOUS_SUPPRESSED"
                for row in package["entities"]
            ),
            "topic_count": len(package["topics"]),
            "era_count": len(package["eras"]),
            "contextual_merged_entity_count": len(contextual_entities),
            "contextual_merged_source_node_count": sum(
                int(row["source_member_count"])
                for row in contextual_entities
            ),
            "contextual_entity_reduction_count": sum(
                int(row["source_member_count"]) - 1
                for row in contextual_entities
            ),
        },
        "output_paths": output_paths,
    }
    manifest_path = output_directory / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as output_file:
        json.dump(manifest, output_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = manifest_path.name
    return manifest

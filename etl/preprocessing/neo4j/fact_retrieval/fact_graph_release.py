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
    with path.open("w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def normalize_search_text(value: str) -> str:
    return "".join(
        character.casefold()
        for character in value
        if character.isalnum()
    )


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

    canonical_rows = read_csv_rows(input_paths["canonical_entities"])
    canonical_by_id = {
        row["canonical_id"]: row
        for row in canonical_rows
    }
    graph_node_by_id = {
        row["node_id"]: row
        for row in read_csv_rows(input_paths["fact_graph_nodes"])
    }
    candidate_by_id = {
        row["fact_graph_candidate_id"]: row
        for row in read_csv_rows(input_paths["all_fact_graph_candidates"])
    }
    fact_by_id = {
        row["fact_id"]: row
        for row in read_csv_rows(input_paths["fact_graph_facts"])
    }
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

    selected_fact_inputs: list[dict[str, Any]] = []
    for fact in fact_by_id.values():
        is_existing_verified = (
            fact["trust_status"] == config["existing_fact_status"]
        )
        decision = accepted_decision_by_fact_id.get(fact["fact_id"])
        if not is_existing_verified and decision is None:
            continue

        subject_node_id = fact["subject_node_id"]
        object_node_id = fact["object_node_id"]
        subject_graph_node = graph_node_by_id.get(subject_node_id)
        object_graph_node = graph_node_by_id.get(object_node_id)
        if subject_graph_node is None or object_graph_node is None:
            raise ValueError(
                f"Missing endpoint node for selected fact: {fact['fact_id']}"
            )
        subject_kind = subject_graph_node["node_kind"]
        object_kind = object_graph_node["node_kind"]
        predicate = fact["predicate"]
        if relation_pattern.fullmatch(predicate) is None:
            raise ValueError(
                f"Unsafe relationship type {predicate!r}: {fact['fact_id']}"
            )

        selected_fact_inputs.append(
            {
                "fact": fact,
                "decision": decision,
                "subject_kind": subject_kind,
                "object_kind": object_kind,
            }
        )

    contextual_groups: dict[
        tuple[str, str, str, str],
        dict[str, Any],
    ] = {}
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
                and canonical_by_id[fact["subject_node_id"]]["entity_type"]
                == anchor_entity_type
            ):
                anchor_id = fact["subject_node_id"]
                direction = "OUT"
                provisional_side = "object"
                provisional_node_id = fact["object_node_id"]
                provisional_kind = object_kind
            elif (
                object_kind == resolved_node_kind
                and subject_kind != resolved_node_kind
                and canonical_by_id[fact["object_node_id"]]["entity_type"]
                == anchor_entity_type
            ):
                anchor_id = fact["object_node_id"]
                direction = "IN"
                provisional_side = "subject"
                provisional_node_id = fact["subject_node_id"]
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
                direction,
                normalized_name,
                entity_type,
            )
            group = contextual_groups.setdefault(
                group_key,
                {
                    "anchor_id": anchor_id,
                    "direction": direction,
                    "normalized_name": normalized_name,
                    "entity_type": entity_type,
                    "display_names": set(),
                    "source_nodes": {},
                    "members": [],
                },
            )
            group["display_names"].add(display_name)
            group["source_nodes"][provisional_node_id] = provisional_kind
            group["members"].append((fact["fact_id"], provisional_side))

    minimum_source_count = int(
        contextual_merge["minimum_distinct_source_node_count"]
    )
    merge_scope = str(contextual_merge["merge_scope"])
    contextual_entity_id_by_endpoint: dict[tuple[str, str], str] = {}
    contextual_entity_metadata: dict[str, dict[str, Any]] = {}
    for group_key, group in contextual_groups.items():
        source_nodes = group["source_nodes"]
        if len(source_nodes) < minimum_source_count:
            continue
        contextual_entity_id = stable_identifier(
            "contextual-entity",
            {
                "scope": merge_scope,
                "anchor_id": group_key[0],
                "direction": group_key[1],
                "normalized_name": group_key[2],
                "entity_type": group_key[3],
            },
        )
        display_names = sorted(
            group["display_names"],
            key=lambda value: (len(value), value.casefold()),
        )
        contextual_entity_metadata[contextual_entity_id] = {
            **group,
            "display_name": display_names[0],
        }
        for member in group["members"]:
            contextual_entity_id_by_endpoint[member] = contextual_entity_id

    selected_facts: list[dict[str, Any]] = []
    selected_fact_ids: set[str] = set()
    for item in selected_fact_inputs:
        fact = item["fact"]
        decision = item["decision"]
        subject_kind = item["subject_kind"]
        object_kind = item["object_kind"]
        subject_node_id = fact["subject_node_id"]
        object_node_id = fact["object_node_id"]
        predicate = fact["predicate"]

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
                "subject_source_node_id": subject_node_id,
                "predicate": predicate,
                "object_entity_id": object_entity_id,
                "object_node_kind": object_kind,
                "object_source_node_id": object_node_id,
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
                "multi_hop_eligible": str(endpoints_resolved).lower(),
                "evidence_ids_json": fact["evidence_ids_json"],
                "source_datasets_json": fact["source_datasets_json"],
                "candidate_tiers_json": fact["candidate_tiers_json"],
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
    )
    if len(selected_facts) != expected_fact_count:
        raise ValueError(
            f"Selected fact count mismatch: "
            f"{len(selected_facts)} != {expected_fact_count}"
        )
    if len(selected_fact_ids) != len(selected_facts):
        raise ValueError("Selected fact IDs are not unique")

    semantic_relation_groups: dict[
        tuple[str, str, str],
        list[dict[str, Any]],
    ] = {}
    for fact in selected_facts:
        relation_key = (
            fact["subject_entity_id"],
            fact["predicate"],
            fact["object_entity_id"],
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
            "context_direction": metadata["direction"],
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
                fact["subject_source_node_id"],
            ),
            (
                fact["object_entity_id"],
                fact["object_node_kind"],
                fact["object_source_node_id"],
            ),
        )
        for entity_id, node_kind, fallback_node_id in endpoint_values:
            if node_kind == resolved_node_kind:
                if entity_id not in canonical_by_id:
                    raise ValueError(
                        f"Canonical endpoint is absent from registry: {entity_id}"
                    )
                continue
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
                "lifecycle_status": "PROVISIONAL",
                "identity_confidence": "",
                "source_support_count": "",
                "graph_release_id": graph_release_id,
            }

    evidence_by_id = {
        row["evidence_id"]: row
        for row in read_csv_rows(input_paths["evidence"])
    }
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
                    "graph_release_id": graph_release_id,
                }
            provisional_source_links.append(
                {
                    "entity_id": entity["entity_id"],
                    "source_record_id": source_record_id,
                    "graph_release_id": graph_release_id,
                }
            )

    accepted_match_status = str(config["accepted_match_status"])
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
    linked_entity_name_ids = {
        row["entity_name_id"]
        for row in entity_name_links
    }
    entity_names = [
        {
            **row,
            "search_text": row["name"],
            "graph_release_id": graph_release_id,
        }
        for row in entity_name_rows
        if row["entity_name_id"] in linked_entity_name_ids
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
    linked_exam_term_ids = {
        row["exam_term_id"]
        for row in exam_term_links
    }
    exam_terms = [
        {
            **row,
            "search_text": row["term"],
            "resolution_status": (
                "RESOLVED"
                if row["exam_term_id"] in linked_exam_term_ids
                else "UNRESOLVED"
            ),
            "retrieval_eligible": "false",
            "graph_release_id": graph_release_id,
        }
        for row in exam_term_rows
    ]

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
            "predicate",
            "object_entity_id",
            "object_node_kind",
            "object_source_node_id",
            "assertion_count",
            "relation_status",
            "endpoint_status",
            "retrieval_eligible",
            "candidate_retrieval_eligible",
            "multi_hop_eligible",
            "evidence_ids_json",
            "source_datasets_json",
            "candidate_tiers_json",
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
            "representative_fact_id",
            "fact_ids_json",
            "fact_count",
            "assertion_count",
            "relation_status",
            "relation_statuses_json",
            "endpoint_status",
            "retrieval_eligible",
            "candidate_retrieval_eligible",
            "multi_hop_eligible",
            "evidence_ids_json",
            "source_datasets_json",
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
            "graph_release_id",
        ],
        "entity_names": [
            "entity_name_id",
            "name",
            "normalized_name",
            "name_type",
            "normalization_policy_version",
            "search_text",
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
            "retrieval_eligible",
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
    output_paths: dict[str, str] = {}
    for table_name, rows in package.items():
        path = output_directory / f"{table_name}.csv"
        write_csv_rows(path, fieldnames[table_name], rows)
        output_paths[table_name] = str(path.resolve())

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
            "candidate_only_fact_count": sum(
                row["retrieval_eligible"] == "false"
                for row in package["facts"]
            ),
            "evidence_count": len(package["evidence"]),
            "source_record_count": len(package["source_records"]),
            "entity_name_count": len(package["entity_names"]),
            "exam_term_count": len(package["exam_terms"]),
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
    manifest["output_paths"]["manifest"] = str(manifest_path.resolve())
    return manifest

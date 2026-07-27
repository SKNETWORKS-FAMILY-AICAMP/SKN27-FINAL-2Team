from __future__ import annotations

import csv
import hashlib
import json
import random
import time
from collections import Counter, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        return list(csv.DictReader(input_file))


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as input_file:
        value = json.load(input_file)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def normalize_name(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def stable_hash(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as input_file:
        chunk = input_file.read(1024 * 1024)
        while chunk:
            hasher.update(chunk)
            chunk = input_file.read(1024 * 1024)
    return hasher.hexdigest()


def build_relation_review_tasks(
    output_root: Path,
    minimum_type_signature_support: int,
) -> list[dict[str, Any]]:
    final_identity_root = output_root / "final_identity"
    relation_gate_root = output_root / "exam_term_nlp_relation_gate"
    fact_graph_root = output_root / "fact_graph_load"

    canonical_types: dict[str, str] = {}
    name_to_types: defaultdict[str, set[str]] = defaultdict(set)
    for row in read_csv_rows(final_identity_root / "canonical_entity_registry.csv"):
        canonical_types[row["canonical_id"]] = row["entity_type"]
        name_to_types[normalize_name(row["display_name"])].add(row["entity_type"])

    entity_names = {
        row["entity_name_id"]: row["normalized_name"]
        or normalize_name(row["name"])
        for row in read_csv_rows(final_identity_root / "neo4j_entity_name_nodes.csv")
    }
    name_relationships = read_csv_rows(
        final_identity_root / "neo4j_name_to_entity_relationships.csv"
    )
    for row in name_relationships:
        if row["match_status"] != "ACCEPTED":
            continue
        canonical_type = canonical_types.get(row["canonical_id"])
        normalized_name = entity_names.get(row["entity_name_id"])
        if canonical_type and normalized_name:
            name_to_types[normalize_name(normalized_name)].add(canonical_type)

    accepted_signatures: Counter[tuple[str, str, str]] = Counter()
    safe_candidates: dict[str, dict[str, str]] = {}
    safe_candidate_rows = read_csv_rows(
        relation_gate_root / "safe_relation_candidates.csv"
    )
    for row in safe_candidate_rows:
        candidate_id = "nlp:" + row["safe_relation_candidate_id"]
        safe_candidates[candidate_id] = row
        signature = (
            row["relation_type"],
            row["start_entity_type"],
            row["end_entity_type"],
        )
        accepted_signatures[signature] += 1

    inferred_candidates: dict[str, dict[str, str]] = {}
    type_review_rows = read_csv_rows(
        relation_gate_root / "type_review_relation_candidates.csv"
    )
    for row in type_review_rows:
        inferred_types: list[str] = []
        inference_used = False
        inference_valid = True
        for side in ("start", "end"):
            entity_type = row[f"{side}_entity_type"]
            if entity_type == "Unknown":
                possible_types = name_to_types.get(
                    normalize_name(row[f"{side}_display_name"]),
                    set(),
                )
                if len(possible_types) == 1:
                    entity_type = next(iter(possible_types))
                    inference_used = True
                elif len(possible_types) != 1:
                    inference_valid = False
            inferred_types.append(entity_type)
        signature_support = 0
        if inference_valid and inference_used:
            signature = (
                row["relation_type"],
                inferred_types[0],
                inferred_types[1],
            )
            signature_support = accepted_signatures.get(signature, 0)
        if signature_support < minimum_type_signature_support:
            continue
        enriched = dict(row)
        enriched["resolved_start_entity_type"] = inferred_types[0]
        enriched["resolved_end_entity_type"] = inferred_types[1]
        enriched["type_inference_signature_support"] = str(signature_support)
        candidate_id = "nlp:" + row["safe_relation_candidate_id"]
        inferred_candidates[candidate_id] = enriched

    tasks: list[dict[str, Any]] = []
    fact_rows = read_csv_rows(fact_graph_root / "fact_graph_facts.csv")
    for fact in fact_rows:
        if fact["trust_status"] != "PROVISIONAL":
            continue
        candidate_ids = json.loads(fact["fact_graph_candidate_ids_json"])
        candidate_tiers = json.loads(fact["candidate_tiers_json"])
        if len(candidate_ids) != 1:
            continue
        candidate_id = candidate_ids[0]
        source_row: dict[str, str] | None = None
        review_origin = ""
        if candidate_tiers == ["NLP_STRICT"]:
            source_row = safe_candidates.get(candidate_id)
            review_origin = "STRICT_RELATION_REVIEW"
        elif candidate_tiers == ["NLP_ENDPOINT_TYPE_REVIEW"]:
            source_row = inferred_candidates.get(candidate_id)
            review_origin = "TYPE_INFERRED_RELATION_REVIEW"
        if source_row is None:
            continue

        start_type = source_row.get(
            "resolved_start_entity_type",
            source_row["start_entity_type"],
        )
        end_type = source_row.get(
            "resolved_end_entity_type",
            source_row["end_entity_type"],
        )
        task_identity = {
            "fact_id": fact["fact_id"],
            "candidate_id": candidate_id,
            "policy": "relation-review-task-v1",
        }
        task = {
            "relation_review_task_id": (
                "relation-review:" + stable_hash(task_identity)[:24]
            ),
            "fact_id": fact["fact_id"],
            "candidate_id": candidate_id,
            "review_origin": review_origin,
            "relation": {
                "type": source_row["relation_type"],
                "family": source_row["relation_family"],
                "display": source_row["relation_display"],
                "surface_predicate": source_row["representative_predicate"],
            },
            "start": {
                "node_id": source_row["start_node_id"],
                "node_kind": source_row["start_node_kind"],
                "display_name": source_row["start_display_name"],
                "original_entity_type": source_row["start_entity_type"],
                "proposed_entity_type": start_type,
            },
            "end": {
                "node_id": source_row["end_node_id"],
                "node_kind": source_row["end_node_kind"],
                "display_name": source_row["end_display_name"],
                "original_entity_type": source_row["end_entity_type"],
                "proposed_entity_type": end_type,
            },
            "evidence": {
                "source_dataset": source_row["representative_source_dataset"],
                "source_document_id": source_row[
                    "representative_source_document_id"
                ],
                "source_title": source_row["representative_source_title"],
                "source_url": source_row["representative_source_url"],
                "atomic_clause": source_row["representative_atomic_clause"],
                "sentence": source_row["representative_evidence_sentence"],
                "evidence_count": int(source_row["evidence_count"]),
                "distinct_support_count": int(
                    source_row["distinct_support_count"]
                ),
                "source_count": int(source_row["source_count"]),
            },
            "exam_term_ids": json.loads(
                source_row["anchor_exam_term_ids_json"]
            ),
            "gate": {
                "status": source_row["gate_status"],
                "candidate_score": int(source_row["maximum_candidate_score"]),
                "type_inference_signature_support": int(
                    source_row.get("type_inference_signature_support") or 0
                ),
            },
        }
        tasks.append(task)

    def task_priority(task: dict[str, Any]) -> tuple[Any, ...]:
        evidence = task["evidence"]
        gate = task["gate"]
        corroborated = gate["status"] == "GATE_PASSED_CORROBORATED"
        strict = task["review_origin"] == "STRICT_RELATION_REVIEW"
        both_registered = (
            task["start"]["node_kind"] != "OPEN_ENTITY_CANDIDATE"
            and task["end"]["node_kind"] != "OPEN_ENTITY_CANDIDATE"
        )
        return (
            not corroborated,
            -evidence["source_count"],
            -evidence["distinct_support_count"],
            -evidence["evidence_count"],
            not both_registered,
            not strict,
            -len(task["exam_term_ids"]),
            -gate["candidate_score"],
            task["relation_review_task_id"],
        )

    tasks.sort(key=task_priority)
    return tasks


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output_file:
        for record in records:
            output_file.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_compatible_checkpoint(
    checkpoint_path: Path,
    tasks: list[dict[str, Any]],
    model: str,
    prompt_sha256: str,
    schema_sha256: str,
    policy_version: str,
    payload_builder: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not checkpoint_path.is_file():
        return {}
    tasks_by_id = {
        task["relation_review_task_id"]: task
        for task in tasks
    }
    compatible: dict[str, dict[str, Any]] = {}
    with checkpoint_path.open("r", encoding="utf-8") as input_file:
        for line in input_file:
            stripped = line.strip()
            if not stripped:
                continue
            record = json.loads(stripped)
            task_id = str(record.get("relation_review_task_id") or "")
            task = tasks_by_id.get(task_id)
            if task is None:
                continue
            expected_input_hash = stable_hash(payload_builder(task))
            if record.get("model") != model:
                continue
            if record.get("prompt_sha256") != prompt_sha256:
                continue
            if record.get("schema_sha256") != schema_sha256:
                continue
            if record.get("policy_version") != policy_version:
                continue
            if record.get("input_hash") != expected_input_hash:
                continue
            if not isinstance(record.get("decision"), dict):
                continue
            compatible[task_id] = record
    return compatible


def request_structured_decision(
    client: Any,
    task_id: str,
    payload: dict[str, Any],
    prompt: str,
    schema: dict[str, Any],
    model_config: dict[str, Any],
    execution_config: dict[str, Any],
) -> dict[str, Any]:
    request_arguments: dict[str, Any] = {
        "model": model_config["model"],
        "instructions": prompt,
        "input": json.dumps(payload, ensure_ascii=False),
        "max_output_tokens": int(model_config["maximum_output_tokens"]),
        "reasoning": {"effort": model_config["reasoning_effort"]},
        "store": bool(execution_config["store_response"]),
        "text": {
            "format": {
                "type": "json_schema",
                "name": model_config["schema_name"],
                "schema": schema,
                "strict": True,
            }
        },
    }
    service_tier = str(execution_config.get("service_tier") or "")
    if service_tier:
        request_arguments["service_tier"] = service_tier
    response = client.responses.create(**request_arguments)
    raw_output = str(response.output_text or "").strip()
    if not raw_output:
        raise ValueError(f"Empty model output: {task_id}")
    decision = json.loads(raw_output)
    if not isinstance(decision, dict):
        raise ValueError(f"Model output is not an object: {task_id}")
    usage: dict[str, Any] = {}
    if getattr(response, "usage", None) is not None:
        usage = response.usage.model_dump()
    return {
        "decision": decision,
        "response_id": str(getattr(response, "id", "")),
        "usage": usage,
    }


def execute_review_phase(
    client: Any,
    tasks: list[dict[str, Any]],
    checkpoint_path: Path,
    failure_path: Path,
    prompt_path: Path,
    schema_path: Path,
    model_config: dict[str, Any],
    execution_config: dict[str, Any],
    policy_version: str,
    payload_builder: Callable[[dict[str, Any]], dict[str, Any]],
    maximum_workers: int,
) -> dict[str, Any]:
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    schema = read_json(schema_path)
    prompt_hash = file_sha256(prompt_path)
    schema_hash = file_sha256(schema_path)
    model = str(model_config["model"])
    checkpoint_records = load_compatible_checkpoint(
        checkpoint_path=checkpoint_path,
        tasks=tasks,
        model=model,
        prompt_sha256=prompt_hash,
        schema_sha256=schema_hash,
        policy_version=policy_version,
        payload_builder=payload_builder,
    )
    pending_tasks = [
        task
        for task in tasks
        if task["relation_review_task_id"] not in checkpoint_records
    ]
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    failure_path.parent.mkdir(parents=True, exist_ok=True)
    maximum_retries = int(execution_config["maximum_retries"])
    retry_base_seconds = float(execution_config["retry_base_seconds"])
    progress_interval = int(execution_config["progress_interval"])

    def execute_task(task: dict[str, Any]) -> dict[str, Any]:
        task_id = task["relation_review_task_id"]
        payload = payload_builder(task)
        last_error = ""
        for attempt in range(maximum_retries + 1):
            try:
                result = request_structured_decision(
                    client=client,
                    task_id=task_id,
                    payload=payload,
                    prompt=prompt,
                    schema=schema,
                    model_config=model_config,
                    execution_config=execution_config,
                )
                return {
                    "status": "SUCCEEDED",
                    "relation_review_task_id": task_id,
                    "model": model,
                    "reasoning_effort": model_config["reasoning_effort"],
                    "prompt_sha256": prompt_hash,
                    "schema_sha256": schema_hash,
                    "policy_version": policy_version,
                    "input_hash": stable_hash(payload),
                    "response_id": result["response_id"],
                    "usage": result["usage"],
                    "decision": result["decision"],
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as error:
                last_error = str(error)
                if attempt < maximum_retries:
                    delay = retry_base_seconds * (2**attempt)
                    delay += random.random() * retry_base_seconds
                    time.sleep(delay)
        return {
            "status": "FAILED",
            "relation_review_task_id": task_id,
            "model": model,
            "attempt_count": maximum_retries + 1,
            "error": last_error,
        }

    failures: list[dict[str, Any]] = []
    completed_in_run = 0
    if pending_tasks:
        with checkpoint_path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as checkpoint_file:
            with ThreadPoolExecutor(max_workers=maximum_workers) as executor:
                futures: dict[Future[dict[str, Any]], dict[str, Any]] = {
                    executor.submit(execute_task, task): task
                    for task in pending_tasks
                }
                for future in as_completed(futures):
                    result = future.result()
                    task_id = result["relation_review_task_id"]
                    if result["status"] == "SUCCEEDED":
                        checkpoint_file.write(
                            json.dumps(result, ensure_ascii=False) + "\n"
                        )
                        checkpoint_file.flush()
                        checkpoint_records[task_id] = result
                        completed_in_run += 1
                    elif result["status"] == "FAILED":
                        failures.append(result)
                    processed = completed_in_run + len(failures)
                    if (
                        processed % progress_interval == 0
                        or processed == len(pending_tasks)
                    ):
                        print(
                            f"{model}: {processed}/{len(pending_tasks)} "
                            f"(success={completed_in_run}, "
                            f"failed={len(failures)}, "
                            f"reused={len(checkpoint_records) - completed_in_run})",
                            flush=True,
                        )

    failure_columns = [
        "relation_review_task_id",
        "model",
        "attempt_count",
        "error",
    ]
    with failure_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as failure_file:
        writer = csv.DictWriter(
            failure_file,
            fieldnames=failure_columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(failures)

    ordered_records = [
        checkpoint_records[task["relation_review_task_id"]]
        for task in tasks
        if task["relation_review_task_id"] in checkpoint_records
    ]
    return {
        "records": ordered_records,
        "selected_task_count": len(tasks),
        "reused_checkpoint_count": len(ordered_records) - completed_in_run,
        "attempted_count": len(pending_tasks),
        "succeeded_count": completed_in_run,
        "failed_count": len(failures),
    }


def compile_final_decisions(
    tasks: list[dict[str, Any]],
    evaluation_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evaluation_by_id = {
        record["relation_review_task_id"]: record
        for record in evaluation_records
    }
    final_records: list[dict[str, Any]] = []
    for task in tasks:
        task_id = task["relation_review_task_id"]
        evaluation_record = evaluation_by_id.get(task_id)
        if evaluation_record is None:
            continue
        evaluation = evaluation_record["decision"]

        evaluator_approved = (
            evaluation["verdict"] == "VERIFIED"
            and evaluation["subject_explicit"]
            and evaluation["object_explicit"]
            and evaluation["predicate_supported"]
            and not evaluation["causative_or_indirect"]
            and not evaluation["negated_hypothetical_or_quoted"]
            and evaluation["start_type_supported"]
            and evaluation["end_type_supported"]
            and evaluation["start_graph_entity"]
            and evaluation["end_graph_entity"]
            and evaluation["safe_for_one_hop_retrieval"]
        )
        final_status = "NEEDS_MANUAL_REVIEW"
        if evaluator_approved:
            final_status = "REVIEWED_APPROVED_ONE_HOP"
            canonical_endpoints = (
                task["start"]["node_kind"] == "CANONICAL"
                and task["end"]["node_kind"] == "CANONICAL"
            )
            if canonical_endpoints and evaluation[
                "safe_for_multi_hop_retrieval"
            ]:
                final_status = "REVIEWED_APPROVED_MULTI_HOP"
        elif evaluation["verdict"] == "REJECTED":
            final_status = "REVIEWED_REJECTED"

        final_records.append(
            {
                "relation_review_task_id": task_id,
                "fact_id": task["fact_id"],
                "candidate_id": task["candidate_id"],
                "review_origin": task["review_origin"],
                "relation_display": task["relation"]["display"],
                "relation_type": task["relation"]["type"],
                "start_node_id": task["start"]["node_id"],
                "start_display_name": task["start"]["display_name"],
                "start_entity_type": task["start"]["proposed_entity_type"],
                "end_node_id": task["end"]["node_id"],
                "end_display_name": task["end"]["display_name"],
                "end_entity_type": task["end"]["proposed_entity_type"],
                "source_dataset": task["evidence"]["source_dataset"],
                "source_document_id": task["evidence"]["source_document_id"],
                "source_title": task["evidence"]["source_title"],
                "atomic_clause": task["evidence"]["atomic_clause"],
                "evidence_sentence": task["evidence"]["sentence"],
                "evaluation_model": evaluation_record["model"],
                "evaluation_verdict": evaluation["verdict"],
                "evaluation_reason_codes": evaluation["reason_codes"],
                "evaluation_rationale": evaluation["rationale"],
                "safe_for_one_hop_retrieval": evaluation[
                    "safe_for_one_hop_retrieval"
                ],
                "safe_for_multi_hop_retrieval": evaluation[
                    "safe_for_multi_hop_retrieval"
                ],
                "final_status": final_status,
            }
        )
    return final_records


def summarize_usage(
    records: list[dict[str, Any]],
    pricing: dict[str, Any],
) -> dict[str, Any]:
    by_model: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        model = record["model"]
        usage = record.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        cached_tokens = int(input_details.get("cached_tokens") or 0)
        reasoning_tokens = int(output_details.get("reasoning_tokens") or 0)
        by_model[model]["request_count"] += 1
        by_model[model]["input_tokens"] += input_tokens
        by_model[model]["cached_input_tokens"] += cached_tokens
        by_model[model]["output_tokens"] += output_tokens
        by_model[model]["reasoning_tokens"] += reasoning_tokens

    model_summaries: dict[str, Any] = {}
    total_cost = 0.0
    for model, counts in sorted(by_model.items()):
        model_pricing = pricing[model]
        uncached_tokens = (
            counts["input_tokens"] - counts["cached_input_tokens"]
        )
        input_cost = (
            uncached_tokens
            * float(model_pricing["input"])
            / 1_000_000
        )
        cached_cost = (
            counts["cached_input_tokens"]
            * float(model_pricing["cached_input"])
            / 1_000_000
        )
        output_cost = (
            counts["output_tokens"]
            * float(model_pricing["output"])
            / 1_000_000
        )
        model_cost = input_cost + cached_cost + output_cost
        total_cost += model_cost
        model_summaries[model] = {
            **dict(counts),
            "estimated_cost_usd": round(model_cost, 6),
        }
    return {
        "models": model_summaries,
        "estimated_total_cost_usd": round(total_cost, 6),
    }


def write_final_outputs(
    output_dir: Path,
    tasks: list[dict[str, Any]],
    evaluation_result: dict[str, Any],
    final_records: list[dict[str, Any]],
    policy: dict[str, Any],
    config_path: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    final_jsonl_path = output_dir / "relation_review_final_decisions.jsonl"
    final_csv_path = output_dir / "relation_review_final_decisions.csv"
    manifest_path = output_dir / "relation_review_manifest.json"
    write_jsonl(final_jsonl_path, final_records)

    csv_columns = [
        "relation_review_task_id",
        "fact_id",
        "candidate_id",
        "review_origin",
        "relation_display",
        "relation_type",
        "start_node_id",
        "start_display_name",
        "start_entity_type",
        "end_node_id",
        "end_display_name",
        "end_entity_type",
        "source_dataset",
        "source_document_id",
        "source_title",
        "atomic_clause",
        "evidence_sentence",
        "evaluation_model",
        "evaluation_verdict",
        "evaluation_reason_codes",
        "evaluation_rationale",
        "safe_for_one_hop_retrieval",
        "safe_for_multi_hop_retrieval",
        "final_status",
    ]
    with final_csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as output_file:
        writer = csv.DictWriter(output_file, fieldnames=csv_columns)
        writer.writeheader()
        for record in final_records:
            csv_record = dict(record)
            csv_record["evaluation_reason_codes"] = json.dumps(
                record["evaluation_reason_codes"],
                ensure_ascii=False,
            )
            writer.writerow(csv_record)

    final_status_counts = Counter(
        record["final_status"] for record in final_records
    )
    usage_summary = summarize_usage(
        evaluation_result["records"],
        policy["pricing_usd_per_million_tokens"],
    )
    manifest = {
        "status": (
            "COMPLETED"
            if len(final_records) == len(tasks)
            else "PARTIAL"
        ),
        "stage": "RELATION_LLM_REVIEW",
        "policy_version": policy["policy_version"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "neo4j_load": False,
        "task_count": len(tasks),
        "evaluation": {
            key: value
            for key, value in evaluation_result.items()
            if key != "records"
        },
        "final_decision_count": len(final_records),
        "final_status_counts": dict(sorted(final_status_counts.items())),
        "usage": usage_summary,
        "config_path": str(config_path.resolve()),
        "config_sha256": file_sha256(config_path),
        "output_paths": {
            "final_jsonl": str(final_jsonl_path.resolve()),
            "final_csv": str(final_csv_path.resolve()),
            "manifest": str(manifest_path.resolve()),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest["output_paths"]

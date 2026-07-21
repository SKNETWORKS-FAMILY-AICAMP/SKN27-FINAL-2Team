from argparse import ArgumentParser
from itertools import combinations
from json import dumps, loads
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "terms"))

from common import load_pipeline_policy
from entity_resolution.identifiers import create_stable_id


def load_resolution_package(
    input_dir: str,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """정책에 등록된 Entity Resolution staging CSV를 읽는다."""
    input_directory = Path(input_dir)
    output_files = policy["entity_resolution"]["output_files"]
    required_tables = [
        "resolution_cases",
        "source_record_candidates",
        "source_candidate_features",
        "source_candidate_pair_signals",
        "canonical_alternative_clusters",
        "canonical_cluster_members",
        "problem_contexts",
        "problem_resolution_assignments",
    ]
    tables: dict[str, pd.DataFrame] = {}
    for table_name in required_tables:
        input_path = input_directory / output_files[table_name]
        if not input_path.is_file():
            raise FileNotFoundError(f"ER staging CSV를 찾을 수 없습니다: {input_path}")
        tables[table_name] = pd.read_csv(input_path, dtype=str).fillna("")
    return tables


def select_source_context(
    metadata: dict,
    source: str,
    semantic_policy: dict,
) -> dict:
    """원천별 정책에 지정된 필드만 LLM 검토 문맥으로 구성한다."""
    selected: dict[str, object] = {}
    maximum_characters = int(
        semantic_policy["maximum_source_context_characters"]
    )
    source_fields = semantic_policy["source_context_fields"].get(source, [])
    for field_name in source_fields:
        raw_value = metadata.get(field_name)
        if raw_value is None or raw_value == "" or raw_value == []:
            continue
        if isinstance(raw_value, list):
            selected[field_name] = raw_value
            continue
        value = str(raw_value)
        if len(value) > maximum_characters:
            value = value[:maximum_characters]
        selected[field_name] = value
    return selected


def build_candidate_task_item(
    candidate: dict,
    feature: dict,
    semantic_policy: dict,
) -> dict:
    """SourceRecord 후보와 표준 feature를 하나의 검토 항목으로 합친다."""
    metadata = loads(candidate["source_metadata_json"])
    return {
        "source_candidate_id": candidate["source_candidate_id"],
        "source_record_id": candidate["source_record_id"],
        "source": candidate["source"],
        "candidate_rank": int(candidate["candidate_rank"]),
        "matched_name": candidate["matched_name"],
        "matched_field": candidate["matched_field"],
        "retrieval_method": candidate["retrieval_method"],
        "retrieval_score": float(candidate["retrieval_score"]),
        "category_compatibility": candidate["category_compatibility"],
        "normalized_names": loads(feature["normalized_names_json"]),
        "hanja": loads(feature["hanja_json"]),
        "era_values": loads(feature["era_values_json"]),
        "birth_year": feature["birth_year"],
        "death_year": feature["death_year"],
        "bonkwan": loads(feature["bonkwan_json"]),
        "source_entity_type_proposal": feature[
            "source_entity_type_proposal"
        ],
        "code_proposed_role": feature["proposed_role"],
        "code_canonical_alternative_id": feature[
            "proposed_canonical_alternative_id"
        ],
        "source_context": select_source_context(
            metadata,
            candidate["source"],
            semantic_policy,
        ),
    }


def build_term_review_tasks(
    tables: dict[str, pd.DataFrame],
    policy: dict,
) -> list[dict]:
    """AMBIGUOUS case를 LLM term-level 의미 판정용 JSON 객체로 만든다."""
    resolution_policy = policy["entity_resolution"]
    semantic_policy = resolution_policy["semantic_review"]
    identifier_policy = resolution_policy["identifier_policy"]
    review_statuses = set(semantic_policy["term_task_link_statuses"])
    candidates = tables["source_record_candidates"]
    features = tables["source_candidate_features"]
    pairs = tables["source_candidate_pair_signals"]
    clusters = tables["canonical_alternative_clusters"]
    members = tables["canonical_cluster_members"]
    contexts = tables["problem_contexts"]
    context_by_problem = {
        str(row.problem_id): str(row.full_text)
        for row in contexts.itertuples()
    }
    feature_by_candidate = {
        str(row["source_candidate_id"]): row
        for row in features.to_dict("records")
    }
    candidate_rows_by_case: dict[str, list[dict]] = {}
    for row in candidates.to_dict("records"):
        candidate_rows_by_case.setdefault(row["resolution_case_id"], []).append(
            row
        )
    pair_rows_by_case: dict[str, list[dict]] = {}
    for row in pairs.to_dict("records"):
        pair_rows_by_case.setdefault(row["resolution_case_id"], []).append(row)
    cluster_rows_by_case: dict[str, list[dict]] = {}
    for row in clusters.to_dict("records"):
        cluster_rows_by_case.setdefault(row["resolution_case_id"], []).append(
            row
        )
    member_ids_by_cluster: dict[str, list[str]] = {}
    for row in members.itertuples():
        member_ids_by_cluster.setdefault(
            row.canonical_alternative_id,
            [],
        ).append(row.source_candidate_id)

    tasks: list[dict] = []
    for case in tables["resolution_cases"].to_dict("records"):
        if case["link_status"] not in review_statuses:
            continue
        case_id = case["resolution_case_id"]
        case_candidates = sorted(
            candidate_rows_by_case.get(case_id, []),
            key=lambda row: int(row["candidate_rank"]),
        )
        if not case_candidates:
            continue
        candidate_items = [
            build_candidate_task_item(
                candidate,
                feature_by_candidate[candidate["source_candidate_id"]],
                semantic_policy,
            )
            for candidate in case_candidates
        ]
        code_alternatives = []
        for cluster in cluster_rows_by_case.get(case_id, []):
            code_alternatives.append(
                {
                    "canonical_alternative_id": cluster[
                        "canonical_alternative_id"
                    ],
                    "confidence_tier": cluster["confidence_tier"],
                    "merge_signals": loads(cluster["merge_signals_json"]),
                    "source_candidate_ids": sorted(
                        member_ids_by_cluster.get(
                            cluster["canonical_alternative_id"],
                            [],
                        )
                    ),
                }
            )
        relevant_pairs = []
        for pair in pair_rows_by_case.get(case_id, []):
            conflict_signals = loads(pair["conflict_signals_json"])
            merge_eligible = str(pair["merge_eligible"]).lower() == "true"
            if not merge_eligible and not conflict_signals:
                continue
            relevant_pairs.append(
                {
                    "left_source_candidate_id": pair[
                        "left_source_candidate_id"
                    ],
                    "right_source_candidate_id": pair[
                        "right_source_candidate_id"
                    ],
                    "signals": loads(pair["signal_dimensions_json"]),
                    "conflicts": conflict_signals,
                    "merge_eligible": merge_eligible,
                }
            )
        problem_ids = loads(case["problem_ids_json"])
        maximum_contexts = int(
            semantic_policy["maximum_problem_contexts_per_term_task"]
        )
        problem_contexts = [
            {
                "problem_id": problem_id,
                "full_text": context_by_problem.get(problem_id, ""),
            }
            for problem_id in problem_ids[:maximum_contexts]
        ]
        task_id = create_stable_id(
            identifier_policy["term_review_task_prefix"],
            [case_id, semantic_policy["prompt_version"]],
            identifier_policy,
        )
        tasks.append(
            {
                "term_review_task_id": task_id,
                "resolution_case_id": case_id,
                "canonical_term": case["canonical_term"],
                "term_variants": loads(case["term_variants_json"]),
                "category": case["category"],
                "entity_type_proposal": case["entity_type_proposal"],
                "problem_count": int(case["problem_count"]),
                "problem_context_samples": problem_contexts,
                "source_candidates": candidate_items,
                "code_canonical_alternatives": code_alternatives,
                "relevant_pair_signals": relevant_pairs,
                "required_decision_status": semantic_policy[
                    "decision_status_input"
                ],
                "review_model": semantic_policy["term_model"]["model"],
                "prompt_version": semantic_policy["prompt_version"],
                "resolution_policy_version": policy["policy_version"],
            }
        )
    return tasks


def write_jsonl(records: list[dict], output_path: str) -> str:
    """중첩된 검토 task 또는 결정을 UTF-8 JSONL로 저장한다."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(dumps(record, ensure_ascii=False) + "\n")
    return str(destination)


def load_jsonl(input_path: str) -> list[dict]:
    """빈 줄을 제외하고 JSONL 객체 목록을 읽는다."""
    records: list[dict] = []
    with open(input_path, "r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            record = loads(stripped)
            if not isinstance(record, dict):
                raise ValueError(f"JSONL {line_number}행은 객체여야 합니다.")
            records.append(record)
    return records


def collect_classified_sources(
    decision: dict,
) -> tuple[dict[str, tuple[str, str, str]], list[str]]:
    """결정 객체를 candidate ID별 역할·대안·사유로 평탄화한다."""
    classified: dict[str, tuple[str, str, str]] = {}
    duplicate_ids: list[str] = []
    for alternative in decision.get("proposed_alternatives", []):
        member_ids = alternative.get(
            "identity_member_source_candidate_ids",
            [],
        )
        for candidate_id in member_ids:
            if candidate_id in classified:
                duplicate_ids.append(candidate_id)
                continue
            classified[candidate_id] = (
                "IDENTITY_MEMBER",
                "",
                str(alternative.get("reason") or ""),
            )
    role_fields = [
        ("evidence_only_sources", "EVIDENCE_ONLY"),
        ("rejected_sources", "REJECTED"),
        ("ambiguous_sources", "AMBIGUOUS"),
    ]
    for field_name, role in role_fields:
        for item in decision.get(field_name, []):
            candidate_id = str(item.get("source_candidate_id") or "")
            if candidate_id in classified:
                duplicate_ids.append(candidate_id)
                continue
            classified[candidate_id] = (
                role,
                "",
                str(item.get("reason") or ""),
            )
    return classified, duplicate_ids


def validate_decision_shape(decision: dict) -> list[str]:
    """외부 라이브러리 없이 핵심 JSON Schema 구조를 선검사한다."""
    messages: list[str] = []
    required_strings = [
        "term_review_task_id",
        "resolution_case_id",
        "decision_status",
        "review_model",
        "prompt_version",
        "decision_reason",
    ]
    required_arrays = [
        "proposed_alternatives",
        "evidence_only_sources",
        "rejected_sources",
        "ambiguous_sources",
    ]
    for field_name in required_strings:
        if not isinstance(decision.get(field_name), str) or not decision.get(
            field_name
        ):
            messages.append(f"{field_name}: 비어 있지 않은 문자열이 필요합니다.")
    for field_name in required_arrays:
        if not isinstance(decision.get(field_name), list):
            messages.append(f"{field_name}: 배열이 필요합니다.")
    if messages:
        return messages
    for alternative in decision["proposed_alternatives"]:
        if not isinstance(alternative, dict):
            messages.append("proposed_alternatives 항목은 객체여야 합니다.")
            continue
        member_ids = alternative.get("identity_member_source_candidate_ids")
        if not isinstance(member_ids, list) or not member_ids:
            messages.append("canonical 대안에는 비어 있지 않은 후보 ID 배열이 필요합니다.")
        if not isinstance(alternative.get("display_name"), str) or not alternative.get(
            "display_name"
        ):
            messages.append("canonical 대안 display_name이 필요합니다.")
        if not isinstance(alternative.get("entity_type"), str) or not alternative.get(
            "entity_type"
        ):
            messages.append("canonical 대안 entity_type이 필요합니다.")
        if not isinstance(alternative.get("reason"), str) or not alternative.get(
            "reason"
        ):
            messages.append("canonical 대안 reason이 필요합니다.")
    for field_name in [
        "evidence_only_sources",
        "rejected_sources",
        "ambiguous_sources",
    ]:
        for item in decision[field_name]:
            if not isinstance(item, dict):
                messages.append(f"{field_name} 항목은 객체여야 합니다.")
                continue
            if not isinstance(item.get("source_candidate_id"), str) or not item.get(
                "source_candidate_id"
            ):
                messages.append(f"{field_name}.source_candidate_id가 필요합니다.")
            if not isinstance(item.get("reason"), str) or not item.get("reason"):
                messages.append(f"{field_name}.reason이 필요합니다.")
    return messages


def add_validation_error(
    errors: list[dict],
    decision_id: str,
    case_id: str,
    severity: str,
    error_code: str,
    message: str,
) -> None:
    """검증 오류를 감사 가능한 표준 행으로 추가한다."""
    errors.append(
        {
            "term_decision_id": decision_id,
            "resolution_case_id": case_id,
            "severity": severity,
            "error_code": error_code,
            "message": message,
        }
    )


def validate_term_decisions(
    decisions: list[dict],
    tasks: list[dict],
    tables: dict[str, pd.DataFrame],
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """LLM의 PROPOSED term 결정을 검증하고 VERIFIED 결과만 평탄화한다."""
    resolution_policy = policy["entity_resolution"]
    semantic_policy = resolution_policy["semantic_review"]
    identifier_policy = resolution_policy["identifier_policy"]
    task_by_id = {task["term_review_task_id"]: task for task in tasks}
    candidate_rows = {
        str(row["source_candidate_id"]): row
        for row in tables["source_record_candidates"].to_dict("records")
    }
    pair_by_ids = {
        frozenset(
            [row["left_source_candidate_id"], row["right_source_candidate_id"]]
        ): row
        for row in tables["source_candidate_pair_signals"].to_dict("records")
    }
    decision_rows: list[dict] = []
    alternative_rows: list[dict] = []
    role_rows: list[dict] = []
    error_rows: list[dict] = []
    observed_task_ids: set[str] = set()

    for decision_sequence, decision in enumerate(decisions, start=1):
        task_id = str(decision.get("term_review_task_id") or "")
        case_id = str(decision.get("resolution_case_id") or "")
        decision_id = create_stable_id(
            identifier_policy["term_decision_prefix"],
            [
                task_id,
                semantic_policy["prompt_version"],
                str(decision_sequence),
            ],
            identifier_policy,
        )
        invalid = False
        manual_review = False
        shape_errors = validate_decision_shape(decision)
        for shape_error in shape_errors:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "DECISION_SCHEMA_ERROR",
                shape_error,
            )
        if shape_errors:
            invalid = True
        task = task_by_id.get(task_id)
        if task is None:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "UNKNOWN_TERM_REVIEW_TASK",
                "등록되지 않은 term review task입니다.",
            )
            invalid = True
        elif task_id in observed_task_ids:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "DUPLICATE_TERM_DECISION",
                "동일 task에 대한 결정이 중복되었습니다.",
            )
            invalid = True
        elif task_id not in observed_task_ids:
            observed_task_ids.add(task_id)

        expected_candidate_ids: set[str] = set()
        if task is not None:
            expected_candidate_ids = {
                item["source_candidate_id"]
                for item in task["source_candidates"]
            }
            if case_id != task["resolution_case_id"]:
                add_validation_error(
                    error_rows,
                    decision_id,
                    case_id,
                    "INVALID",
                    "CASE_ID_MISMATCH",
                    "task와 결정의 resolution_case_id가 다릅니다.",
                )
                invalid = True
        if decision.get("decision_status") != semantic_policy[
            "decision_status_input"
        ]:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "INVALID_DECISION_STATUS",
                "LLM 입력 결정 상태는 PROPOSED여야 합니다.",
            )
            invalid = True
        if decision.get("prompt_version") != semantic_policy["prompt_version"]:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "PROMPT_VERSION_MISMATCH",
                "task와 결정의 prompt version이 다릅니다.",
            )
            invalid = True
        if decision.get("review_model") != semantic_policy["term_model"][
            "model"
        ]:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "REVIEW_MODEL_MISMATCH",
                "정책에 지정된 term review model이 아닙니다.",
            )
            invalid = True

        classified: dict[str, tuple[str, str, str]] = {}
        duplicate_ids: list[str] = []
        if not shape_errors:
            classified, duplicate_ids = collect_classified_sources(decision)
        if duplicate_ids:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "DUPLICATE_CANDIDATE_CLASSIFICATION",
                dumps(sorted(set(duplicate_ids)), ensure_ascii=False),
            )
            invalid = True
        classified_ids = set(classified)
        unknown_ids = classified_ids.difference(expected_candidate_ids)
        missing_ids = expected_candidate_ids.difference(classified_ids)
        if unknown_ids:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "UNKNOWN_SOURCE_CANDIDATE",
                dumps(sorted(unknown_ids), ensure_ascii=False),
            )
            invalid = True
        if missing_ids:
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "INVALID",
                "MISSING_CANDIDATE_CLASSIFICATION",
                dumps(sorted(missing_ids), ensure_ascii=False),
            )
            invalid = True

        alternative_specs: list[tuple[dict, str]] = []
        proposed_alternatives = decision.get("proposed_alternatives", [])
        if not isinstance(proposed_alternatives, list):
            proposed_alternatives = []
        if shape_errors:
            proposed_alternatives = []
        for alternative in proposed_alternatives:
            if not isinstance(alternative, dict):
                continue
            member_ids = sorted(
                alternative.get("identity_member_source_candidate_ids", [])
            )
            if not member_ids:
                add_validation_error(
                    error_rows,
                    decision_id,
                    case_id,
                    "INVALID",
                    "EMPTY_CANONICAL_ALTERNATIVE",
                    "canonical 대안에는 SourceRecord가 한 건 이상 필요합니다.",
                )
                invalid = True
                continue
            alternative_id = create_stable_id(
                identifier_policy["canonical_alternative_prefix"],
                [case_id] + member_ids,
                identifier_policy,
            )
            alternative_specs.append((alternative, alternative_id))
            allowed_entity_types = set(
                resolution_policy["entity_type_mapping"].values()
            )
            alternative_entity_type = alternative["entity_type"]
            if alternative_entity_type not in allowed_entity_types:
                add_validation_error(
                    error_rows,
                    decision_id,
                    case_id,
                    "INVALID",
                    "INVALID_ENTITY_TYPE",
                    alternative_entity_type,
                )
                invalid = True
            if task is not None and task["entity_type_proposal"]:
                if alternative_entity_type != task["entity_type_proposal"]:
                    add_validation_error(
                        error_rows,
                        decision_id,
                        case_id,
                        "NEEDS_MANUAL_REVIEW",
                        "ENTITY_TYPE_REVIEW_REQUIRED",
                        alternative_entity_type,
                    )
                    manual_review = True
            for candidate_id in member_ids:
                candidate = candidate_rows.get(candidate_id)
                if candidate is None:
                    continue
                if candidate["category_compatibility"] == "CONFLICT":
                    add_validation_error(
                        error_rows,
                        decision_id,
                        case_id,
                        "INVALID",
                        "CATEGORY_CONFLICT_IDENTITY_MEMBER",
                        candidate_id,
                    )
                    invalid = True
            for left_id, right_id in combinations(member_ids, 2):
                pair = pair_by_ids.get(frozenset([left_id, right_id]))
                if pair is None:
                    add_validation_error(
                        error_rows,
                        decision_id,
                        case_id,
                        "INVALID",
                        "MISSING_PAIR_EVIDENCE",
                        f"{left_id},{right_id}",
                    )
                    invalid = True
                    continue
                conflicts = loads(pair["conflict_signals_json"])
                if conflicts:
                    add_validation_error(
                        error_rows,
                        decision_id,
                        case_id,
                        "INVALID",
                        "STRONG_PAIR_CONFLICT",
                        dumps(conflicts, ensure_ascii=False),
                    )
                    invalid = True
                    continue
                if str(pair["merge_eligible"]).lower() != "true":
                    add_validation_error(
                        error_rows,
                        decision_id,
                        case_id,
                        "NEEDS_MANUAL_REVIEW",
                        "INSUFFICIENT_PAIR_EVIDENCE",
                        f"{left_id},{right_id}",
                    )
                    manual_review = True

        if isinstance(decision.get("ambiguous_sources"), list) and decision.get(
            "ambiguous_sources"
        ):
            add_validation_error(
                error_rows,
                decision_id,
                case_id,
                "NEEDS_MANUAL_REVIEW",
                "AMBIGUOUS_SOURCE_REMAINS",
                "AMBIGUOUS 후보가 남아 있습니다.",
            )
            manual_review = True
        verification_status = "VERIFIED"
        if manual_review:
            verification_status = "NEEDS_MANUAL_REVIEW"
        if invalid:
            verification_status = "INVALID"

        evidence_only_sources = decision.get("evidence_only_sources")
        rejected_sources = decision.get("rejected_sources")
        ambiguous_sources = decision.get("ambiguous_sources")
        evidence_only_count = 0
        rejected_count = 0
        ambiguous_count = 0
        if isinstance(evidence_only_sources, list):
            evidence_only_count = len(evidence_only_sources)
        if isinstance(rejected_sources, list):
            rejected_count = len(rejected_sources)
        if isinstance(ambiguous_sources, list):
            ambiguous_count = len(ambiguous_sources)
        decision_rows.append(
            {
                "term_decision_id": decision_id,
                "term_review_task_id": task_id,
                "resolution_case_id": case_id,
                "input_decision_status": decision.get("decision_status", ""),
                "verification_status": verification_status,
                "alternative_count": len(alternative_specs),
                "evidence_only_count": evidence_only_count,
                "rejected_count": rejected_count,
                "ambiguous_count": ambiguous_count,
                "decision_reason": decision.get("decision_reason", ""),
                "review_model": decision.get("review_model", ""),
                "prompt_version": decision.get("prompt_version", ""),
                "resolution_policy_version": policy["policy_version"],
            }
        )
        if verification_status != "VERIFIED":
            continue

        alternative_by_candidate: dict[str, str] = {}
        for alternative, alternative_id in alternative_specs:
            member_ids = sorted(
                alternative["identity_member_source_candidate_ids"]
            )
            source_record_ids = [
                candidate_rows[candidate_id]["source_record_id"]
                for candidate_id in member_ids
            ]
            alternative_rows.append(
                {
                    "canonical_alternative_id": alternative_id,
                    "resolution_case_id": case_id,
                    "canonical_id": "",
                    "display_name_proposal": alternative["display_name"],
                    "entity_type_proposal": alternative["entity_type"],
                    "source_candidate_ids_json": dumps(
                        member_ids,
                        ensure_ascii=False,
                    ),
                    "identity_member_source_ids_json": dumps(
                        source_record_ids,
                        ensure_ascii=False,
                    ),
                    "member_count": len(member_ids),
                    "merge_gate_passed": True,
                    "verification_status": "VERIFIED",
                    "term_decision_id": decision_id,
                    "decision_reason": alternative["reason"],
                    "resolution_policy_version": policy["policy_version"],
                }
            )
            for candidate_id in member_ids:
                alternative_by_candidate[candidate_id] = alternative_id
        for candidate_id in sorted(expected_candidate_ids):
            role, _, role_reason = classified[candidate_id]
            role_rows.append(
                {
                    "source_candidate_id": candidate_id,
                    "source_record_id": candidate_rows[candidate_id][
                        "source_record_id"
                    ],
                    "resolution_case_id": case_id,
                    "canonical_alternative_id": alternative_by_candidate.get(
                        candidate_id,
                        "",
                    ),
                    "verified_role": role,
                    "verification_status": "VERIFIED",
                    "term_decision_id": decision_id,
                    "role_reason": role_reason,
                    "resolution_policy_version": policy["policy_version"],
                }
            )

    output_columns = {
        "term_resolution_decisions": [
            "term_decision_id",
            "term_review_task_id",
            "resolution_case_id",
            "input_decision_status",
            "verification_status",
            "alternative_count",
            "evidence_only_count",
            "rejected_count",
            "ambiguous_count",
            "decision_reason",
            "review_model",
            "prompt_version",
            "resolution_policy_version",
        ],
        "reviewed_canonical_alternatives": [
            "canonical_alternative_id",
            "resolution_case_id",
            "canonical_id",
            "display_name_proposal",
            "entity_type_proposal",
            "source_candidate_ids_json",
            "identity_member_source_ids_json",
            "member_count",
            "merge_gate_passed",
            "verification_status",
            "term_decision_id",
            "decision_reason",
            "resolution_policy_version",
        ],
        "reviewed_source_roles": [
            "source_candidate_id",
            "source_record_id",
            "resolution_case_id",
            "canonical_alternative_id",
            "verified_role",
            "verification_status",
            "term_decision_id",
            "role_reason",
            "resolution_policy_version",
        ],
        "term_decision_validation_errors": [
            "term_decision_id",
            "resolution_case_id",
            "severity",
            "error_code",
            "message",
        ],
    }
    row_sets = {
        "term_resolution_decisions": decision_rows,
        "reviewed_canonical_alternatives": alternative_rows,
        "reviewed_source_roles": role_rows,
        "term_decision_validation_errors": error_rows,
    }
    return {
        table_name: pd.DataFrame(
            row_sets[table_name],
            columns=columns,
        )
        for table_name, columns in output_columns.items()
    }


def write_term_decision_tables(
    tables: dict[str, pd.DataFrame],
    output_dir: str,
    policy: dict,
) -> dict[str, str]:
    """term decision gate 결과를 정책 파일명으로 저장한다."""
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_files = policy["entity_resolution"]["semantic_review"][
        "term_decision_output_files"
    ]
    written: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = output_directory / output_files[table_name]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        written[table_name] = str(output_path)
    return written


if __name__ == "__main__":
    parser = ArgumentParser(
        description="Entity Resolution term-level 의미 판정 task 생성·결정 검증"
    )
    parser.add_argument("input_dir", help="ER staging CSV 폴더")
    parser.add_argument("output_dir", help="review task·결정 출력 폴더")
    parser.add_argument(
        "--decisions",
        default="",
        help="검증할 term identity model decision JSONL 경로",
    )
    parser.add_argument(
        "--policy",
        default=str(
            Path(__file__).resolve().parent.parent
            / "config"
            / "resolution_policy.json"
        ),
        help="Entity Resolution 정책 JSON 경로",
    )
    cli_args = parser.parse_args()
    pipeline_policy = load_pipeline_policy(cli_args.policy)
    resolution_tables = load_resolution_package(
        cli_args.input_dir,
        pipeline_policy,
    )
    review_tasks = build_term_review_tasks(
        resolution_tables,
        pipeline_policy,
    )
    semantic_policy = pipeline_policy["entity_resolution"]["semantic_review"]
    task_path = Path(cli_args.output_dir) / semantic_policy["term_task_file"]
    write_jsonl(review_tasks, str(task_path))
    print(f"term review task: {len(review_tasks)}건, {task_path}")
    if cli_args.decisions:
        proposed_decisions = load_jsonl(cli_args.decisions)
        decision_tables = validate_term_decisions(
            proposed_decisions,
            review_tasks,
            resolution_tables,
            pipeline_policy,
        )
        output_paths = write_term_decision_tables(
            decision_tables,
            cli_args.output_dir,
            pipeline_policy,
        )
        print(dumps(output_paths, ensure_ascii=False, indent=2))

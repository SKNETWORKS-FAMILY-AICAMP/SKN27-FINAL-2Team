from __future__ import annotations

import re
from collections import Counter
from hashlib import new as new_hash
from json import dumps, load
from pathlib import Path

import pandas as pd


def load_choice_relation_policy(policy_path: str) -> dict:
    """정답–오답 관계 분석 설정을 읽고 필수 section을 검사한다."""
    path = Path(policy_path)
    if not path.is_file():
        raise FileNotFoundError(f"choice relation 설정이 없습니다: {path}")
    with path.open("r", encoding="utf-8") as policy_file:
        policy = load(policy_file)

    required_sections = {
        "policy_version",
        "prompt_version",
        "input",
        "identifier",
        "generator_model",
        "evaluator_model",
        "evaluator",
        "executor",
        "validation",
        "goldset",
        "allowed_values",
        "paths",
    }
    missing_sections = required_sections.difference(policy)
    if missing_sections:
        missing_text = ", ".join(sorted(missing_sections))
        raise ValueError(f"choice relation 필수 설정이 없습니다: {missing_text}")
    return policy


def load_problem_records(problem_path: str) -> list[dict]:
    """기출문제 JSON 배열을 읽고 problem_id 유일성을 검사한다."""
    path = Path(problem_path)
    if not path.is_file():
        raise FileNotFoundError(f"기출문제 JSON이 없습니다: {path}")
    with path.open("r", encoding="utf-8") as problem_file:
        records = load(problem_file)
    if not isinstance(records, list):
        raise ValueError("기출문제 JSON 최상위 값은 배열이어야 합니다.")

    problem_ids: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("기출문제 배열의 모든 항목은 객체여야 합니다.")
        problem_id = str(record.get("problem_id") or "").strip()
        if not problem_id:
            raise ValueError("problem_id가 비어 있는 기출문제가 있습니다.")
        problem_ids.append(problem_id)
    duplicate_ids = sorted(
        problem_id
        for problem_id, count in Counter(problem_ids).items()
        if count > 1
    )
    if duplicate_ids:
        raise ValueError(
            "problem_id가 중복됩니다: "
            + dumps(duplicate_ids, ensure_ascii=False)
        )
    return records


def create_task_id(problem_id: str, policy: dict) -> str:
    """문항과 분석 정책 버전에 고정되는 task ID를 만든다."""
    identifier_policy = policy["identifier"]
    hasher = new_hash(identifier_policy["hash_algorithm"])
    source = f"{problem_id}|{policy['policy_version']}"
    hasher.update(source.encode("utf-8"))
    digest_length = int(identifier_policy["digest_length"])
    return (
        f"{identifier_policy['task_prefix']}"
        f"{hasher.hexdigest()[:digest_length]}"
    )


def build_choice_relation_tasks(
    problem_records: list[dict],
    policy: dict,
    limit: int = 0,
    selected_problem_ids: set[str] | None = None,
) -> dict[str, object]:
    """일반 선택형 문항을 관계 분석 task와 원문 선지 행으로 만든다."""
    if limit < 0:
        raise ValueError("choice relation task limit은 0 이상이어야 합니다.")

    input_policy = policy["input"]
    included_sources = set(input_policy["included_data_sources"])
    included_tasks = set(input_policy["included_question_tasks"])
    negative_patterns = [
        re.compile(pattern)
        for pattern in input_policy["negative_question_patterns"]
    ]
    expected_choice_count = int(input_policy["expected_choice_count"])
    expected_answer_count = int(input_policy["expected_answer_count"])
    exclude_missing_references = bool(
        input_policy["exclude_missing_references"]
    )
    choice_separator = str(policy["identifier"]["choice_separator"])
    reference_marker_patterns = [
        re.compile(pattern)
        for pattern in input_policy["reference_marker_patterns"]
    ]

    tasks: list[dict] = []
    source_choice_rows: list[dict] = []
    exclusion_rows: list[dict] = []
    input_integrity_rows: list[dict] = []
    selection_counts = {
        "input_problem_count": len(problem_records),
        "excluded_problem_id_filter_count": 0,
        "excluded_data_source_count": 0,
        "excluded_question_task_count": 0,
        "excluded_negative_wording_count": 0,
        "invalid_structure_count": 0,
        "missing_reference_problem_count": 0,
        "excluded_missing_reference_count": 0,
        "selected_task_count": 0,
    }

    for record in problem_records:
        problem_id = str(record["problem_id"]).strip()
        if (
            selected_problem_ids is not None
            and problem_id not in selected_problem_ids
        ):
            selection_counts["excluded_problem_id_filter_count"] += 1
            continue
        data_source = str(record.get("data_source") or "")
        question_task = str(record.get("question_task") or "")
        if data_source not in included_sources:
            selection_counts["excluded_data_source_count"] += 1
            continue
        if question_task not in included_tasks:
            selection_counts["excluded_question_task_count"] += 1
            continue

        question = str(record.get("question") or "").strip()
        if any(pattern.search(question) for pattern in negative_patterns):
            selection_counts["excluded_negative_wording_count"] += 1
            exclusion_rows.append(
                {
                    "problem_id": problem_id,
                    "reason_code": "NEGATIVE_WORDING_OUT_OF_SCOPE",
                    "message": "일반 선택형 1차 범위에서 부정 발문을 제외했습니다.",
                }
            )
            continue

        choices = record.get("choices")
        if not isinstance(choices, list):
            choices = []
        answer_choices = [
            choice
            for choice in choices
            if isinstance(choice, dict) and choice.get("is_answer") is True
        ]
        invalid_messages: list[str] = []
        if len(choices) != expected_choice_count:
            invalid_messages.append(
                f"선지 수 {len(choices)} != {expected_choice_count}"
            )
        if len(answer_choices) != expected_answer_count:
            invalid_messages.append(
                f"정답 선지 수 {len(answer_choices)} != {expected_answer_count}"
            )
        if any(
            not isinstance(choice, dict)
            or not str(choice.get("content") or "").strip()
            for choice in choices
        ):
            invalid_messages.append("비어 있거나 객체가 아닌 선지가 있습니다.")
        if invalid_messages:
            selection_counts["invalid_structure_count"] += 1
            exclusion_rows.append(
                {
                    "problem_id": problem_id,
                    "reason_code": "INVALID_CHOICE_STRUCTURE",
                    "message": "; ".join(invalid_messages),
                }
            )
            continue
        material = str(record.get("material") or "").strip()
        reference_markers = sorted(
            {
                match.group(0)
                for pattern in reference_marker_patterns
                for match in pattern.finditer(question)
            }
        )
        missing_reference_markers = [
            marker
            for marker in reference_markers
            if marker not in material
        ]
        input_integrity_status = policy["validation"][
            "complete_input_status"
        ]
        if missing_reference_markers:
            input_integrity_status = policy["validation"][
                "missing_reference_status"
            ]
            selection_counts["missing_reference_problem_count"] += 1
            input_integrity_rows.append(
                {
                    "problem_id": problem_id,
                    "input_integrity_status": input_integrity_status,
                    "reference_markers_json": dumps(
                        reference_markers,
                        ensure_ascii=False,
                    ),
                    "missing_reference_markers_json": dumps(
                        missing_reference_markers,
                        ensure_ascii=False,
                    ),
                    "material": material,
                    "question": question,
                }
            )
            if exclude_missing_references:
                selection_counts["excluded_missing_reference_count"] += 1
                exclusion_rows.append(
                    {
                        "problem_id": problem_id,
                        "reason_code": "MISSING_QUESTION_REFERENCE",
                        "message": (
                            "발문 참조 표식이 자료에 없어 clean-only "
                            "분석에서 제외했습니다."
                        ),
                    }
                )
                continue
        if limit > 0 and len(tasks) >= limit:
            break

        task_id = create_task_id(problem_id, policy)
        task_choices: list[dict] = []
        answer_choice_id = ""
        for choice_index, choice in enumerate(choices, start=1):
            choice_id = f"{problem_id}{choice_separator}{choice_index}"
            is_answer_key = bool(choice["is_answer"])
            if is_answer_key:
                answer_choice_id = choice_id
            task_choice = {
                "choice_id": choice_id,
                "choice_index": choice_index,
                "text": str(choice["content"]).strip(),
                "is_answer_key": is_answer_key,
            }
            task_choices.append(task_choice)
            source_choice_rows.append(
                {
                    "choice_relation_task_id": task_id,
                    "problem_id": problem_id,
                    **task_choice,
                }
            )

        tasks.append(
            {
                "choice_relation_task_id": task_id,
                "problem_id": problem_id,
                "data_source": data_source,
                "question_task": question_task,
                "material": material,
                "question": question,
                "topic": str(record.get("topic") or "").strip(),
                "topic_type": str(record.get("topic_type") or "").strip(),
                "major_type": str(record.get("major_type") or "").strip(),
                "minor_type": str(record.get("minor_type") or "").strip(),
                "difficulty_label": str(
                    record.get("difficulty_label") or ""
                ).strip(),
                "answer_choice_id": answer_choice_id,
                "choices": task_choices,
                "reference_markers": reference_markers,
                "missing_reference_markers": missing_reference_markers,
                "input_integrity_status": input_integrity_status,
                "analysis_policy_version": policy["policy_version"],
                "prompt_version": policy["prompt_version"],
            }
        )

    selection_counts["selected_task_count"] = len(tasks)
    if selected_problem_ids is not None:
        observed_problem_ids = {
            task["problem_id"] for task in tasks
        }
        unresolved_problem_ids = selected_problem_ids.difference(
            observed_problem_ids
        )
        if unresolved_problem_ids:
            raise ValueError(
                "선택한 problem_id가 현재 분석 범위에 없거나 유효하지 않습니다: "
                + dumps(sorted(unresolved_problem_ids), ensure_ascii=False)
            )
    return {
        "tasks": tasks,
        "source_choices": pd.DataFrame(source_choice_rows),
        "exclusions": pd.DataFrame(
            exclusion_rows,
            columns=["problem_id", "reason_code", "message"],
        ),
        "input_integrity_issues": pd.DataFrame(
            input_integrity_rows,
            columns=[
                "problem_id",
                "input_integrity_status",
                "reference_markers_json",
                "missing_reference_markers_json",
                "material",
                "question",
            ],
        ),
        "summary": selection_counts,
    }


def apply_controlled_fields(
    decision: dict,
    task: dict,
    policy: dict,
) -> dict:
    """모델이 바꿀 수 없는 ID·상태·버전 필드를 입력값으로 고정한다."""
    controlled = dict(decision)
    controlled["choice_relation_task_id"] = task[
        "choice_relation_task_id"
    ]
    controlled["problem_id"] = task["problem_id"]
    controlled["decision_status"] = policy["validation"]["decision_status"]
    controlled["review_model"] = policy["generator_model"]["model"]
    controlled["prompt_version"] = policy["prompt_version"]
    return controlled


def validate_choice_relation_decision(
    decision: dict,
    task: dict,
    policy: dict,
) -> list[dict]:
    """한 관계 분석 결과의 ID·개수·허용값·신뢰도 범위를 검사한다."""
    errors: list[dict] = []

    def add_error(code: str, message: str) -> None:
        errors.append({"error_code": code, "message": message})

    required_strings = [
        "choice_relation_task_id",
        "problem_id",
        "decision_status",
        "review_model",
        "prompt_version",
        "analysis_status",
        "reason",
    ]
    for field_name in required_strings:
        if not isinstance(decision.get(field_name), str) or not decision.get(
            field_name
        ):
            add_error(
                "DECISION_SCHEMA_ERROR",
                f"{field_name}: 비어 있지 않은 문자열이 필요합니다.",
            )

    allowed_values = policy["allowed_values"]
    if decision.get("analysis_status") not in set(
        allowed_values["analysis_status"]
    ):
        add_error(
            "INVALID_ANALYSIS_STATUS",
            str(decision.get("analysis_status")),
        )

    target = decision.get("question_target")
    target_fields = ["name", "entity_type", "era", "theme", "inference_basis"]
    if not isinstance(target, dict):
        add_error("DECISION_SCHEMA_ERROR", "question_target은 객체여야 합니다.")
    elif isinstance(target, dict):
        for field_name in target_fields:
            if not isinstance(target.get(field_name), str):
                add_error(
                    "DECISION_SCHEMA_ERROR",
                    f"question_target.{field_name}: 문자열이 필요합니다.",
                )

    expected_choices = {
        choice["choice_id"]: choice for choice in task["choices"]
    }
    claims = decision.get("choice_claims")
    observed_claim_ids: list[str] = []
    if not isinstance(claims, list):
        add_error("DECISION_SCHEMA_ERROR", "choice_claims는 배열이어야 합니다.")
        claims = []
    for claim in claims:
        if not isinstance(claim, dict):
            add_error("DECISION_SCHEMA_ERROR", "choice claim은 객체여야 합니다.")
            continue
        choice_id = str(claim.get("choice_id") or "")
        observed_claim_ids.append(choice_id)
        expected_choice = expected_choices.get(choice_id)
        if expected_choice is None:
            add_error("UNKNOWN_CHOICE_ID", choice_id)
            continue
        if claim.get("choice_index") != expected_choice["choice_index"]:
            add_error("CHOICE_INDEX_MISMATCH", choice_id)
        if claim.get("contextual_validity") not in set(
            allowed_values["contextual_validity"]
        ):
            add_error("INVALID_CONTEXTUAL_VALIDITY", choice_id)
        if claim.get("standalone_fact_status") not in set(
            allowed_values["standalone_fact_status"]
        ):
            add_error("INVALID_STANDALONE_FACT_STATUS", choice_id)
        for fact_field in ["contextual_claim", "actual_fact"]:
            fact = claim.get(fact_field)
            if not isinstance(fact, dict):
                add_error(
                    "DECISION_SCHEMA_ERROR",
                    f"{choice_id}.{fact_field}: 객체가 필요합니다.",
                )
                continue
            for component in [
                "subject",
                "predicate",
                "object",
                "era",
                "location",
            ]:
                if not isinstance(fact.get(component), str):
                    add_error(
                        "DECISION_SCHEMA_ERROR",
                        f"{choice_id}.{fact_field}.{component}: 문자열이 필요합니다.",
                    )

    if len(observed_claim_ids) != len(set(observed_claim_ids)):
        add_error("DUPLICATE_CHOICE_CLAIM", "choice_id가 중복되었습니다.")
    missing_claim_ids = set(expected_choices).difference(observed_claim_ids)
    if missing_claim_ids:
        add_error(
            "MISSING_CHOICE_CLAIM",
            dumps(sorted(missing_claim_ids), ensure_ascii=False),
        )

    answer_choice_id = task["answer_choice_id"]
    expected_distractor_ids = {
        choice_id
        for choice_id, choice in expected_choices.items()
        if not choice["is_answer_key"]
    }
    relations = decision.get("distractor_relations")
    observed_distractor_ids: list[str] = []
    if not isinstance(relations, list):
        add_error(
            "DECISION_SCHEMA_ERROR",
            "distractor_relations는 배열이어야 합니다.",
        )
        relations = []
    for relation in relations:
        if not isinstance(relation, dict):
            add_error(
                "DECISION_SCHEMA_ERROR",
                "distractor relation은 객체여야 합니다.",
            )
            continue
        distractor_id = str(relation.get("distractor_choice_id") or "")
        observed_distractor_ids.append(distractor_id)
        if relation.get("answer_choice_id") != answer_choice_id:
            add_error("ANSWER_CHOICE_ID_MISMATCH", distractor_id)
        if distractor_id not in expected_distractor_ids:
            add_error("UNKNOWN_DISTRACTOR_CHOICE_ID", distractor_id)
        if relation.get("primary_relation_type") not in set(
            allowed_values["primary_relation_type"]
        ):
            add_error("INVALID_PRIMARY_RELATION_TYPE", distractor_id)
        secondary_relation_types = relation.get(
            "secondary_relation_types"
        )
        if not isinstance(secondary_relation_types, list):
            add_error(
                "DECISION_SCHEMA_ERROR",
                f"{distractor_id}.secondary_relation_types: 배열이 필요합니다.",
            )
        elif isinstance(secondary_relation_types, list):
            if len(secondary_relation_types) != len(
                set(secondary_relation_types)
            ):
                add_error(
                    "DUPLICATE_SECONDARY_RELATION_TYPE",
                    distractor_id,
                )
            unknown_relation_types = set(
                secondary_relation_types
            ).difference(allowed_values["primary_relation_type"])
            if unknown_relation_types:
                add_error(
                    "INVALID_SECONDARY_RELATION_TYPE",
                    dumps(
                        sorted(unknown_relation_types),
                        ensure_ascii=False,
                    ),
                )
            if (
                relation.get("primary_relation_type")
                in secondary_relation_types
            ):
                add_error(
                    "PRIMARY_RELATION_REPEATED_AS_SECONDARY",
                    distractor_id,
                )
        for dimension_field in ["shared_dimensions", "changed_dimensions"]:
            dimensions = relation.get(dimension_field)
            if not isinstance(dimensions, list):
                add_error(
                    "DECISION_SCHEMA_ERROR",
                    f"{distractor_id}.{dimension_field}: 배열이 필요합니다.",
                )
                continue
            if len(dimensions) != len(set(dimensions)):
                add_error(
                    "DUPLICATE_COMPARISON_DIMENSION",
                    f"{distractor_id}.{dimension_field}",
                )
            unknown_dimensions = set(dimensions).difference(
                allowed_values["comparison_dimension"]
            )
            if unknown_dimensions:
                add_error(
                    "INVALID_COMPARISON_DIMENSION",
                    dumps(sorted(unknown_dimensions), ensure_ascii=False),
                )
        if relation.get("proximity") not in set(
            allowed_values["proximity"]
        ):
            add_error("INVALID_PROXIMITY", distractor_id)
        confidence = relation.get("confidence")
        if not isinstance(confidence, (int, float)):
            add_error("INVALID_CONFIDENCE", distractor_id)
        elif not 0 <= float(confidence) <= 1:
            add_error("INVALID_CONFIDENCE", distractor_id)

    if len(observed_distractor_ids) != len(set(observed_distractor_ids)):
        add_error(
            "DUPLICATE_DISTRACTOR_RELATION",
            "distractor_choice_id가 중복되었습니다.",
        )
    missing_relation_ids = expected_distractor_ids.difference(
        observed_distractor_ids
    )
    if missing_relation_ids:
        add_error(
            "MISSING_DISTRACTOR_RELATION",
            dumps(sorted(missing_relation_ids), ensure_ascii=False),
        )

    confidence = decision.get("confidence")
    if not isinstance(confidence, (int, float)):
        add_error("INVALID_CONFIDENCE", "decision.confidence")
    elif not 0 <= float(confidence) <= 1:
        add_error("INVALID_CONFIDENCE", "decision.confidence")
    return errors


def determine_verification_status(
    decision: dict,
    task: dict,
    policy: dict,
) -> str:
    """유효 결과를 신뢰도와 불확실성에 따라 자동 승인 또는 보류한다."""
    validation_policy = policy["validation"]
    if decision["analysis_status"] != "ANALYZED":
        return validation_policy["manual_review_status"]
    if any(
        claim["contextual_validity"] == "UNCERTAIN"
        or claim["standalone_fact_status"] == "UNCERTAIN"
        for claim in decision["choice_claims"]
    ):
        return validation_policy["manual_review_status"]
    if any(
        relation["proximity"] == "UNCERTAIN"
        for relation in decision["distractor_relations"]
    ):
        return validation_policy["manual_review_status"]

    minimum_confidence = float(
        validation_policy["minimum_auto_verified_confidence"]
    )
    confidence_values = [float(decision["confidence"])] + [
        float(relation["confidence"])
        for relation in decision["distractor_relations"]
    ]
    if min(confidence_values) < minimum_confidence:
        return validation_policy["manual_review_status"]
    return validation_policy["verified_status"]


def validate_choice_relation_decisions(
    decisions: list[dict],
    tasks: list[dict],
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """모델 결과를 검증하고 선지 명제·오답 관계 CSV 행으로 평탄화한다."""
    tasks_by_id = {
        task["choice_relation_task_id"]: task for task in tasks
    }
    observed_task_ids: set[str] = set()
    decision_rows: list[dict] = []
    choice_rows: list[dict] = []
    relation_rows: list[dict] = []
    error_rows: list[dict] = []

    for raw_decision in decisions:
        raw_task_id = str(raw_decision.get("choice_relation_task_id") or "")
        task = tasks_by_id.get(raw_task_id)
        if task is None:
            error_rows.append(
                {
                    "choice_relation_task_id": raw_task_id,
                    "problem_id": str(raw_decision.get("problem_id") or ""),
                    "error_code": "UNKNOWN_CHOICE_RELATION_TASK",
                    "message": "입력 task에 없는 결과입니다.",
                }
            )
            continue
        if raw_task_id in observed_task_ids:
            error_rows.append(
                {
                    "choice_relation_task_id": raw_task_id,
                    "problem_id": task["problem_id"],
                    "error_code": "DUPLICATE_CHOICE_RELATION_DECISION",
                    "message": "동일 task 결과가 중복되었습니다.",
                }
            )
            continue
        observed_task_ids.add(raw_task_id)

        decision = apply_controlled_fields(raw_decision, task, policy)
        validation_errors = validate_choice_relation_decision(
            decision,
            task,
            policy,
        )
        verification_status = policy["validation"]["invalid_status"]
        if not validation_errors:
            verification_status = determine_verification_status(
                decision,
                task,
                policy,
            )
        for error in validation_errors:
            error_rows.append(
                {
                    "choice_relation_task_id": raw_task_id,
                    "problem_id": task["problem_id"],
                    **error,
                }
            )

        decision_rows.append(
            {
                "choice_relation_task_id": raw_task_id,
                "problem_id": task["problem_id"],
                "analysis_status": str(
                    decision.get("analysis_status") or ""
                ),
                "input_integrity_status": task[
                    "input_integrity_status"
                ],
                "missing_reference_markers_json": dumps(
                    task["missing_reference_markers"],
                    ensure_ascii=False,
                ),
                "confidence": decision.get("confidence", ""),
                "verification_status": verification_status,
                "reason": str(decision.get("reason") or ""),
                "review_model": policy["generator_model"]["model"],
                "prompt_version": policy["prompt_version"],
                "analysis_policy_version": policy["policy_version"],
            }
        )
        if validation_errors:
            continue

        target = decision["question_target"]
        choices_by_id = {
            choice["choice_id"]: choice for choice in task["choices"]
        }
        for claim in decision["choice_claims"]:
            source_choice = choices_by_id[claim["choice_id"]]
            contextual_claim = claim["contextual_claim"]
            actual_fact = claim["actual_fact"]
            choice_rows.append(
                {
                    "choice_relation_task_id": raw_task_id,
                    "problem_id": task["problem_id"],
                    "choice_id": claim["choice_id"],
                    "choice_index": claim["choice_index"],
                    "is_answer_key": source_choice["is_answer_key"],
                    "original_text": source_choice["text"],
                    "question_target_name": target["name"],
                    "question_target_entity_type": target["entity_type"],
                    "question_target_era": target["era"],
                    "question_theme": target["theme"],
                    "contextual_validity": claim["contextual_validity"],
                    "standalone_fact_status": claim[
                        "standalone_fact_status"
                    ],
                    "contextual_subject": contextual_claim["subject"],
                    "contextual_predicate": contextual_claim["predicate"],
                    "contextual_object": contextual_claim["object"],
                    "contextual_era": contextual_claim["era"],
                    "contextual_location": contextual_claim["location"],
                    "actual_subject": actual_fact["subject"],
                    "actual_predicate": actual_fact["predicate"],
                    "actual_object": actual_fact["object"],
                    "actual_era": actual_fact["era"],
                    "actual_location": actual_fact["location"],
                    "verification_status": verification_status,
                    "explanation": claim["explanation"],
                }
            )
        for relation in decision["distractor_relations"]:
            relation_rows.append(
                {
                    "choice_relation_task_id": raw_task_id,
                    "problem_id": task["problem_id"],
                    "answer_choice_id": relation["answer_choice_id"],
                    "distractor_choice_id": relation[
                        "distractor_choice_id"
                    ],
                    "primary_relation_type": relation[
                        "primary_relation_type"
                    ],
                    "secondary_relation_types_json": dumps(
                        relation["secondary_relation_types"],
                        ensure_ascii=False,
                    ),
                    "shared_dimensions_json": dumps(
                        relation["shared_dimensions"],
                        ensure_ascii=False,
                    ),
                    "changed_dimensions_json": dumps(
                        relation["changed_dimensions"],
                        ensure_ascii=False,
                    ),
                    "proximity": relation["proximity"],
                    "confidence": relation["confidence"],
                    "verification_status": verification_status,
                    "explanation": relation["explanation"],
                }
            )

    missing_task_ids = set(tasks_by_id).difference(observed_task_ids)
    for task_id in sorted(missing_task_ids):
        error_rows.append(
            {
                "choice_relation_task_id": task_id,
                "problem_id": tasks_by_id[task_id]["problem_id"],
                "error_code": "MISSING_CHOICE_RELATION_DECISION",
                "message": "task에 대응하는 결과가 없습니다.",
            }
        )

    return {
        "decisions": pd.DataFrame(decision_rows),
        "choice_claims": pd.DataFrame(choice_rows),
        "distractor_relations": pd.DataFrame(relation_rows),
        "validation_errors": pd.DataFrame(
            error_rows,
            columns=[
                "choice_relation_task_id",
                "problem_id",
                "error_code",
                "message",
            ],
        ),
    }

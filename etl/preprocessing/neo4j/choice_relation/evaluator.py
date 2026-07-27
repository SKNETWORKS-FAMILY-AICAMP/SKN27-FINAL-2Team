from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import new as new_hash
from json import dumps, loads
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from choice_relation.executor import load_jsonl


def canonicalize_evidence_url(url: str) -> str:
    """추적용 query와 fragment를 제거해 같은 출처 URL을 하나로 합친다."""
    parsed = urlsplit(url.strip())
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
        if not key.lower().startswith("utm_")
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            urlencode(filtered_query, doseq=True),
            "",
        )
    )


def extract_web_evidence_sources(response: object) -> list[dict[str, str]]:
    """Responses API의 검색 source와 URL citation을 중복 없이 추출한다."""
    sources: list[dict[str, str]] = []
    observed_urls: set[str] = set()
    output_items = getattr(response, "output", [])
    for output_item in output_items:
        item = output_item
        if hasattr(output_item, "model_dump"):
            item = output_item.model_dump()
        if not isinstance(item, dict):
            continue

        action = item.get("action")
        action_sources: list = []
        if isinstance(action, dict):
            raw_sources = action.get("sources")
            if isinstance(raw_sources, list):
                action_sources = raw_sources
        for source in action_sources:
            if not isinstance(source, dict):
                continue
            url = canonicalize_evidence_url(
                str(source.get("url") or "")
            )
            if not url or url in observed_urls:
                continue
            observed_urls.add(url)
            sources.append(
                {
                    "url": url,
                    "title": str(source.get("title") or "").strip(),
                }
            )

        content_items = item.get("content")
        if not isinstance(content_items, list):
            continue
        for content_item in content_items:
            if not isinstance(content_item, dict):
                continue
            annotations = content_item.get("annotations")
            if not isinstance(annotations, list):
                continue
            for annotation in annotations:
                if not isinstance(annotation, dict):
                    continue
                url = canonicalize_evidence_url(
                    str(annotation.get("url") or "")
                )
                if not url or url in observed_urls:
                    continue
                observed_urls.add(url)
                sources.append(
                    {
                        "url": url,
                        "title": str(
                            annotation.get("title") or ""
                        ).strip(),
                    }
                )
    return sources


def calculate_proposal_digest(proposal: dict, policy: dict) -> str:
    """생성 제안 내용이 바뀌면 평가 checkpoint가 무효화되도록 digest를 만든다."""
    identifier_policy = policy["identifier"]
    hasher = new_hash(identifier_policy["hash_algorithm"])
    serialized = dumps(
        proposal,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    hasher.update(serialized.encode("utf-8"))
    return hasher.hexdigest()


def build_choice_relation_evaluation_tasks(
    tasks: list[dict],
    proposals: list[dict],
    policy: dict,
) -> list[dict]:
    """원문 task와 생성 제안을 결합해 독립 평가 task를 만든다."""
    tasks_by_id = {
        task["choice_relation_task_id"]: task
        for task in tasks
    }
    evaluator_policy = policy["evaluator"]
    identifier_policy = policy["identifier"]
    evaluation_tasks: list[dict] = []
    observed_task_ids: set[str] = set()

    for proposal in proposals:
        task_id = str(proposal.get("choice_relation_task_id") or "")
        if task_id in observed_task_ids:
            raise ValueError(f"생성 제안 task ID가 중복되었습니다: {task_id}")
        task = tasks_by_id.get(task_id)
        if task is None:
            raise ValueError(f"평가할 원문 task를 찾을 수 없습니다: {task_id}")
        observed_task_ids.add(task_id)

        proposal_digest = calculate_proposal_digest(proposal, policy)
        hasher = new_hash(identifier_policy["hash_algorithm"])
        hasher.update(
            (
                f"{task_id}|{proposal_digest}|"
                f"{evaluator_policy['policy_version']}|"
                f"{evaluator_policy['prompt_version']}"
            ).encode("utf-8")
        )
        digest_length = int(identifier_policy["digest_length"])
        evaluation_id = (
            f"{evaluator_policy['task_prefix']}"
            f"{hasher.hexdigest()[:digest_length]}"
        )
        evaluation_tasks.append(
            {
                "choice_relation_evaluation_id": evaluation_id,
                "choice_relation_task_id": task_id,
                "problem_id": task["problem_id"],
                "proposal_digest": proposal_digest,
                "source_task": task,
                "generator_proposal": proposal,
                "evaluator_policy_version": evaluator_policy[
                    "policy_version"
                ],
                "evaluator_prompt_version": evaluator_policy[
                    "prompt_version"
                ],
            }
        )
    return evaluation_tasks


def apply_controlled_evaluation_fields(
    evaluation: dict,
    evaluation_task: dict,
    policy: dict,
) -> dict:
    """평가 모델이 바꿀 수 없는 ID·모델·버전을 코드에서 고정한다."""
    controlled = dict(evaluation)
    evaluator_policy = policy["evaluator"]
    controlled["choice_relation_evaluation_id"] = evaluation_task[
        "choice_relation_evaluation_id"
    ]
    controlled["choice_relation_task_id"] = evaluation_task[
        "choice_relation_task_id"
    ]
    controlled["problem_id"] = evaluation_task["problem_id"]
    controlled["review_model"] = policy["evaluator_model"]["model"]
    controlled["prompt_version"] = evaluator_policy["prompt_version"]
    if not isinstance(controlled.get("evidence_sources"), list):
        controlled["evidence_sources"] = []
    return controlled


def validate_choice_relation_evaluation(
    evaluation: dict,
    evaluation_task: dict,
    policy: dict,
) -> list[dict]:
    """평가 결과의 구조·ID·개수·허용값을 결정적으로 검사한다."""
    errors: list[dict] = []

    def add_error(code: str, message: str) -> None:
        errors.append({"error_code": code, "message": message})

    required_strings = [
        "choice_relation_evaluation_id",
        "choice_relation_task_id",
        "problem_id",
        "review_model",
        "prompt_version",
        "input_quality_status",
        "target_status",
        "target_reason",
        "summary",
    ]
    for field_name in required_strings:
        value = evaluation.get(field_name)
        if not isinstance(value, str) or not value:
            add_error(
                "EVALUATION_SCHEMA_ERROR",
                f"{field_name}: 비어 있지 않은 문자열이 필요합니다.",
            )

    controlled_fields = {
        "choice_relation_evaluation_id": evaluation_task[
            "choice_relation_evaluation_id"
        ],
        "choice_relation_task_id": evaluation_task[
            "choice_relation_task_id"
        ],
        "problem_id": evaluation_task["problem_id"],
        "review_model": policy["evaluator_model"]["model"],
        "prompt_version": policy["evaluator"]["prompt_version"],
    }
    for field_name, expected_value in controlled_fields.items():
        if evaluation.get(field_name) != expected_value:
            add_error(
                "EVALUATION_CONTROLLED_FIELD_MISMATCH",
                f"{field_name}: {evaluation.get(field_name)}",
            )

    input_quality_status = evaluation.get("input_quality_status")
    evaluator_policy = policy["evaluator"]
    allowed_input_statuses = set(
        evaluator_policy["input_quality_statuses"]
    )
    if input_quality_status not in allowed_input_statuses:
        add_error(
            "INVALID_INPUT_QUALITY_STATUS",
            str(input_quality_status),
        )
    input_quality_issues = evaluation.get("input_quality_issues")
    if not isinstance(input_quality_issues, list):
        add_error(
            "EVALUATION_SCHEMA_ERROR",
            "input_quality_issues는 배열이어야 합니다.",
        )
    elif any(not isinstance(issue, str) for issue in input_quality_issues):
        add_error(
            "EVALUATION_SCHEMA_ERROR",
            "input_quality_issues의 모든 값은 문자열이어야 합니다.",
        )
    allowed_review_statuses = {
        evaluator_policy["supported_status"],
        evaluator_policy["contradicted_status"],
        evaluator_policy["unverifiable_status"],
    }
    if evaluation.get("target_status") not in allowed_review_statuses:
        add_error(
            "INVALID_TARGET_STATUS",
            str(evaluation.get("target_status")),
        )

    evidence_sources = evaluation.get("evidence_sources")
    if not isinstance(evidence_sources, list):
        add_error(
            "EVALUATION_SCHEMA_ERROR",
            "evidence_sources는 배열이어야 합니다.",
        )
        evidence_sources = []
    for source in evidence_sources:
        if not isinstance(source, dict):
            add_error(
                "EVALUATION_SCHEMA_ERROR",
                "evidence source는 객체여야 합니다.",
            )
            continue
        if not str(source.get("url") or "").strip():
            add_error(
                "EVALUATION_SCHEMA_ERROR",
                "evidence source URL이 비어 있습니다.",
            )
        if not isinstance(source.get("title"), str):
            add_error(
                "EVALUATION_SCHEMA_ERROR",
                "evidence source title은 문자열이어야 합니다.",
            )

    proposal = evaluation_task["generator_proposal"]
    expected_choice_ids = {
        str(claim["choice_id"])
        for claim in proposal["choice_claims"]
    }
    choice_reviews = evaluation.get("choice_reviews")
    observed_choice_ids: list[str] = []
    if not isinstance(choice_reviews, list):
        add_error(
            "EVALUATION_SCHEMA_ERROR",
            "choice_reviews는 배열이어야 합니다.",
        )
        choice_reviews = []
    for review in choice_reviews:
        if not isinstance(review, dict):
            add_error(
                "EVALUATION_SCHEMA_ERROR",
                "choice review는 객체여야 합니다.",
            )
            continue
        choice_id = str(review.get("choice_id") or "")
        observed_choice_ids.append(choice_id)
        if choice_id not in expected_choice_ids:
            add_error("UNKNOWN_EVALUATION_CHOICE_ID", choice_id)
        if review.get("claim_status") not in allowed_review_statuses:
            add_error("INVALID_CLAIM_STATUS", choice_id)
        corrected_fact = review.get("corrected_actual_fact")
        if not isinstance(corrected_fact, dict):
            add_error(
                "EVALUATION_SCHEMA_ERROR",
                f"{choice_id}.corrected_actual_fact는 객체여야 합니다.",
            )
            continue
        for component in [
            "subject",
            "predicate",
            "object",
            "era",
            "location",
        ]:
            if not isinstance(corrected_fact.get(component), str):
                add_error(
                    "EVALUATION_SCHEMA_ERROR",
                    f"{choice_id}.corrected_actual_fact.{component}",
                )
        if not isinstance(review.get("reason"), str):
            add_error(
                "EVALUATION_SCHEMA_ERROR",
                f"{choice_id}.reason",
            )
    if len(observed_choice_ids) != len(set(observed_choice_ids)):
        add_error(
            "DUPLICATE_EVALUATION_CHOICE",
            "choice review ID가 중복되었습니다.",
        )
    missing_choice_ids = expected_choice_ids.difference(
        observed_choice_ids
    )
    if missing_choice_ids:
        add_error(
            "MISSING_EVALUATION_CHOICE",
            dumps(sorted(missing_choice_ids), ensure_ascii=False),
        )

    expected_relation_ids = {
        str(relation["distractor_choice_id"])
        for relation in proposal["distractor_relations"]
    }
    allowed_relation_types = set(
        policy["allowed_values"]["primary_relation_type"]
    )
    relation_reviews = evaluation.get("relation_reviews")
    observed_relation_ids: list[str] = []
    if not isinstance(relation_reviews, list):
        add_error(
            "EVALUATION_SCHEMA_ERROR",
            "relation_reviews는 배열이어야 합니다.",
        )
        relation_reviews = []
    for review in relation_reviews:
        if not isinstance(review, dict):
            add_error(
                "EVALUATION_SCHEMA_ERROR",
                "relation review는 객체여야 합니다.",
            )
            continue
        distractor_id = str(review.get("distractor_choice_id") or "")
        observed_relation_ids.append(distractor_id)
        if distractor_id not in expected_relation_ids:
            add_error("UNKNOWN_EVALUATION_RELATION_ID", distractor_id)
        if review.get("relation_status") not in allowed_review_statuses:
            add_error("INVALID_RELATION_STATUS", distractor_id)
        primary_relation = review.get("corrected_primary_relation_type")
        if primary_relation not in allowed_relation_types:
            add_error("INVALID_CORRECTED_PRIMARY_RELATION", distractor_id)
        secondary_relations = review.get(
            "corrected_secondary_relation_types"
        )
        if not isinstance(secondary_relations, list):
            add_error(
                "EVALUATION_SCHEMA_ERROR",
                f"{distractor_id}.corrected_secondary_relation_types",
            )
        elif isinstance(secondary_relations, list):
            if len(secondary_relations) != len(set(secondary_relations)):
                add_error(
                    "DUPLICATE_CORRECTED_SECONDARY_RELATION",
                    distractor_id,
                )
            unknown_relations = set(secondary_relations).difference(
                allowed_relation_types
            )
            if unknown_relations:
                add_error(
                    "INVALID_CORRECTED_SECONDARY_RELATION",
                    dumps(sorted(unknown_relations), ensure_ascii=False),
                )
            if primary_relation in secondary_relations:
                add_error(
                    "CORRECTED_PRIMARY_REPEATED_AS_SECONDARY",
                    distractor_id,
                )
        if not isinstance(review.get("reason"), str):
            add_error(
                "EVALUATION_SCHEMA_ERROR",
                f"{distractor_id}.reason",
            )
    if len(observed_relation_ids) != len(set(observed_relation_ids)):
        add_error(
            "DUPLICATE_EVALUATION_RELATION",
            "relation review ID가 중복되었습니다.",
        )
    missing_relation_ids = expected_relation_ids.difference(
        observed_relation_ids
    )
    if missing_relation_ids:
        add_error(
            "MISSING_EVALUATION_RELATION",
            dumps(sorted(missing_relation_ids), ensure_ascii=False),
        )

    confidence = evaluation.get("confidence")
    if not isinstance(confidence, (int, float)):
        add_error("INVALID_EVALUATION_CONFIDENCE", "confidence")
    elif not 0 <= float(confidence) <= 1:
        add_error("INVALID_EVALUATION_CONFIDENCE", "confidence")
    return errors


def determine_final_verification_status(
    evaluation: dict,
    evaluation_task: dict,
    validation_errors: list[dict],
    policy: dict,
) -> tuple[str, list[str]]:
    """독립 평가와 생성 제안의 불일치를 최종 승인 상태로 변환한다."""
    evaluator_policy = policy["evaluator"]
    if validation_errors:
        return evaluator_policy["invalid_status"], [
            str(error["error_code"]) for error in validation_errors
        ]

    supported_status = evaluator_policy["supported_status"]
    contradicted_status = evaluator_policy["contradicted_status"]
    unverifiable_status = evaluator_policy["unverifiable_status"]
    reason_codes: list[str] = []
    if evaluation["input_quality_status"] in set(
        evaluator_policy["blocking_input_quality_statuses"]
    ):
        reason_codes.append("INPUT_QUALITY_REVIEW_REQUIRED")
    if evaluation["target_status"] != supported_status:
        reason_codes.append("TARGET_REVIEW_REQUIRED")
    if any(
        review["claim_status"] != supported_status
        for review in evaluation["choice_reviews"]
    ):
        reason_codes.append("CLAIM_REVIEW_REQUIRED")
    if any(
        review["relation_status"] == unverifiable_status
        for review in evaluation["relation_reviews"]
    ):
        reason_codes.append("RELATION_REVIEW_REQUIRED")

    proposal = evaluation_task["generator_proposal"]
    web_search_policy = evaluator_policy["web_search"]
    if (
        web_search_policy["require_evidence_for_final_verification"]
        and not evaluation["evidence_sources"]
    ):
        reason_codes.append("WEB_EVIDENCE_REQUIRED")

    proposal_relations = {
        relation["distractor_choice_id"]: relation
        for relation in proposal["distractor_relations"]
    }
    relation_auto_corrected = False
    for review in evaluation["relation_reviews"]:
        if review["relation_status"] == contradicted_status:
            relation_auto_corrected = True
            continue
        if review["relation_status"] != supported_status:
            continue
        proposal_relation = proposal_relations[
            review["distractor_choice_id"]
        ]
        corrected_secondary = set(
            review["corrected_secondary_relation_types"]
        )
        proposal_secondary = set(
            proposal_relation["secondary_relation_types"]
        )
        if (
            review["corrected_primary_relation_type"]
            != proposal_relation["primary_relation_type"]
            or corrected_secondary != proposal_secondary
        ):
            reason_codes.append(
                "GENERATOR_EVALUATOR_RELATION_DISAGREEMENT"
            )
            break

    minimum_confidence = float(
        evaluator_policy["minimum_final_verified_confidence"]
    )
    if float(evaluation["confidence"]) < minimum_confidence:
        reason_codes.append("EVALUATOR_CONFIDENCE_BELOW_THRESHOLD")

    reason_codes = list(dict.fromkeys(reason_codes))
    if reason_codes:
        return evaluator_policy["manual_review_status"], reason_codes
    if relation_auto_corrected:
        return evaluator_policy["auto_corrected_status"], [
            "RELATION_AUTO_CORRECTED"
        ]
    return evaluator_policy["final_verified_status"], []


def apply_evaluator_relation_corrections(
    relation_rows: pd.DataFrame,
    evaluations: list[dict],
    accepted_task_ids: set[str],
    policy: dict,
) -> pd.DataFrame:
    """승인된 관계에 평가 모델이 확정한 교정값과 근거를 반영한다."""
    if relation_rows.empty:
        return relation_rows.copy()
    final_relations = relation_rows.loc[
        relation_rows["choice_relation_task_id"].isin(
            accepted_task_ids
        )
    ].copy()
    if final_relations.empty:
        return final_relations

    reviews_by_relation_id: dict[tuple[str, str], tuple[dict, float]] = {}
    for evaluation in evaluations:
        task_id = str(evaluation["choice_relation_task_id"])
        if task_id not in accepted_task_ids:
            continue
        evaluator_confidence = float(evaluation["confidence"])
        for review in evaluation["relation_reviews"]:
            relation_key = (
                task_id,
                str(review["distractor_choice_id"]),
            )
            reviews_by_relation_id[relation_key] = (
                review,
                evaluator_confidence,
            )

    final_relations["generator_primary_relation_type"] = (
        final_relations["primary_relation_type"]
    )
    final_relations["generator_secondary_relation_types_json"] = (
        final_relations["secondary_relation_types_json"]
    )
    final_relations["generator_explanation"] = (
        final_relations["explanation"]
    )
    final_relations["evaluation_relation_status"] = ""
    final_relations["relation_resolution"] = ""
    final_relations["evaluator_confidence"] = pd.Series(
        index=final_relations.index,
        dtype="float64",
    )
    final_relations["evaluator_reason"] = ""

    contradicted_status = policy["evaluator"]["contradicted_status"]
    for row_index, relation_row in final_relations.iterrows():
        relation_key = (
            str(relation_row["choice_relation_task_id"]),
            str(relation_row["distractor_choice_id"]),
        )
        review, evaluator_confidence = reviews_by_relation_id[
            relation_key
        ]
        relation_status = str(review["relation_status"])
        relation_resolution = "GENERATOR_SUPPORTED"
        if relation_status == contradicted_status:
            relation_resolution = "EVALUATOR_CORRECTED"
        final_relations.at[
            row_index,
            "primary_relation_type",
        ] = review["corrected_primary_relation_type"]
        final_relations.at[
            row_index,
            "secondary_relation_types_json",
        ] = dumps(
            review["corrected_secondary_relation_types"],
            ensure_ascii=False,
        )
        final_relations.at[
            row_index,
            "evaluation_relation_status",
        ] = relation_status
        final_relations.at[
            row_index,
            "relation_resolution",
        ] = relation_resolution
        final_relations.at[
            row_index,
            "evaluator_confidence",
        ] = evaluator_confidence
        final_relations.at[
            row_index,
            "evaluator_reason",
        ] = review["reason"]
        final_relations.at[
            row_index,
            "explanation",
        ] = review["reason"]
    return final_relations


def validate_choice_relation_evaluations(
    evaluations: list[dict],
    evaluation_tasks: list[dict],
    policy: dict,
) -> dict[str, object]:
    """평가 결과를 검증하고 최종 상태와 검토 이유를 평탄화한다."""
    tasks_by_id = {
        task["choice_relation_evaluation_id"]: task
        for task in evaluation_tasks
    }
    observed_evaluation_ids: set[str] = set()
    summary_rows: list[dict] = []
    error_rows: list[dict] = []
    final_status_by_task_id: dict[str, str] = {}

    for raw_evaluation in evaluations:
        evaluation_id = str(
            raw_evaluation.get("choice_relation_evaluation_id") or ""
        )
        evaluation_task = tasks_by_id.get(evaluation_id)
        if evaluation_task is None:
            error_rows.append(
                {
                    "choice_relation_evaluation_id": evaluation_id,
                    "choice_relation_task_id": "",
                    "problem_id": str(
                        raw_evaluation.get("problem_id") or ""
                    ),
                    "error_code": "UNKNOWN_EVALUATION_ID",
                    "message": evaluation_id,
                }
            )
            continue
        if evaluation_id in observed_evaluation_ids:
            error_rows.append(
                {
                    "choice_relation_evaluation_id": evaluation_id,
                    "choice_relation_task_id": evaluation_task[
                        "choice_relation_task_id"
                    ],
                    "problem_id": evaluation_task["problem_id"],
                    "error_code": "DUPLICATE_EVALUATION",
                    "message": evaluation_id,
                }
            )
            continue
        observed_evaluation_ids.add(evaluation_id)

        evaluation = apply_controlled_evaluation_fields(
            raw_evaluation,
            evaluation_task,
            policy,
        )
        validation_errors = validate_choice_relation_evaluation(
            evaluation,
            evaluation_task,
            policy,
        )
        final_status, reason_codes = determine_final_verification_status(
            evaluation,
            evaluation_task,
            validation_errors,
            policy,
        )
        task_id = evaluation_task["choice_relation_task_id"]
        final_status_by_task_id[task_id] = final_status
        summary_rows.append(
            {
                "choice_relation_evaluation_id": evaluation_id,
                "choice_relation_task_id": task_id,
                "problem_id": evaluation_task["problem_id"],
                "input_quality_status": evaluation.get(
                    "input_quality_status",
                    "",
                ),
                "target_status": evaluation.get("target_status", ""),
                "confidence": evaluation.get("confidence", ""),
                "evidence_source_count": len(
                    evaluation.get("evidence_sources", [])
                ),
                "evidence_sources_json": dumps(
                    evaluation.get("evidence_sources", []),
                    ensure_ascii=False,
                ),
                "final_verification_status": final_status,
                "review_reason_codes_json": dumps(
                    reason_codes,
                    ensure_ascii=False,
                ),
                "summary": evaluation.get("summary", ""),
                "review_model": policy["evaluator_model"]["model"],
                "prompt_version": policy["evaluator"]["prompt_version"],
                "evaluator_policy_version": policy["evaluator"][
                    "policy_version"
                ],
            }
        )
        for error in validation_errors:
            error_rows.append(
                {
                    "choice_relation_evaluation_id": evaluation_id,
                    "choice_relation_task_id": task_id,
                    "problem_id": evaluation_task["problem_id"],
                    "error_code": error["error_code"],
                    "message": error["message"],
                }
            )

    return {
        "summary": pd.DataFrame(summary_rows),
        "validation_errors": pd.DataFrame(error_rows),
        "final_status_by_task_id": final_status_by_task_id,
    }


def request_choice_relation_evaluation(
    client: object,
    evaluation_task: dict,
    prompt: str,
    schema: dict,
    policy: dict,
) -> tuple[dict, dict]:
    """상위 평가 모델로 생성 제안의 역사 사실과 관계를 독립 검증한다."""
    model_policy = policy["evaluator_model"]
    executor_policy = policy["executor"]
    request_arguments: dict[str, object] = {
        "model": model_policy["model"],
        "instructions": prompt,
        "input": dumps(evaluation_task, ensure_ascii=False),
        "max_output_tokens": int(executor_policy["maximum_output_tokens"]),
        "reasoning": {"effort": model_policy["reasoning_effort"]},
        "store": bool(executor_policy["store_response"]),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "choice_relation_evaluation",
                "schema": schema,
                "strict": True,
            }
        },
    }
    if model_policy.get("send_temperature"):
        request_arguments["temperature"] = float(
            model_policy["temperature"]
        )
    service_tier = str(executor_policy.get("service_tier") or "")
    if service_tier:
        request_arguments["service_tier"] = service_tier
    web_search_policy = policy["evaluator"]["web_search"]
    if web_search_policy["enabled"]:
        request_arguments["tools"] = [web_search_policy["tool"]]
        request_arguments["tool_choice"] = web_search_policy[
            "tool_choice"
        ]
        request_arguments["include"] = web_search_policy["include"]
        maximum_tool_calls = int(
            web_search_policy["maximum_tool_calls"]
        )
        if maximum_tool_calls > 0:
            request_arguments["max_tool_calls"] = maximum_tool_calls

    response = client.responses.create(**request_arguments)
    raw_output = str(response.output_text or "").strip()
    if not raw_output:
        raise ValueError("LLM choice relation 평가 응답이 비어 있습니다.")
    parsed = loads(raw_output)
    if not isinstance(parsed, dict):
        raise ValueError("LLM choice relation 평가 결과는 JSON 객체여야 합니다.")
    parsed["evidence_sources"] = extract_web_evidence_sources(response)

    evaluation = apply_controlled_evaluation_fields(
        parsed,
        evaluation_task,
        policy,
    )
    validation_errors = validate_choice_relation_evaluation(
        evaluation,
        evaluation_task,
        policy,
    )
    if validation_errors:
        messages = [
            f"{error['error_code']}: {error['message']}"
            for error in validation_errors
        ]
        raise ValueError("; ".join(messages))

    usage: dict = {}
    if getattr(response, "usage", None) is not None:
        usage = response.usage.model_dump()
    return evaluation, {
        "response_id": str(getattr(response, "id", "")),
        "usage": usage,
    }


def load_compatible_evaluation_checkpoint(
    checkpoint_path: str,
    evaluation_tasks_by_id: dict[str, dict],
    policy: dict,
) -> dict[str, dict]:
    """같은 제안·상위 모델·평가 정책의 성공 checkpoint만 재사용한다."""
    compatible: dict[str, dict] = {}
    evaluator_policy = policy["evaluator"]
    for record in load_jsonl(checkpoint_path):
        evaluation_id = str(
            record.get("choice_relation_evaluation_id") or ""
        )
        evaluation_task = evaluation_tasks_by_id.get(evaluation_id)
        if evaluation_task is None:
            continue
        if record.get("review_model") != policy["evaluator_model"]["model"]:
            continue
        if record.get("prompt_version") != evaluator_policy[
            "prompt_version"
        ]:
            continue
        if record.get("evaluator_policy_version") != evaluator_policy[
            "policy_version"
        ]:
            continue
        if record.get("proposal_digest") != evaluation_task[
            "proposal_digest"
        ]:
            continue
        evaluation = record.get("evaluation")
        if not isinstance(evaluation, dict):
            continue
        if validate_choice_relation_evaluation(
            evaluation,
            evaluation_task,
            policy,
        ):
            continue
        compatible[evaluation_id] = record
    return compatible


def build_evaluation_execution_plan(
    evaluation_tasks: list[dict],
    checkpoint_path: str,
    policy: dict,
) -> dict[str, int]:
    """평가 API 호출 전 재사용·미처리 수를 계산한다."""
    tasks_by_id = {
        task["choice_relation_evaluation_id"]: task
        for task in evaluation_tasks
    }
    checkpoint_records = load_compatible_evaluation_checkpoint(
        checkpoint_path,
        tasks_by_id,
        policy,
    )
    return {
        "selected_task_count": len(evaluation_tasks),
        "reused_checkpoint_count": len(checkpoint_records),
        "pending_task_count": (
            len(evaluation_tasks) - len(checkpoint_records)
        ),
    }


def request_evaluation_with_retries(
    client: object,
    evaluation_task: dict,
    prompt: str,
    schema: dict,
    policy: dict,
    requester: Callable[
        [object, dict, str, dict, dict],
        tuple[dict, dict],
    ],
) -> dict[str, object]:
    """평가 한 건을 정책에 정해진 횟수만큼 재시도한다."""
    retry_count = int(policy["executor"]["maximum_retries"])
    evaluation_id = evaluation_task["choice_relation_evaluation_id"]
    last_error = ""
    attempt_count = 0
    for attempt in range(1, retry_count + 2):
        attempt_count = attempt
        try:
            evaluation, response_metadata = requester(
                client,
                evaluation_task,
                prompt,
                schema,
                policy,
            )
            return {
                "evaluation_task": evaluation_task,
                "evaluation": evaluation,
                "response_metadata": response_metadata,
                "attempt_count": attempt_count,
                "error": "",
            }
        except Exception as error:
            last_error = str(error)
            print(
                f"choice relation 평가 실패 {evaluation_id} "
                f"({attempt}/{retry_count + 1}): {last_error}"
            )
            non_retryable_markers = policy["executor"][
                "non_retryable_error_markers"
            ]
            if any(
                marker in last_error
                for marker in non_retryable_markers
            ):
                break
    return {
        "evaluation_task": evaluation_task,
        "evaluation": None,
        "response_metadata": {},
        "attempt_count": attempt_count,
        "error": last_error,
    }


def execute_choice_relation_evaluations(
    evaluation_tasks: list[dict],
    prompt: str,
    schema: dict,
    checkpoint_path: str,
    policy: dict,
    client: object,
    requester: Callable[
        [object, dict, str, dict, dict],
        tuple[dict, dict],
    ] = request_choice_relation_evaluation,
) -> dict[str, object]:
    """미평가 제안만 상위 모델로 검증하고 성공 결과를 즉시 저장한다."""
    tasks_by_id = {
        task["choice_relation_evaluation_id"]: task
        for task in evaluation_tasks
    }
    checkpoint_records = load_compatible_evaluation_checkpoint(
        checkpoint_path,
        tasks_by_id,
        policy,
    )
    initial_checkpoint_count = len(checkpoint_records)
    checkpoint = Path(checkpoint_path)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    failure_rows: list[dict] = []
    succeeded_count = 0
    pending_tasks = [
        task
        for task in evaluation_tasks
        if task["choice_relation_evaluation_id"]
        not in checkpoint_records
    ]
    attempted_count = len(pending_tasks)
    concurrent_workers = int(
        policy["evaluator"]["concurrent_workers"]
    )
    if concurrent_workers < 1:
        raise ValueError("평가 동시 실행 수는 1 이상이어야 합니다.")

    if pending_tasks:
        maximum_workers = min(concurrent_workers, len(pending_tasks))
        with ThreadPoolExecutor(
            max_workers=maximum_workers
        ) as thread_pool:
            futures = [
                thread_pool.submit(
                    request_evaluation_with_retries,
                    client,
                    evaluation_task,
                    prompt,
                    schema,
                    policy,
                    requester,
                )
                for evaluation_task in pending_tasks
            ]
            with checkpoint.open(
                "a",
                encoding="utf-8",
            ) as checkpoint_file:
                for future in as_completed(futures):
                    result = future.result()
                    evaluation_task = result["evaluation_task"]
                    evaluation_id = evaluation_task[
                        "choice_relation_evaluation_id"
                    ]
                    evaluation = result["evaluation"]
                    if evaluation is None:
                        failure_rows.append(
                            {
                                "choice_relation_evaluation_id": (
                                    evaluation_id
                                ),
                                "choice_relation_task_id": (
                                    evaluation_task[
                                        "choice_relation_task_id"
                                    ]
                                ),
                                "problem_id": evaluation_task[
                                    "problem_id"
                                ],
                                "attempt_count": result[
                                    "attempt_count"
                                ],
                                "error": result["error"],
                            }
                        )
                        continue

                    response_metadata = result["response_metadata"]
                    checkpoint_record = {
                        "choice_relation_evaluation_id": evaluation_id,
                        "choice_relation_task_id": evaluation_task[
                            "choice_relation_task_id"
                        ],
                        "problem_id": evaluation_task["problem_id"],
                        "proposal_digest": evaluation_task[
                            "proposal_digest"
                        ],
                        "review_model": policy["evaluator_model"][
                            "model"
                        ],
                        "prompt_version": policy["evaluator"][
                            "prompt_version"
                        ],
                        "evaluator_policy_version": policy["evaluator"][
                            "policy_version"
                        ],
                        "response_id": response_metadata.get(
                            "response_id",
                            "",
                        ),
                        "usage": response_metadata.get("usage", {}),
                        "completed_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        "evaluation": evaluation,
                    }
                    checkpoint_file.write(
                        dumps(
                            checkpoint_record,
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    checkpoint_file.flush()
                    checkpoint_records[
                        evaluation_id
                    ] = checkpoint_record
                    succeeded_count += 1

    ordered_evaluations = [
        checkpoint_records[
            task["choice_relation_evaluation_id"]
        ]["evaluation"]
        for task in evaluation_tasks
        if task["choice_relation_evaluation_id"] in checkpoint_records
    ]
    return {
        "selected_task_count": len(evaluation_tasks),
        "reused_checkpoint_count": initial_checkpoint_count,
        "attempted_task_count": attempted_count,
        "succeeded_task_count": succeeded_count,
        "failed_task_count": len(failure_rows),
        "completed_task_count": len(ordered_evaluations),
        "pending_task_count": (
            len(evaluation_tasks) - len(ordered_evaluations)
        ),
        "evaluations": ordered_evaluations,
        "failures": pd.DataFrame(failure_rows),
    }

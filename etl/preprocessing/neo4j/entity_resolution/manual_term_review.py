from copy import deepcopy
from datetime import datetime, timezone
from json import JSONDecodeError, dumps, loads
from pathlib import Path

import pandas as pd


def get_manual_review_columns() -> list[str]:
    """관련 엔티티 수동 검토 CSV의 고정 스키마를 반환한다."""
    return [
        "resolution_case_id",
        "canonical_term",
        "model_verification_status",
        "validation_error_codes",
        "candidate_reference_json",
        "model_decision_reason",
        "canonical_alternatives_json",
        "evidence_only_source_candidate_ids_json",
        "rejected_source_candidate_ids_json",
        "ambiguous_source_candidate_ids_json",
        "manual_status",
        "manual_reason",
        "reviewer",
        "reviewed_at",
    ]


def select_candidate_label(candidate: dict) -> str:
    """후보 원천 문맥에서 사람이 식별하기 쉬운 대표 이름을 고른다."""
    source_context = candidate.get("source_context", {})
    label_fields = [
        "headword",
        "term_name",
        "name",
        "event_name",
        "title",
    ]
    for field_name in label_fields:
        value = source_context.get(field_name)
        if value:
            return str(value)
    return str(candidate.get("matched_name") or "")


def build_candidate_reference(task: dict) -> list[dict]:
    """불투명한 candidate ID를 사람이 판독할 수 있는 안내 목록으로 만든다."""
    references: list[dict] = []
    for candidate in task.get("source_candidates", []):
        references.append(
            {
                "source_candidate_id": candidate["source_candidate_id"],
                "source": candidate.get("source", ""),
                "source_record_id": candidate.get("source_record_id", ""),
                "label": select_candidate_label(candidate),
                "entity_type": candidate.get(
                    "source_entity_type_proposal",
                    "",
                ),
                "hanja": candidate.get("hanja", []),
                "era": candidate.get("era_values", []),
            }
        )
    return references


def collect_source_ids(decision: dict, field_name: str) -> list[str]:
    """LLM 분류 항목에서 source candidate ID만 추출한다."""
    items = decision.get(field_name, [])
    if not isinstance(items, list):
        return []
    return [
        str(item.get("source_candidate_id") or "")
        for item in items
        if isinstance(item, dict) and item.get("source_candidate_id")
    ]


def load_existing_manual_rows(
    manual_review_path: str,
) -> dict[str, dict]:
    """기존 수동 판정을 case ID별로 읽고 중복·스키마 훼손을 차단한다."""
    path = Path(manual_review_path)
    if not path.is_file():
        return {}
    table = pd.read_csv(path, dtype=str).fillna("")
    required_columns = set(get_manual_review_columns())
    missing_columns = required_columns.difference(table.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(
            "관련 엔티티 수동 검토 CSV 컬럼이 없습니다: "
            f"{missing_text}"
        )
    duplicate_mask = table["resolution_case_id"].duplicated(keep=False)
    if duplicate_mask.any():
        duplicate_ids = sorted(
            set(table.loc[duplicate_mask, "resolution_case_id"])
        )
        raise ValueError(
            "관련 엔티티 수동 검토 case가 중복되었습니다: "
            f"{dumps(duplicate_ids, ensure_ascii=False)}"
        )
    return {
        str(row["resolution_case_id"]): row
        for row in table.to_dict("records")
    }


def build_manual_review_table(
    decisions: list[dict],
    tasks: list[dict],
    automatic_tables: dict[str, pd.DataFrame],
    manual_review_path: str,
    policy: dict,
) -> pd.DataFrame:
    """자동 게이트가 보류한 case만 수동 검토 CSV 행으로 구성한다."""
    manual_policy = policy["entity_resolution"][
        "related_entity_resolution"
    ]["manual_review"]
    pending_status = manual_policy["statuses"]["pending"]
    existing_by_case = load_existing_manual_rows(manual_review_path)
    decision_by_case = {
        str(decision.get("resolution_case_id") or ""): decision
        for decision in decisions
    }
    task_by_case = {
        str(task.get("resolution_case_id") or ""): task for task in tasks
    }
    summary = automatic_tables["term_resolution_decisions"]
    manual_summary = summary.loc[
        summary["verification_status"] == "NEEDS_MANUAL_REVIEW"
    ]
    error_table = automatic_tables["term_decision_validation_errors"]
    error_codes_by_case = {
        str(case_id): sorted(set(group["error_code"].astype(str)))
        for case_id, group in error_table.groupby("resolution_case_id")
    }
    editable_fields = [
        "canonical_alternatives_json",
        "evidence_only_source_candidate_ids_json",
        "rejected_source_candidate_ids_json",
        "ambiguous_source_candidate_ids_json",
        "manual_status",
        "manual_reason",
        "reviewer",
        "reviewed_at",
    ]
    rows: list[dict] = []
    for summary_row in manual_summary.to_dict("records"):
        case_id = str(summary_row["resolution_case_id"])
        decision = decision_by_case[case_id]
        task = task_by_case[case_id]
        row = {
            "resolution_case_id": case_id,
            "canonical_term": task.get("canonical_term", ""),
            "model_verification_status": summary_row[
                "verification_status"
            ],
            "validation_error_codes": dumps(
                error_codes_by_case.get(case_id, []),
                ensure_ascii=False,
            ),
            "candidate_reference_json": dumps(
                build_candidate_reference(task),
                ensure_ascii=False,
            ),
            "model_decision_reason": decision.get("decision_reason", ""),
            "canonical_alternatives_json": dumps(
                decision.get("proposed_alternatives", []),
                ensure_ascii=False,
            ),
            "evidence_only_source_candidate_ids_json": dumps(
                collect_source_ids(decision, "evidence_only_sources"),
                ensure_ascii=False,
            ),
            "rejected_source_candidate_ids_json": dumps(
                collect_source_ids(decision, "rejected_sources"),
                ensure_ascii=False,
            ),
            "ambiguous_source_candidate_ids_json": dumps(
                collect_source_ids(decision, "ambiguous_sources"),
                ensure_ascii=False,
            ),
            "manual_status": pending_status,
            "manual_reason": "",
            "reviewer": "",
            "reviewed_at": "",
        }
        existing = existing_by_case.get(case_id)
        if existing is not None:
            for field_name in editable_fields:
                row[field_name] = existing[field_name]
        rows.append(row)
    return pd.DataFrame(rows, columns=get_manual_review_columns())


def write_manual_review_table(
    table: pd.DataFrame,
    manual_review_path: str,
) -> str:
    """사용자가 편집할 수동 검토 CSV를 UTF-8 BOM 형식으로 저장한다."""
    path = Path(manual_review_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


def add_manual_error(
    errors: list[dict],
    term_decision_id: str,
    case_id: str,
    error_code: str,
    message: str,
) -> None:
    """수동 검토 입력 오류를 term 검증 오류 스키마로 기록한다."""
    errors.append(
        {
            "term_decision_id": term_decision_id,
            "resolution_case_id": case_id,
            "severity": "NEEDS_MANUAL_REVIEW",
            "error_code": error_code,
            "message": message,
        }
    )


def parse_json_list(
    raw_value: str,
    column_name: str,
    term_decision_id: str,
    case_id: str,
    errors: list[dict],
) -> list | None:
    """수동 CSV의 JSON 배열 셀을 읽고 형식 오류를 누적한다."""
    try:
        value = loads(str(raw_value or "[]"))
    except JSONDecodeError as error:
        add_manual_error(
            errors,
            term_decision_id,
            case_id,
            "INVALID_MANUAL_JSON",
            f"{column_name}: {error.msg}",
        )
        return None
    if not isinstance(value, list):
        add_manual_error(
            errors,
            term_decision_id,
            case_id,
            "MANUAL_JSON_NOT_ARRAY",
            column_name,
        )
        return None
    return value


def normalize_manual_alternatives(
    alternatives: list,
    manual_reason: str,
    term_decision_id: str,
    case_id: str,
    errors: list[dict],
) -> list[dict] | None:
    """수동 canonical 대안 JSON을 검증 가능한 decision 형태로 정규화한다."""
    normalized: list[dict] = []
    for index, alternative in enumerate(alternatives, start=1):
        if not isinstance(alternative, dict):
            add_manual_error(
                errors,
                term_decision_id,
                case_id,
                "INVALID_MANUAL_ALTERNATIVE",
                f"대안 {index}이 객체가 아닙니다.",
            )
            return None
        display_name = str(alternative.get("display_name") or "").strip()
        entity_type = str(alternative.get("entity_type") or "").strip()
        member_ids = alternative.get(
            "identity_member_source_candidate_ids",
            [],
        )
        if not display_name or not entity_type or not isinstance(
            member_ids,
            list,
        ) or not member_ids:
            add_manual_error(
                errors,
                term_decision_id,
                case_id,
                "INCOMPLETE_MANUAL_ALTERNATIVE",
                f"대안 {index}의 이름·유형·identity member를 확인하세요.",
            )
            return None
        normalized.append(
            {
                "display_name": display_name,
                "entity_type": entity_type,
                "identity_member_source_candidate_ids": [
                    str(candidate_id) for candidate_id in member_ids
                ],
                "reason": str(
                    alternative.get("reason") or manual_reason
                ),
            }
        )
    return normalized


def build_classified_source_items(
    candidate_ids: list,
    reason_by_candidate: dict[str, str],
    manual_reason: str,
) -> list[dict]:
    """수동 ID 배열을 기존 term decision의 역할 항목으로 변환한다."""
    return [
        {
            "source_candidate_id": str(candidate_id),
            "reason": reason_by_candidate.get(
                str(candidate_id),
                manual_reason,
            ),
        }
        for candidate_id in candidate_ids
    ]


def collect_original_reasons(decision: dict) -> dict[str, str]:
    """모델이 후보별로 작성한 사유를 수동 승인 결과에도 보존한다."""
    reasons: dict[str, str] = {}
    for field_name in [
        "evidence_only_sources",
        "rejected_sources",
        "ambiguous_sources",
    ]:
        items = decision.get(field_name, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("source_candidate_id") or "")
            if candidate_id:
                reasons[candidate_id] = str(item.get("reason") or "")
    return reasons


def validate_manual_classification(
    alternatives: list[dict],
    evidence_ids: list,
    rejected_ids: list,
    ambiguous_ids: list,
    expected_candidate_ids: set[str],
    term_decision_id: str,
    case_id: str,
    errors: list[dict],
) -> bool:
    """사람 판정이 모든 후보를 정확히 한 역할로 분류했는지 확인한다."""
    classified_ids: list[str] = []
    for alternative in alternatives:
        classified_ids.extend(
            alternative["identity_member_source_candidate_ids"]
        )
    classified_ids.extend(str(value) for value in evidence_ids)
    classified_ids.extend(str(value) for value in rejected_ids)
    classified_ids.extend(str(value) for value in ambiguous_ids)
    duplicate_ids = sorted(
        {
            candidate_id
            for candidate_id in classified_ids
            if classified_ids.count(candidate_id) > 1
        }
    )
    actual_ids = set(classified_ids)
    unknown_ids = sorted(actual_ids.difference(expected_candidate_ids))
    missing_ids = sorted(expected_candidate_ids.difference(actual_ids))
    if duplicate_ids:
        add_manual_error(
            errors,
            term_decision_id,
            case_id,
            "DUPLICATE_MANUAL_CLASSIFICATION",
            dumps(duplicate_ids, ensure_ascii=False),
        )
    if unknown_ids:
        add_manual_error(
            errors,
            term_decision_id,
            case_id,
            "UNKNOWN_MANUAL_CANDIDATE",
            dumps(unknown_ids, ensure_ascii=False),
        )
    if missing_ids:
        add_manual_error(
            errors,
            term_decision_id,
            case_id,
            "MISSING_MANUAL_CLASSIFICATION",
            dumps(missing_ids, ensure_ascii=False),
        )
    if ambiguous_ids:
        add_manual_error(
            errors,
            term_decision_id,
            case_id,
            "AMBIGUOUS_MANUAL_CLASSIFICATION",
            "VERIFIED 판정에는 AMBIGUOUS 후보를 남길 수 없습니다.",
        )
    return (
        not duplicate_ids
        and not unknown_ids
        and not missing_ids
        and not ambiguous_ids
    )


def prepare_manual_decisions(
    manual_table: pd.DataFrame,
    model_decisions: list[dict],
    tasks: list[dict],
    automatic_tables: dict[str, pd.DataFrame],
    policy: dict,
) -> dict[str, object]:
    """완료된 수동 행을 검증하여 모델 결정을 대체할 안전한 입력으로 만든다."""
    manual_policy = policy["entity_resolution"][
        "related_entity_resolution"
    ]["manual_review"]
    statuses = manual_policy["statuses"]
    valid_statuses = set(statuses.values())
    effective_decisions = deepcopy(model_decisions)
    decision_index_by_case = {
        str(decision.get("resolution_case_id") or ""): index
        for index, decision in enumerate(effective_decisions)
    }
    task_by_case = {
        str(task.get("resolution_case_id") or ""): task for task in tasks
    }
    summary_by_case = {
        str(row["resolution_case_id"]): row
        for row in automatic_tables["term_resolution_decisions"].to_dict(
            "records"
        )
    }
    errors: list[dict] = []
    manual_verifications: dict[str, dict] = {}
    applied_case_ids: list[str] = []

    for row_index, row in manual_table.iterrows():
        case_id = str(row["resolution_case_id"])
        summary = summary_by_case.get(case_id, {})
        term_decision_id = str(summary.get("term_decision_id") or "")
        manual_status = str(row["manual_status"] or "").strip().upper()
        if manual_status not in valid_statuses:
            add_manual_error(
                errors,
                term_decision_id,
                case_id,
                "INVALID_MANUAL_STATUS",
                manual_status,
            )
            continue
        if manual_status == statuses["pending"]:
            continue
        if summary.get("verification_status") != "NEEDS_MANUAL_REVIEW":
            add_manual_error(
                errors,
                term_decision_id,
                case_id,
                "MANUAL_OVERRIDE_NOT_REQUIRED",
                str(summary.get("verification_status") or "UNKNOWN_CASE"),
            )
            continue
        reviewer = str(row["reviewer"] or "").strip()
        manual_reason = str(row["manual_reason"] or "").strip()
        if not reviewer or not manual_reason:
            add_manual_error(
                errors,
                term_decision_id,
                case_id,
                "INCOMPLETE_MANUAL_AUDIT",
                "manual_reason과 reviewer를 모두 입력하세요.",
            )
            continue
        reviewed_at = str(row["reviewed_at"] or "").strip()
        if not reviewed_at:
            reviewed_at = datetime.now(timezone.utc).isoformat()
            manual_table.at[row_index, "reviewed_at"] = reviewed_at
        decision_index = decision_index_by_case.get(case_id)
        task = task_by_case.get(case_id)
        if decision_index is None or task is None:
            add_manual_error(
                errors,
                term_decision_id,
                case_id,
                "UNKNOWN_MANUAL_CASE",
                "현재 model decision 또는 review task에서 찾을 수 없습니다.",
            )
            continue
        original_decision = effective_decisions[decision_index]
        expected_candidate_ids = {
            str(candidate["source_candidate_id"])
            for candidate in task.get("source_candidates", [])
        }
        replacement = deepcopy(original_decision)
        if manual_status == statuses["rejected"]:
            replacement["proposed_alternatives"] = []
            replacement["evidence_only_sources"] = []
            replacement["rejected_sources"] = [
                {
                    "source_candidate_id": candidate_id,
                    "reason": manual_reason,
                }
                for candidate_id in sorted(expected_candidate_ids)
            ]
            replacement["ambiguous_sources"] = []
        elif manual_status == statuses["verified"]:
            alternatives = parse_json_list(
                row["canonical_alternatives_json"],
                "canonical_alternatives_json",
                term_decision_id,
                case_id,
                errors,
            )
            evidence_ids = parse_json_list(
                row["evidence_only_source_candidate_ids_json"],
                "evidence_only_source_candidate_ids_json",
                term_decision_id,
                case_id,
                errors,
            )
            rejected_ids = parse_json_list(
                row["rejected_source_candidate_ids_json"],
                "rejected_source_candidate_ids_json",
                term_decision_id,
                case_id,
                errors,
            )
            ambiguous_ids = parse_json_list(
                row["ambiguous_source_candidate_ids_json"],
                "ambiguous_source_candidate_ids_json",
                term_decision_id,
                case_id,
                errors,
            )
            parsed_values = [
                alternatives,
                evidence_ids,
                rejected_ids,
                ambiguous_ids,
            ]
            if any(value is None for value in parsed_values):
                continue
            normalized_alternatives = normalize_manual_alternatives(
                alternatives,
                manual_reason,
                term_decision_id,
                case_id,
                errors,
            )
            if normalized_alternatives is None:
                continue
            if not normalized_alternatives:
                add_manual_error(
                    errors,
                    term_decision_id,
                    case_id,
                    "EMPTY_VERIFIED_ALTERNATIVE",
                    "VERIFIED 판정에는 canonical 대안이 필요합니다.",
                )
                continue
            classification_valid = validate_manual_classification(
                normalized_alternatives,
                evidence_ids,
                rejected_ids,
                ambiguous_ids,
                expected_candidate_ids,
                term_decision_id,
                case_id,
                errors,
            )
            if not classification_valid:
                continue
            original_reasons = collect_original_reasons(original_decision)
            replacement["proposed_alternatives"] = normalized_alternatives
            replacement["evidence_only_sources"] = (
                build_classified_source_items(
                    evidence_ids,
                    original_reasons,
                    manual_reason,
                )
            )
            replacement["rejected_sources"] = build_classified_source_items(
                rejected_ids,
                original_reasons,
                manual_reason,
            )
            replacement["ambiguous_sources"] = []
        replacement["decision_reason"] = manual_reason
        effective_decisions[decision_index] = replacement
        manual_verifications[case_id] = {
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
        }
        applied_case_ids.append(case_id)

    error_columns = [
        "term_decision_id",
        "resolution_case_id",
        "severity",
        "error_code",
        "message",
    ]
    return {
        "decisions": effective_decisions,
        "manual_verifications": manual_verifications,
        "validation_errors": pd.DataFrame(errors, columns=error_columns),
        "applied_case_ids": applied_case_ids,
        "manual_review_table": manual_table,
    }


def write_manual_validation_errors(
    errors: pd.DataFrame,
    output_path: str,
) -> str:
    """수동 판정 입력 오류를 내부 감사 CSV로 저장한다."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    errors.to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)

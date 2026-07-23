import sys
from argparse import ArgumentParser
from json import dumps, loads
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "terms"))

from common import load_pipeline_policy
from entity_resolution.identifiers import create_stable_id
from entity_resolution.semantic_review import (
    load_jsonl,
    load_resolution_package,
    write_jsonl,
)


def build_problem_review_inputs(
    resolution_tables: dict[str, pd.DataFrame],
    term_decision_tables: dict[str, pd.DataFrame],
    policy: dict,
) -> tuple[list[dict], pd.DataFrame]:
    """검증된 term 대안으로 문항별 선택 task와 단일 대안 배정을 만든다."""
    resolution_policy = policy["entity_resolution"]
    semantic_policy = resolution_policy["semantic_review"]
    identifier_policy = resolution_policy["identifier_policy"]
    cases = resolution_tables["resolution_cases"]
    contexts = resolution_tables["problem_contexts"]
    assignments = resolution_tables["problem_resolution_assignments"]
    term_decisions = term_decision_tables["term_resolution_decisions"]
    reviewed_alternatives = term_decision_tables[
        "reviewed_canonical_alternatives"
    ]
    verified_case_ids = set(
        term_decisions[
            term_decisions["verification_status"] == "VERIFIED"
        ]["resolution_case_id"]
    )
    case_by_id = {
        str(row["resolution_case_id"]): row
        for row in cases.to_dict("records")
    }
    context_column = ""
    if "extraction_text" in contexts.columns:
        context_column = "extraction_text"
    elif "full_text" in contexts.columns:
        context_column = "full_text"
    if not context_column:
        raise ValueError("problem_contexts에 extraction_text가 없습니다.")
    context_by_problem = {
        str(row["problem_id"]): str(row[context_column])
        for row in contexts.to_dict("records")
    }
    alternatives_by_case: dict[str, list[dict]] = {}
    for row in reviewed_alternatives.to_dict("records"):
        if row["verification_status"] != "VERIFIED":
            continue
        alternatives_by_case.setdefault(row["resolution_case_id"], []).append(
            row
        )
    for alternative_rows in alternatives_by_case.values():
        alternative_rows.sort(key=lambda row: row["canonical_alternative_id"])

    tasks: list[dict] = []
    deterministic_rows: list[dict] = []
    for assignment in assignments.to_dict("records"):
        case_id = assignment["resolution_case_id"]
        if case_id not in verified_case_ids:
            continue
        alternatives = alternatives_by_case.get(case_id, [])
        alternative_items = [
            {
                "canonical_alternative_id": row["canonical_alternative_id"],
                "display_name": row["display_name_proposal"],
                "entity_type": row["entity_type_proposal"],
                "identity_member_source_record_ids": loads(
                    row["identity_member_source_ids_json"]
                ),
                "reason": row["decision_reason"],
            }
            for row in alternatives
        ]
        if len(alternatives) <= 1:
            selected_ids = [
                row["canonical_alternative_id"] for row in alternatives
            ]
            selection_mode = "NONE"
            if len(selected_ids) == 1:
                selection_mode = "SINGLE"
            deterministic_rows.append(
                {
                    "problem_assignment_id": assignment[
                        "problem_assignment_id"
                    ],
                    "problem_id": assignment["problem_id"],
                    "resolution_case_id": case_id,
                    "selected_canonical_alternative_ids_json": dumps(
                        selected_ids,
                        ensure_ascii=False,
                    ),
                    "selection_mode": selection_mode,
                    "resolution_method": "structured_rule",
                    "verification_status": "VERIFIED",
                    "problem_decision_id": "",
                    "decision_reason": "검증된 canonical 대안 수에 따른 결정적 배정",
                    "resolution_policy_version": policy["policy_version"],
                }
            )
            continue

        case = case_by_id[case_id]
        task_id = create_stable_id(
            identifier_policy["problem_review_task_prefix"],
            [
                assignment["problem_assignment_id"],
                semantic_policy["problem_prompt_version"],
            ],
            identifier_policy,
        )
        tasks.append(
            {
                "problem_review_task_id": task_id,
                "problem_assignment_id": assignment["problem_assignment_id"],
                "problem_id": assignment["problem_id"],
                "resolution_case_id": case_id,
                "canonical_term": case["canonical_term"],
                "category": case["category"],
                "problem_full_text": context_by_problem.get(
                    assignment["problem_id"],
                    "",
                ),
                "canonical_alternatives": alternative_items,
                "required_decision_status": semantic_policy[
                    "decision_status_input"
                ],
                "review_model": semantic_policy["problem_model"]["model"],
                "prompt_version": semantic_policy["problem_prompt_version"],
                "resolution_policy_version": policy["policy_version"],
            }
        )
    columns = [
        "problem_assignment_id",
        "problem_id",
        "resolution_case_id",
        "selected_canonical_alternative_ids_json",
        "selection_mode",
        "resolution_method",
        "verification_status",
        "problem_decision_id",
        "decision_reason",
        "resolution_policy_version",
    ]
    deterministic_df = pd.DataFrame(deterministic_rows, columns=columns)
    return tasks, deterministic_df


def validate_problem_decision_shape(decision: dict) -> list[str]:
    """문항 선택 결정의 핵심 JSON Schema 구조를 검사한다."""
    messages: list[str] = []
    required_strings = [
        "problem_review_task_id",
        "problem_assignment_id",
        "resolution_case_id",
        "decision_status",
        "review_model",
        "prompt_version",
        "selection_mode",
        "reason",
    ]
    for field_name in required_strings:
        if not isinstance(decision.get(field_name), str) or not decision.get(
            field_name
        ):
            messages.append(f"{field_name}: 비어 있지 않은 문자열이 필요합니다.")
    selected_ids = decision.get("selected_canonical_alternative_ids")
    if not isinstance(selected_ids, list):
        messages.append("selected_canonical_alternative_ids: 배열이 필요합니다.")
    return messages


def validate_problem_decisions(
    decisions: list[dict],
    tasks: list[dict],
    deterministic_assignments: pd.DataFrame,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """문항별 LLM 선택을 검증하고 확정 가능한 배정만 평탄화한다."""
    resolution_policy = policy["entity_resolution"]
    semantic_policy = resolution_policy["semantic_review"]
    identifier_policy = resolution_policy["identifier_policy"]
    task_by_id = {task["problem_review_task_id"]: task for task in tasks}
    decision_rows: list[dict] = []
    verified_rows = deterministic_assignments.to_dict("records")
    error_rows: list[dict] = []
    observed_task_ids: set[str] = set()
    allowed_modes = {"SINGLE", "MULTIPLE", "AMBIGUOUS", "NONE"}

    for decision_sequence, decision in enumerate(decisions, start=1):
        task_id = str(decision.get("problem_review_task_id") or "")
        assignment_id = str(decision.get("problem_assignment_id") or "")
        case_id = str(decision.get("resolution_case_id") or "")
        decision_id = create_stable_id(
            identifier_policy["problem_decision_prefix"],
            [
                task_id,
                semantic_policy["problem_prompt_version"],
                str(decision_sequence),
            ],
            identifier_policy,
        )
        invalid = False
        manual_review = False
        shape_errors = validate_problem_decision_shape(decision)
        for message in shape_errors:
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "DECISION_SCHEMA_ERROR",
                    "message": message,
                }
            )
        if shape_errors:
            invalid = True
        task = task_by_id.get(task_id)
        if task is None:
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "UNKNOWN_PROBLEM_REVIEW_TASK",
                    "message": "등록되지 않은 problem review task입니다.",
                }
            )
            invalid = True
        elif task_id in observed_task_ids:
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "DUPLICATE_PROBLEM_DECISION",
                    "message": "동일 task에 대한 결정이 중복되었습니다.",
                }
            )
            invalid = True
        elif task_id not in observed_task_ids:
            observed_task_ids.add(task_id)

        option_ids: set[str] = set()
        if task is not None:
            option_ids = {
                row["canonical_alternative_id"]
                for row in task["canonical_alternatives"]
            }
            if assignment_id != task["problem_assignment_id"] or case_id != task[
                "resolution_case_id"
            ]:
                error_rows.append(
                    {
                        "problem_decision_id": decision_id,
                        "problem_assignment_id": assignment_id,
                        "resolution_case_id": case_id,
                        "severity": "INVALID",
                        "error_code": "TASK_REFERENCE_MISMATCH",
                        "message": "task와 결정의 assignment 또는 case ID가 다릅니다.",
                    }
                )
                invalid = True
        if decision.get("decision_status") != semantic_policy[
            "decision_status_input"
        ]:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "INVALID_DECISION_STATUS",
                    "message": "LLM 입력 결정 상태는 PROPOSED여야 합니다.",
                }
            )
        if decision.get("review_model") != semantic_policy["problem_model"][
            "model"
        ]:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "REVIEW_MODEL_MISMATCH",
                    "message": "정책에 지정된 problem review model이 아닙니다.",
                }
            )
        if decision.get("prompt_version") != semantic_policy[
            "problem_prompt_version"
        ]:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "PROMPT_VERSION_MISMATCH",
                    "message": "task와 결정의 prompt version이 다릅니다.",
                }
            )

        selected_ids = decision.get("selected_canonical_alternative_ids")
        if not isinstance(selected_ids, list):
            selected_ids = []
        selected_id_set = set(selected_ids)
        if len(selected_id_set) != len(selected_ids):
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "DUPLICATE_ALTERNATIVE_SELECTION",
                    "message": "같은 canonical 대안을 중복 선택했습니다.",
                }
            )
        unknown_ids = selected_id_set.difference(option_ids)
        if unknown_ids:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "UNKNOWN_CANONICAL_ALTERNATIVE",
                    "message": dumps(sorted(unknown_ids), ensure_ascii=False),
                }
            )
        selection_mode = decision.get("selection_mode", "")
        if selection_mode not in allowed_modes:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "INVALID_SELECTION_MODE",
                    "message": selection_mode,
                }
            )
        cardinality_invalid = False
        if selection_mode == "SINGLE" and len(selected_ids) != 1:
            cardinality_invalid = True
        elif selection_mode == "MULTIPLE" and len(selected_ids) < 2:
            cardinality_invalid = True
        elif selection_mode == "NONE" and selected_ids:
            cardinality_invalid = True
        elif selection_mode == "AMBIGUOUS":
            manual_review = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "NEEDS_MANUAL_REVIEW",
                    "error_code": "AMBIGUOUS_PROBLEM_SELECTION",
                    "message": "문항 문맥으로 canonical 대안을 확정하지 못했습니다.",
                }
            )
        if cardinality_invalid:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "SELECTION_MODE_CARDINALITY_MISMATCH",
                    "message": "selection_mode과 선택한 대안 수가 맞지 않습니다.",
                }
            )
        verification_status = "VERIFIED"
        if manual_review:
            verification_status = "NEEDS_MANUAL_REVIEW"
        if invalid:
            verification_status = "INVALID"
        decision_rows.append(
            {
                "problem_decision_id": decision_id,
                "problem_review_task_id": task_id,
                "problem_assignment_id": assignment_id,
                "resolution_case_id": case_id,
                "selection_mode": selection_mode,
                "selected_canonical_alternative_ids_json": dumps(
                    selected_ids,
                    ensure_ascii=False,
                ),
                "input_decision_status": decision.get("decision_status", ""),
                "verification_status": verification_status,
                "decision_reason": decision.get("reason", ""),
                "review_model": decision.get("review_model", ""),
                "prompt_version": decision.get("prompt_version", ""),
                "resolution_policy_version": policy["policy_version"],
            }
        )
        if verification_status != "VERIFIED":
            continue
        verified_rows.append(
            {
                "problem_assignment_id": assignment_id,
                "problem_id": task["problem_id"],
                "resolution_case_id": case_id,
                "selected_canonical_alternative_ids_json": dumps(
                    selected_ids,
                    ensure_ascii=False,
                ),
                "selection_mode": selection_mode,
                "resolution_method": "llm_per_problem",
                "verification_status": "VERIFIED",
                "problem_decision_id": decision_id,
                "decision_reason": decision.get("reason", ""),
                "resolution_policy_version": policy["policy_version"],
            }
        )

    decision_columns = [
        "problem_decision_id",
        "problem_review_task_id",
        "problem_assignment_id",
        "resolution_case_id",
        "selection_mode",
        "selected_canonical_alternative_ids_json",
        "input_decision_status",
        "verification_status",
        "decision_reason",
        "review_model",
        "prompt_version",
        "resolution_policy_version",
    ]
    assignment_columns = list(deterministic_assignments.columns)
    error_columns = [
        "problem_decision_id",
        "problem_assignment_id",
        "resolution_case_id",
        "severity",
        "error_code",
        "message",
    ]
    return {
        "problem_resolution_decisions": pd.DataFrame(
            decision_rows,
            columns=decision_columns,
        ),
        "verified_problem_assignments": pd.DataFrame(
            verified_rows,
            columns=assignment_columns,
        ),
        "problem_decision_validation_errors": pd.DataFrame(
            error_rows,
            columns=error_columns,
        ),
    }


def write_problem_decision_tables(
    tables: dict[str, pd.DataFrame],
    output_dir: str,
    policy: dict,
) -> dict[str, str]:
    """문항 선택 gate 결과를 정책 파일명으로 저장한다."""
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_files = policy["entity_resolution"]["semantic_review"][
        "problem_decision_output_files"
    ]
    written: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = output_directory / output_files[table_name]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        written[table_name] = str(output_path)
    return written


def load_term_decision_tables(
    review_dir: str,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """term-level gate가 생성한 CSV 중 문항 배정에 필요한 테이블을 읽는다."""
    review_directory = Path(review_dir)
    output_files = policy["entity_resolution"]["semantic_review"][
        "term_decision_output_files"
    ]
    table_names = [
        "term_resolution_decisions",
        "reviewed_canonical_alternatives",
    ]
    tables: dict[str, pd.DataFrame] = {}
    for table_name in table_names:
        input_path = review_directory / output_files[table_name]
        if not input_path.is_file():
            raise FileNotFoundError(f"term decision CSV를 찾을 수 없습니다: {input_path}")
        tables[table_name] = pd.read_csv(input_path, dtype=str).fillna("")
    return tables


if __name__ == "__main__":
    parser = ArgumentParser(
        description="검증된 canonical 대안을 기출문항별로 선택하는 review task·gate"
    )
    parser.add_argument("resolution_dir", help="ER staging CSV 폴더")
    parser.add_argument("review_dir", help="term·problem review 출력 폴더")
    parser.add_argument(
        "--decisions",
        default="",
        help="검증할 problem_resolution_decisions.jsonl 경로",
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
    base_tables = load_resolution_package(
        cli_args.resolution_dir,
        pipeline_policy,
    )
    reviewed_term_tables = load_term_decision_tables(
        cli_args.review_dir,
        pipeline_policy,
    )
    problem_tasks, deterministic_assignments = build_problem_review_inputs(
        base_tables,
        reviewed_term_tables,
        pipeline_policy,
    )
    semantic_policy = pipeline_policy["entity_resolution"]["semantic_review"]
    task_path = Path(cli_args.review_dir) / semantic_policy[
        "problem_task_file"
    ]
    write_jsonl(problem_tasks, str(task_path))
    print(
        f"problem review task: {len(problem_tasks)}건, "
        f"deterministic assignment: {len(deterministic_assignments)}건"
    )
    proposed_decisions: list[dict] = []
    if cli_args.decisions:
        proposed_decisions = load_jsonl(cli_args.decisions)
    decision_tables = validate_problem_decisions(
        proposed_decisions,
        problem_tasks,
        deterministic_assignments,
        pipeline_policy,
    )
    paths = write_problem_decision_tables(
        decision_tables,
        cli_args.review_dir,
        pipeline_policy,
    )
    print(dumps(paths, ensure_ascii=False, indent=2))

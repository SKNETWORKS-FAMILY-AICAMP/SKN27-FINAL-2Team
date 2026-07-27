from argparse import ArgumentParser
from json import dumps
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import load_pipeline_policy
from entity_resolution.semantic_review import (
    collect_classified_sources,
    load_jsonl,
)


def get_role_conflict_review_columns() -> list[str]:
    """역할 충돌 수동 검토 CSV의 고정 컬럼 순서를 반환한다."""
    return [
        "gold_case_order",
        "gold_case_id",
        "term_review_task_id",
        "resolution_case_id",
        "canonical_term",
        "category",
        "problem_context_samples_json",
        "source_candidate_id",
        "source_record_id",
        "source",
        "matched_name",
        "matched_field",
        "retrieval_method",
        "category_compatibility",
        "source_entity_type_proposal",
        "source_context_json",
        "candidate_pair_signals_json",
        "gold_role",
        "model_role",
        "gold_reason",
        "model_reason",
        "reviewed_role",
        "review_status",
        "manual_reason",
        "reviewer",
        "reviewed_at",
    ]


def load_existing_role_conflict_rows(
    review_path: str,
    policy: dict,
) -> dict[tuple[str, str], dict[str, str]]:
    """기존 역할 충돌 검토본에서 사람 입력 컬럼만 재사용할 행을 읽는다."""
    path = Path(review_path)
    if not path.is_file():
        return {}
    review_policy = policy["entity_resolution"]["semantic_review"][
        "gold_evaluation"
    ]["role_conflict_review"]
    table = pd.read_csv(path, dtype=str).fillna("")
    required_columns = set(get_role_conflict_review_columns())
    missing_columns = required_columns.difference(table.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(
            "역할 충돌 수동 검토 CSV 컬럼이 없습니다: "
            f"{missing_text}"
        )
    key_fields = review_policy["key_fields"]
    duplicate_mask = table.duplicated(subset=key_fields, keep=False)
    if duplicate_mask.any():
        duplicate_rows = table.loc[duplicate_mask, key_fields].to_dict(
            "records"
        )
        raise ValueError(
            "역할 충돌 수동 검토 행이 중복되었습니다: "
            + dumps(duplicate_rows, ensure_ascii=False)
        )
    return {
        (
            str(row[key_fields[0]]),
            str(row[key_fields[1]]),
        ): row
        for row in table.to_dict("records")
    }


def build_role_conflict_review_table(
    gold_decisions: list[dict],
    predicted_decisions: list[dict],
    tasks: list[dict],
    existing_review_path: str,
    policy: dict,
) -> pd.DataFrame:
    """EVIDENCE_ONLY·REJECTED가 엇갈린 후보를 사람 재검토 행으로 만든다."""
    review_policy = policy["entity_resolution"]["semantic_review"][
        "gold_evaluation"
    ]["role_conflict_review"]
    compared_roles = set(review_policy["compared_roles"])
    pending_status = review_policy["pending_status"]
    editable_fields = review_policy["editable_fields"]
    key_fields = review_policy["key_fields"]
    existing_by_key = load_existing_role_conflict_rows(
        existing_review_path,
        policy,
    )
    gold_by_task = {
        str(decision["term_review_task_id"]): decision
        for decision in gold_decisions
    }
    predicted_by_task = {
        str(decision["term_review_task_id"]): decision
        for decision in predicted_decisions
    }
    rows: list[dict] = []
    for task in tasks:
        task_id = str(task["term_review_task_id"])
        gold_decision = gold_by_task.get(task_id)
        predicted_decision = predicted_by_task.get(task_id)
        if gold_decision is None or predicted_decision is None:
            continue
        gold_roles, gold_duplicate_ids = collect_classified_sources(
            gold_decision
        )
        predicted_roles, predicted_duplicate_ids = (
            collect_classified_sources(predicted_decision)
        )
        if gold_duplicate_ids or predicted_duplicate_ids:
            continue
        metadata = task.get("gold_set_metadata", {})
        pair_signals = task.get("relevant_pair_signals", [])
        for candidate in task.get("source_candidates", []):
            candidate_id = str(candidate["source_candidate_id"])
            gold_classification = gold_roles.get(candidate_id)
            predicted_classification = predicted_roles.get(candidate_id)
            if (
                gold_classification is None
                or predicted_classification is None
            ):
                continue
            gold_role, _, gold_reason = gold_classification
            model_role, _, model_reason = predicted_classification
            if gold_role == model_role:
                continue
            if {gold_role, model_role} != compared_roles:
                continue
            candidate_signals = [
                signal
                for signal in pair_signals
                if candidate_id
                in {
                    str(signal["left_source_candidate_id"]),
                    str(signal["right_source_candidate_id"]),
                }
            ]
            row = {
                "gold_case_order": metadata.get("gold_case_order", ""),
                "gold_case_id": metadata.get("gold_case_id", ""),
                "term_review_task_id": task_id,
                "resolution_case_id": task.get(
                    "resolution_case_id",
                    "",
                ),
                "canonical_term": task.get("canonical_term", ""),
                "category": task.get("category", ""),
                "problem_context_samples_json": dumps(
                    task.get("problem_context_samples", []),
                    ensure_ascii=False,
                ),
                "source_candidate_id": candidate_id,
                "source_record_id": candidate.get(
                    "source_record_id",
                    "",
                ),
                "source": candidate.get("source", ""),
                "matched_name": candidate.get("matched_name", ""),
                "matched_field": candidate.get("matched_field", ""),
                "retrieval_method": candidate.get(
                    "retrieval_method",
                    "",
                ),
                "category_compatibility": candidate.get(
                    "category_compatibility",
                    "",
                ),
                "source_entity_type_proposal": candidate.get(
                    "source_entity_type_proposal",
                    "",
                ),
                "source_context_json": dumps(
                    candidate.get("source_context", {}),
                    ensure_ascii=False,
                ),
                "candidate_pair_signals_json": dumps(
                    candidate_signals,
                    ensure_ascii=False,
                ),
                "gold_role": gold_role,
                "model_role": model_role,
                "gold_reason": gold_reason,
                "model_reason": model_reason,
                "reviewed_role": "",
                "review_status": pending_status,
                "manual_reason": "",
                "reviewer": "",
                "reviewed_at": "",
            }
            existing_key = (
                str(row[key_fields[0]]),
                str(row[key_fields[1]]),
            )
            existing = existing_by_key.get(existing_key)
            if existing is not None:
                for field_name in editable_fields:
                    row[field_name] = existing[field_name]
            rows.append(row)
    return pd.DataFrame(
        rows,
        columns=get_role_conflict_review_columns(),
    )


def write_role_conflict_review_table(
    table: pd.DataFrame,
    review_path: str,
) -> str:
    """역할 충돌 수동 검토 CSV를 UTF-8 BOM으로 저장한다."""
    path = Path(review_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


if __name__ == "__main__":
    neo4j_root = Path(__file__).resolve().parent.parent
    parser = ArgumentParser(
        description="gold와 모델의 EVIDENCE_ONLY·REJECTED 충돌 검토표 생성"
    )
    parser.add_argument(
        "--gold-decisions",
        default=str(
            neo4j_root
            / "goldset"
            / "internal"
            / "evaluation"
            / "human_gold_decisions.jsonl"
        ),
    )
    parser.add_argument(
        "--predicted-decisions",
        default=str(
            neo4j_root
            / "goldset"
            / "internal"
            / "model"
            / "term_identity_model_decisions.jsonl"
        ),
    )
    parser.add_argument(
        "--tasks",
        default=str(
            neo4j_root
            / "goldset"
            / "internal"
            / "source"
            / "gold_review_tasks.jsonl"
        ),
    )
    parser.add_argument(
        "--output",
        default="",
    )
    parser.add_argument(
        "--policy",
        default=str(neo4j_root / "config" / "resolution_policy.json"),
    )
    cli_args = parser.parse_args()
    pipeline_policy = load_pipeline_policy(cli_args.policy)
    output_path = cli_args.output
    if not output_path:
        configured_output = pipeline_policy["entity_resolution"][
            "semantic_review"
        ]["gold_set"]["workflow"]["role_conflict_manual_review"]
        output_path = str((neo4j_root / configured_output).resolve())
    review_table = build_role_conflict_review_table(
        load_jsonl(cli_args.gold_decisions),
        load_jsonl(cli_args.predicted_decisions),
        load_jsonl(cli_args.tasks),
        output_path,
        pipeline_policy,
    )
    written_path = write_role_conflict_review_table(
        review_table,
        output_path,
    )
    print(
        dumps(
            {
                "review_row_count": len(review_table),
                "output": written_path,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

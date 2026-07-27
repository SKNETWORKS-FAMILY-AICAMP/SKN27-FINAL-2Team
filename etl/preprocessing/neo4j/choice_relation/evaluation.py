from __future__ import annotations

from json import loads
from pathlib import Path

import pandas as pd


def load_relation_goldset(goldset_path: str, policy: dict) -> pd.DataFrame:
    """초기 관계 기준표를 읽고 키·관계 유형·검수 상태를 검사한다."""
    path = Path(goldset_path)
    if not path.is_file():
        raise FileNotFoundError(f"choice relation goldset이 없습니다: {path}")
    goldset = pd.read_csv(path, encoding="utf-8")
    required_columns = {
        "problem_id",
        "answer_choice_id",
        "distractor_choice_id",
        "gold_primary_relation_type",
        "gold_secondary_relation_types_json",
        "gold_reason",
        "review_status",
    }
    missing_columns = required_columns.difference(goldset.columns)
    if missing_columns:
        raise ValueError(
            "choice relation goldset 필수 열이 없습니다: "
            + ", ".join(sorted(missing_columns))
        )

    key_columns = ["problem_id", "distractor_choice_id"]
    duplicate_rows = goldset[goldset.duplicated(key_columns, keep=False)]
    if not duplicate_rows.empty:
        raise ValueError("choice relation goldset 복합 키가 중복됩니다.")

    allowed_relation_types = set(
        policy["allowed_values"]["primary_relation_type"]
    )
    unknown_primary = set(
        goldset["gold_primary_relation_type"]
    ).difference(allowed_relation_types)
    if unknown_primary:
        raise ValueError(
            "goldset에 허용되지 않은 주 관계가 있습니다: "
            + ", ".join(sorted(unknown_primary))
        )

    parsed_secondary: list[list[str]] = []
    for row_index, value in enumerate(
        goldset["gold_secondary_relation_types_json"],
        start=2,
    ):
        relation_types = loads(str(value))
        if not isinstance(relation_types, list):
            raise ValueError(f"goldset {row_index}행 보조 관계는 배열이어야 합니다.")
        if len(relation_types) != len(set(relation_types)):
            raise ValueError(f"goldset {row_index}행 보조 관계가 중복됩니다.")
        unknown_secondary = set(relation_types).difference(
            allowed_relation_types
        )
        if unknown_secondary:
            raise ValueError(
                f"goldset {row_index}행에 허용되지 않은 보조 관계가 있습니다."
            )
        primary_relation = str(
            goldset.iloc[row_index - 2]["gold_primary_relation_type"]
        )
        if primary_relation in relation_types:
            raise ValueError(
                f"goldset {row_index}행 주 관계가 보조 관계에 중복됩니다."
            )
        parsed_secondary.append(relation_types)
    validated = goldset.copy()
    validated["gold_secondary_relation_types"] = parsed_secondary
    return validated


def evaluate_relation_predictions(
    predicted_relations: pd.DataFrame,
    goldset: pd.DataFrame,
    policy: dict,
) -> dict[str, object]:
    """예측 관계의 coverage·주 관계 정확도·관계 집합 micro F1을 계산한다."""
    required_prediction_columns = {
        "problem_id",
        "answer_choice_id",
        "distractor_choice_id",
        "primary_relation_type",
        "secondary_relation_types_json",
    }
    missing_columns = required_prediction_columns.difference(
        predicted_relations.columns
    )
    if missing_columns:
        raise ValueError(
            "예측 관계 필수 열이 없습니다: "
            + ", ".join(sorted(missing_columns))
        )

    key_columns = ["problem_id", "distractor_choice_id"]
    duplicated = predicted_relations[
        predicted_relations.duplicated(key_columns, keep=False)
    ]
    if not duplicated.empty:
        raise ValueError("예측 정답–오답 관계 복합 키가 중복됩니다.")

    prediction_by_key = {
        (str(row["problem_id"]), str(row["distractor_choice_id"])): row
        for row in predicted_relations.to_dict("records")
    }
    comparison_rows: list[dict] = []
    matched_count = 0
    primary_correct_count = 0
    relation_set_exact_count = 0
    true_positive_count = 0
    false_positive_count = 0
    false_negative_count = 0

    for gold_row in goldset.to_dict("records"):
        key = (
            str(gold_row["problem_id"]),
            str(gold_row["distractor_choice_id"]),
        )
        prediction = prediction_by_key.get(key)
        predicted_primary = ""
        predicted_secondary: list[str] = []
        if prediction is not None:
            matched_count += 1
            predicted_primary = str(prediction["primary_relation_type"])
            predicted_secondary_value = loads(
                str(prediction["secondary_relation_types_json"])
            )
            if not isinstance(predicted_secondary_value, list):
                raise ValueError("예측 보조 관계 JSON은 배열이어야 합니다.")
            predicted_secondary = [
                str(value) for value in predicted_secondary_value
            ]

        gold_primary = str(gold_row["gold_primary_relation_type"])
        gold_secondary = list(
            gold_row["gold_secondary_relation_types"]
        )
        primary_correct = predicted_primary == gold_primary
        gold_relation_set = {gold_primary, *gold_secondary}
        predicted_relation_set = {
            value
            for value in [predicted_primary, *predicted_secondary]
            if value
        }
        relation_set_exact = (
            predicted_relation_set == gold_relation_set
        )
        if primary_correct:
            primary_correct_count += 1
        if relation_set_exact:
            relation_set_exact_count += 1
        true_positive_count += len(
            predicted_relation_set.intersection(gold_relation_set)
        )
        false_positive_count += len(
            predicted_relation_set.difference(gold_relation_set)
        )
        false_negative_count += len(
            gold_relation_set.difference(predicted_relation_set)
        )
        predicted_secondary_json = "[]"
        if prediction is not None:
            predicted_secondary_json = str(
                prediction["secondary_relation_types_json"]
            )
        comparison_rows.append(
            {
                "problem_id": gold_row["problem_id"],
                "answer_choice_id": gold_row["answer_choice_id"],
                "distractor_choice_id": gold_row["distractor_choice_id"],
                "gold_primary_relation_type": gold_primary,
                "predicted_primary_relation_type": predicted_primary,
                "primary_correct": primary_correct,
                "gold_secondary_relation_types_json": gold_row[
                    "gold_secondary_relation_types_json"
                ],
                "predicted_secondary_relation_types_json": (
                    predicted_secondary_json
                ),
                "relation_set_exact": relation_set_exact,
                "review_status": gold_row["review_status"],
            }
        )

    gold_pair_count = len(goldset)
    precision_denominator = (
        true_positive_count + false_positive_count
    )
    recall_denominator = true_positive_count + false_negative_count
    micro_precision = 0.0
    if precision_denominator > 0:
        micro_precision = true_positive_count / precision_denominator
    micro_recall = 0.0
    if recall_denominator > 0:
        micro_recall = true_positive_count / recall_denominator
    micro_f1 = 0.0
    if micro_precision + micro_recall > 0:
        micro_f1 = (
            2
            * micro_precision
            * micro_recall
            / (micro_precision + micro_recall)
        )

    reviewed_status = policy["goldset"]["reviewed_status"]
    official_evaluation_available = bool(gold_pair_count) and all(
        str(status) == reviewed_status
        for status in goldset["review_status"]
    )
    prediction_coverage = 0.0
    primary_accuracy_overall = 0.0
    relation_set_exact_rate_overall = 0.0
    if gold_pair_count:
        prediction_coverage = matched_count / gold_pair_count
        primary_accuracy_overall = (
            primary_correct_count / gold_pair_count
        )
        relation_set_exact_rate_overall = (
            relation_set_exact_count / gold_pair_count
        )
    primary_accuracy_on_matched = 0.0
    if matched_count:
        primary_accuracy_on_matched = (
            primary_correct_count / matched_count
        )
    metrics = {
        "gold_pair_count": gold_pair_count,
        "predicted_pair_count": len(predicted_relations),
        "matched_pair_count": matched_count,
        "prediction_coverage": prediction_coverage,
        "primary_accuracy_overall": primary_accuracy_overall,
        "primary_accuracy_on_matched": primary_accuracy_on_matched,
        "relation_set_exact_rate_overall": (
            relation_set_exact_rate_overall
        ),
        "relation_label_micro_precision": micro_precision,
        "relation_label_micro_recall": micro_recall,
        "relation_label_micro_f1": micro_f1,
        "relation_label_true_positive_count": true_positive_count,
        "relation_label_false_positive_count": false_positive_count,
        "relation_label_false_negative_count": false_negative_count,
        "official_evaluation_available": official_evaluation_available,
    }
    return {
        "metrics": metrics,
        "comparison": pd.DataFrame(comparison_rows),
    }

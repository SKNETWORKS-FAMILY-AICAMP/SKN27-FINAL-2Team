from __future__ import annotations

from pathlib import Path

import pandas as pd


RESULT_ROOT = Path(
    r"C:\Users\Playdata\Downloads\eval_fixed_walk_forward_v2\eval_fixed_walk_forward_v2"
)
REPORT_DIR = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\reports")


def to_md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(empty)"
    columns = list(df.columns)
    rows = []
    rows.append("| " + " | ".join(columns) + " |")
    rows.append("| " + " | ".join(["---"] * len(columns)) + " |")
    for _, row in df.iterrows():
        values = [str(row[column]) for column in columns]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def main() -> None:
    prediction_path = (
        RESULT_ROOT
        / "walk_forward_47_prev_to_next"
        / "round_73"
        / "walk_forward_round_73_combined_predictions.csv"
    )
    df = pd.read_csv(prediction_path)
    wrong = df[~df["is_correct_era_topic_train"]].copy()

    detail_columns = [
        "question_no",
        "problem_id",
        "true_era",
        "pred_era",
        "true_topic_train",
        "pred_topic_train",
        "true_topic",
        "pred_topic",
        "is_correct_era",
        "is_correct_topic_train",
        "is_correct_topic",
        "text",
    ]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    wrong[detail_columns].to_csv(
        REPORT_DIR / "round73_wrong_combo_details.csv",
        index=False,
        encoding="utf-8-sig",
    )

    rates = df[
        [
            "is_correct_era",
            "is_correct_topic_train",
            "is_correct_topic",
            "is_correct_era_topic_train",
            "is_correct_all_three",
        ]
    ].mean()

    topic_conf = (
        df[df["true_topic_train"] != df["pred_topic_train"]]
        .groupby(["true_topic_train", "pred_topic_train"])
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    era_conf = (
        df[df["true_era"] != df["pred_era"]]
        .groupby(["true_era", "pred_era"])
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )
    wrong_simple = wrong[
        [
            "question_no",
            "problem_id",
            "true_era",
            "pred_era",
            "true_topic_train",
            "pred_topic_train",
            "true_topic",
            "pred_topic",
            "is_correct_era",
            "is_correct_topic_train",
            "is_correct_topic",
        ]
    ].copy()

    md = [
        "# 73회차 Walk-Forward 오답 분석 - 2026-07-16",
        "",
        "## 요약",
        "",
        f"- 전체 문항: {len(df)}개",
        f"- `era + topic_train` 조합 오답: {len(wrong)}개",
        f"- `era + topic_train` 조합 정확도: {rates['is_correct_era_topic_train']:.2f}",
        f"- `era` 정확도: {rates['is_correct_era']:.2f}",
        f"- `topic_train` 정확도: {rates['is_correct_topic_train']:.2f}",
        f"- `topic` 정확도: {rates['is_correct_topic']:.2f}",
        "",
        "## Topic_train 오분류 조합",
        "",
        to_md_table(topic_conf),
        "",
        "## Era 오분류 조합",
        "",
        to_md_table(era_conf) if not era_conf.empty else "era 오분류 없음",
        "",
        "## era + topic_train 조합 오답 문항",
        "",
        to_md_table(wrong_simple),
        "",
        "## 해석",
        "",
        "- 73회차의 조합 오답 대부분은 시대보다 `topic_train` 오분류에서 발생했다.",
        "- `era`는 50문항 중 1개만 틀렸으므로, 73회차 성능 저하의 핵심 원인은 시대 분류가 아니라 통합 주제 분류다.",
        "- 최종 트렌드 예측에서는 `era`보다 `topic_train` 오분류 개선이 우선이다.",
    ]

    report_path = REPORT_DIR / "ML_round73_error_analysis_2026-07-16.md"
    report_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(report_path)
    print(REPORT_DIR / "round73_wrong_combo_details.csv")


if __name__ == "__main__":
    main()

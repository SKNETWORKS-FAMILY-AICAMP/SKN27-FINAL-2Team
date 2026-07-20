from __future__ import annotations

from pathlib import Path

import pandas as pd


FEATURE_CSV = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\output\split_v2\full_features_v2.csv")
WALK_PRED_CSV = Path(
    r"C:\Users\Playdata\Downloads\eval_fixed_walk_forward_v2\eval_fixed_walk_forward_v2\walk_forward_all_rounds_combined_predictions.csv"
)
REPORT_DIR = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\reports")

SUMMARY_CSV = REPORT_DIR / "최근5회차_TOP5_트렌드_topic포함_비교_요약.csv"
DETAIL_CSV = REPORT_DIR / "최근5회차_TOP5_트렌드_topic포함_비교_상세.csv"
REPORT_MD = REPORT_DIR / "최근5회차_TOP5_트렌드_topic포함_분석_2026-07-18.md"


def combo_counts(
    df: pd.DataFrame,
    combo_column: str,
    topic_column: str,
    top_n: int = 5,
    topic_top_n: int = 3,
) -> pd.DataFrame:
    counts = df[combo_column].astype(str).value_counts().head(top_n).rename_axis("label").reset_index(name="count")
    source_total = len(df)
    counts["ratio"] = counts["count"] / source_total if source_total else 0.0

    topic_summaries = []
    for label in counts["label"]:
        matched = df[df[combo_column].astype(str) == label]
        topic_counts = matched[topic_column].astype(str).value_counts().head(topic_top_n)
        topic_summaries.append(
            ", ".join(f"{topic} {int(count)}" for topic, count in topic_counts.items())
        )
    counts["topic_top"] = topic_summaries
    counts["label_with_topic"] = counts.apply(
        lambda row: f"{row['label']} ({row['topic_top']})" if row["topic_top"] else str(row["label"]),
        axis=1,
    )
    return counts


def label_set(df: pd.DataFrame) -> set[str]:
    return set(df["label"].astype(str).tolist())


def format_top(df: pd.DataFrame) -> str:
    if df.empty:
        return "-"
    return "<br>".join(
        f"{idx + 1}. {row.label_with_topic} ({int(row['count'])}, {row.ratio:.1%})"
        for idx, row in df.reset_index(drop=True).iterrows()
    )


def to_md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(empty)"
    headers = list(df.columns)
    rows = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for _, row in df.iterrows():
        values = []
        for column in headers:
            value = row[column]
            if isinstance(value, float):
                values.append(f"{value:.4f}")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    features = pd.read_csv(FEATURE_CSV)
    walk = pd.read_csv(WALK_PRED_CSV)

    features["round_no"] = pd.to_numeric(features["round_no"], errors="raise").astype(int)
    walk["round_no"] = pd.to_numeric(walk["round_no"], errors="raise").astype(int)

    features["true_era_topic_train"] = features["era"].astype(str) + " + " + features["topic_train"].astype(str)

    summary_rows = []
    detail_rows = []

    for round_no in range(71, 79):
        prev_start = round_no - 5
        prev_end = round_no - 1
        prev_df = features[(features["round_no"] >= prev_start) & (features["round_no"] <= prev_end)].copy()
        target_df = walk[walk["round_no"] == round_no].copy()

        recent5_top = combo_counts(prev_df, "true_era_topic_train", "topic")
        predicted_top = combo_counts(target_df, "pred_era_topic_train", "pred_topic")
        actual_top = combo_counts(target_df, "true_era_topic_train", "true_topic")

        pred_actual_overlap = len(label_set(predicted_top) & label_set(actual_top))
        recent5_actual_overlap = len(label_set(recent5_top) & label_set(actual_top))
        pred_recent5_overlap = len(label_set(predicted_top) & label_set(recent5_top))

        summary_rows.append(
            {
                "round_no": round_no,
                "recent5_rounds": f"{prev_start}~{prev_end}",
                "recent5_actual_top5": format_top(recent5_top),
                "predicted_top5": format_top(predicted_top),
                "actual_top5": format_top(actual_top),
                "pred_actual_top5_overlap": pred_actual_overlap,
                "recent5_actual_top5_overlap": recent5_actual_overlap,
                "pred_recent5_top5_overlap": pred_recent5_overlap,
                "test_size": len(target_df),
            }
        )

        for source_name, top_df in [
            ("recent5_actual", recent5_top),
            ("predicted", predicted_top),
            ("actual", actual_top),
        ]:
            for rank, row in enumerate(top_df.itertuples(index=False), start=1):
                detail_rows.append(
                    {
                        "round_no": round_no,
                        "recent5_rounds": f"{prev_start}~{prev_end}",
                        "source": source_name,
                        "rank": rank,
                        "label": row.label,
                        "topic_top": row.topic_top,
                        "label_with_topic": row.label_with_topic,
                        "count": int(row.count),
                        "ratio": float(row.ratio),
                    }
                )

    summary_df = pd.DataFrame(summary_rows)
    detail_df = pd.DataFrame(detail_rows)

    summary_df.to_csv(SUMMARY_CSV, index=False, encoding="utf-8-sig")
    detail_df.to_csv(DETAIL_CSV, index=False, encoding="utf-8-sig")

    compact_rows = [
        {
            "회차": row["round_no"],
            "최근 5회차": row["recent5_rounds"],
            "모델분류 TOP5 vs 실제 TOP5 겹침": row["pred_actual_top5_overlap"],
            "최근5 실제 TOP5 vs 실제 TOP5 겹침": row["recent5_actual_top5_overlap"],
        }
        for row in summary_rows
    ]
    compact_df = pd.DataFrame(compact_rows)

    md_parts = [
        "# 최근 5회차 TOP5 트렌드 분석(topic 포함) - 2026-07-18",
        "",
        "## 목적",
        "",
        "기존 `era + topic_train` TOP5만 보면 통합 주제 수준에서만 확인할 수 있다.",
        "이 문서는 각 TOP5 조합 옆에 해당 조합 내부의 세부 `topic` 상위 분포를 함께 표시한다.",
        "",
        "예: `일제 강점기 + 사건 (독립운동 3, 정치 사건 1)`",
        "",
        "## 구분",
        "",
        "- 최근 5회차 실제 TOP5: 직전 5회차 실제 라벨 기준 통계",
        "- 모델분류 TOP5: 해당 회차 text를 모델로 분류한 결과 기준 통계",
        "- 해당 회차 실제 TOP5: 해당 회차 실제 라벨 기준 통계",
        "",
        "## 요약",
        "",
        to_md_table(compact_df),
        "",
        "## 회차별 상세",
        "",
    ]

    for row in summary_rows:
        md_parts.extend(
            [
                f"### {row['round_no']}회차",
                "",
                f"- 최근 5회차 기준: {row['recent5_rounds']}회차",
                f"- 모델분류 TOP5와 실제 TOP5 겹침: {row['pred_actual_top5_overlap']}개",
                f"- 최근5 실제 TOP5와 실제 TOP5 겹침: {row['recent5_actual_top5_overlap']}개",
                "",
                "| 구분 | TOP5 |",
                "| --- | --- |",
                f"| 최근 5회차 실제 TOP5 | {row['recent5_actual_top5']} |",
                f"| 모델분류 TOP5 | {row['predicted_top5']} |",
                f"| 해당 회차 실제 TOP5 | {row['actual_top5']} |",
                "",
            ]
        )

    md_parts.extend(
        [
            "## 생성 파일",
            "",
            f"- `{SUMMARY_CSV.name}`",
            f"- `{DETAIL_CSV.name}`",
        ]
    )

    REPORT_MD.write_text("\n".join(md_parts) + "\n", encoding="utf-8")

    print(REPORT_MD)
    print(SUMMARY_CSV)
    print(DETAIL_CSV)


if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path

import pandas as pd


FEATURE_CSV = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\output\split_v2\full_features_v2.csv")
WALK_PRED_CSV = Path(
    r"C:\Users\Playdata\Downloads\eval_fixed_walk_forward_v2\eval_fixed_walk_forward_v2\walk_forward_all_rounds_combined_predictions.csv"
)
REPORT_DIR = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\reports")


def top_counts(df: pd.DataFrame, column: str, top_n: int = 5) -> pd.DataFrame:
    result = df[column].value_counts().head(top_n).rename_axis("label").reset_index(name="count")
    total = int(result["count"].sum()) if len(result) else 0
    source_total = len(df)
    result["ratio"] = result["count"] / source_total if source_total else 0.0
    return result


def format_top(df: pd.DataFrame) -> str:
    if df.empty:
        return "-"
    return "<br>".join(
        f"{idx + 1}. {row.label} ({int(row['count'])}, {row.ratio:.1%})"
        for idx, row in df.reset_index(drop=True).iterrows()
    )


def label_set(df: pd.DataFrame) -> set[str]:
    return set(df["label"].astype(str).tolist())


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
        target_pred_df = walk[walk["round_no"] == round_no].copy()

        prev_top = top_counts(prev_df, "true_era_topic_train")
        pred_top = top_counts(target_pred_df, "pred_era_topic_train")
        actual_top = top_counts(target_pred_df, "true_era_topic_train")

        pred_actual_overlap = len(label_set(pred_top) & label_set(actual_top))
        prev_actual_overlap = len(label_set(prev_top) & label_set(actual_top))
        pred_prev_overlap = len(label_set(pred_top) & label_set(prev_top))

        summary_rows.append(
            {
                "round_no": round_no,
                "recent5_rounds": f"{prev_start}~{prev_end}",
                "recent5_actual_top5": format_top(prev_top),
                "predicted_top5": format_top(pred_top),
                "actual_top5": format_top(actual_top),
                "pred_actual_top5_overlap": pred_actual_overlap,
                "recent5_actual_top5_overlap": prev_actual_overlap,
                "pred_recent5_top5_overlap": pred_prev_overlap,
                "test_size": len(target_pred_df),
            }
        )

        for source_name, top_df in [
            ("recent5_actual", prev_top),
            ("predicted", pred_top),
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
                        "count": int(row.count),
                        "ratio": float(row.ratio),
                    }
                )

    summary_df = pd.DataFrame(summary_rows)
    detail_df = pd.DataFrame(detail_rows)

    summary_csv = REPORT_DIR / "최근5회차_TOP5_트렌드_비교_요약.csv"
    detail_csv = REPORT_DIR / "최근5회차_TOP5_트렌드_비교_상세.csv"
    report_md = REPORT_DIR / "최근5회차_TOP5_트렌드_분석_2026-07-16.md"

    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    detail_df.to_csv(detail_csv, index=False, encoding="utf-8-sig")

    compact_rows = []
    for row in summary_rows:
        compact_rows.append(
            {
                "예측 회차": row["round_no"],
                "최근 5회차": row["recent5_rounds"],
                "예측 TOP5 vs 실제 TOP5 겹침": row["pred_actual_top5_overlap"],
                "최근5 TOP5 vs 실제 TOP5 겹침": row["recent5_actual_top5_overlap"],
                "예측 TOP5 vs 최근5 TOP5 겹침": row["pred_recent5_top5_overlap"],
            }
        )
    compact_df = pd.DataFrame(compact_rows)

    md_parts = [
        "# 최근 5회차 TOP5 트렌드 분석 - 2026-07-16",
        "",
        "## 목적",
        "",
        "현재 walk-forward 평가는 이미 `47~70 -> 71`, `47~71 -> 72` 방식으로 누적 학습 후 다음 회차를 예측하고 있다.",
        "",
        "이 문서는 추가 학습 없이 기존 walk-forward 예측 결과를 사용해서 다음 3가지를 비교한다.",
        "",
        "1. 예측 직전 최근 5회차의 실제 `era + topic_train` TOP5",
        "2. 모델이 예측한 해당 회차 `era + topic_train` TOP5",
        "3. 해당 회차의 실제 `era + topic_train` TOP5",
        "",
        "즉, 모델이 단순히 최근 5회차 빈도만 따라가는지, 실제 해당 회차 분포에 가까운지 확인하기 위한 분석이다.",
        "",
        "## 요약 지표",
        "",
        to_md_table(compact_df),
        "",
        "## 해석",
        "",
        "- 모델 학습은 이미 walk-forward에서 누적 방식으로 끝났으므로, 이 분석에는 추가 학습이 필요 없다.",
        "- 최근 5회차 TOP5는 학습 데이터가 아니라 트렌드 해석 기준이다.",
        "- `예측 TOP5 vs 실제 TOP5 겹침`이 높으면 모델 예측 분포가 해당 회차 실제 분포와 비슷하다는 뜻이다.",
        "- `최근5 TOP5 vs 실제 TOP5 겹침`과 비교하면 모델이 단순 최근 빈도보다 더 나은지 확인할 수 있다.",
        "",
        "## 회차별 상세",
        "",
    ]

    for row in summary_rows:
        md_parts.extend(
            [
                f"### {row['round_no']}회차 예측",
                "",
                f"- 최근 5회차 기준: {row['recent5_rounds']}회차",
                f"- 예측 TOP5와 실제 TOP5 겹침: {row['pred_actual_top5_overlap']}개",
                f"- 최근5 TOP5와 실제 TOP5 겹침: {row['recent5_actual_top5_overlap']}개",
                "",
                "| 구분 | TOP5 |",
                "| --- | --- |",
                f"| 최근 5회차 실제 TOP5 | {row['recent5_actual_top5']} |",
                f"| 모델 예측 TOP5 | {row['predicted_top5']} |",
                f"| 해당 회차 실제 TOP5 | {row['actual_top5']} |",
                "",
            ]
        )

    md_parts.extend(
        [
            "## 생성 파일",
            "",
            f"- `{summary_csv.name}`",
            f"- `{detail_csv.name}`",
        ]
    )

    report_md.write_text("\n".join(md_parts) + "\n", encoding="utf-8")
    print(report_md)
    print(summary_csv)
    print(detail_csv)


if __name__ == "__main__":
    main()

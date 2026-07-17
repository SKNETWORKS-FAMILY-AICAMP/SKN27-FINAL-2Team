from __future__ import annotations

from pathlib import Path

import pandas as pd


WALK_PRED_CSV = Path(
    r"C:\Users\Playdata\Downloads\eval_fixed_walk_forward_v2\eval_fixed_walk_forward_v2\walk_forward_all_rounds_combined_predictions.csv"
)
REPORT_DIR = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\reports")


TARGET_SPECS = [
    ("era", "pred_era", "true_era"),
    ("topic_train", "pred_topic_train", "true_topic_train"),
    ("topic", "pred_topic", "true_topic"),
    ("era_topic_train", "pred_era_topic_train", "true_era_topic_train"),
    ("era_topic_train_topic", "pred_era_topic_train_topic", "true_era_topic_train_topic"),
]


def top_n(df: pd.DataFrame, column: str, n: int = 5) -> pd.DataFrame:
    counts = df[column].astype(str).value_counts().head(n).rename_axis("label").reset_index(name="count")
    counts["ratio"] = counts["count"] / len(df) if len(df) else 0.0
    return counts


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
    df = pd.read_csv(WALK_PRED_CSV)
    df["round_no"] = pd.to_numeric(df["round_no"], errors="raise").astype(int)

    overall_rows = []
    by_round_rows = []

    for target_name, pred_col, true_col in TARGET_SPECS:
        pred_top = top_n(df, pred_col)
        true_top = top_n(df, true_col)
        for rank, row in enumerate(pred_top.itertuples(index=False), start=1):
            overall_rows.append(
                {
                    "scope": "71~78_total",
                    "target": target_name,
                    "source": "predicted",
                    "rank": rank,
                    "label": row.label,
                    "count": int(row.count),
                    "ratio": float(row.ratio),
                }
            )
        for rank, row in enumerate(true_top.itertuples(index=False), start=1):
            overall_rows.append(
                {
                    "scope": "71~78_total",
                    "target": target_name,
                    "source": "actual",
                    "rank": rank,
                    "label": row.label,
                    "count": int(row.count),
                    "ratio": float(row.ratio),
                }
            )

        for round_no, group in df.groupby("round_no"):
            pred_round_top = top_n(group, pred_col)
            true_round_top = top_n(group, true_col)
            for rank, row in enumerate(pred_round_top.itertuples(index=False), start=1):
                by_round_rows.append(
                    {
                        "round_no": int(round_no),
                        "target": target_name,
                        "source": "predicted",
                        "rank": rank,
                        "label": row.label,
                        "count": int(row.count),
                        "ratio": float(row.ratio),
                    }
                )
            for rank, row in enumerate(true_round_top.itertuples(index=False), start=1):
                by_round_rows.append(
                    {
                        "round_no": int(round_no),
                        "target": target_name,
                        "source": "actual",
                        "rank": rank,
                        "label": row.label,
                        "count": int(row.count),
                        "ratio": float(row.ratio),
                    }
                )

    overall_df = pd.DataFrame(overall_rows)
    by_round_df = pd.DataFrame(by_round_rows)

    overall_csv = REPORT_DIR / "walk_forward_최신트렌드_TOP5_예측_전체.csv"
    by_round_csv = REPORT_DIR / "walk_forward_최신트렌드_TOP5_예측_회차별.csv"
    report_md = REPORT_DIR / "walk_forward_최신트렌드_TOP5_예측_분석_2026-07-16.md"

    overall_df.to_csv(overall_csv, index=False, encoding="utf-8-sig")
    by_round_df.to_csv(by_round_csv, index=False, encoding="utf-8-sig")

    main_target = overall_df[
        (overall_df["target"] == "era_topic_train") & (overall_df["source"] == "predicted")
    ].copy()
    main_actual = overall_df[
        (overall_df["target"] == "era_topic_train") & (overall_df["source"] == "actual")
    ].copy()
    topic_train_pred = overall_df[
        (overall_df["target"] == "topic_train") & (overall_df["source"] == "predicted")
    ].copy()
    era_pred = overall_df[(overall_df["target"] == "era") & (overall_df["source"] == "predicted")].copy()

    md = [
        "# Walk-Forward 최신 트렌드 TOP5 예측 분석 - 2026-07-16",
        "",
        "## 목적",
        "",
        "기존 walk-forward 예측 결과를 사용해 71~78회차 최신 구간의 예측 TOP5 데이터를 정리했다.",
        "",
        "추가 학습은 하지 않았다. 입력으로 사용한 파일은 `walk_forward_all_rounds_combined_predictions.csv`이다.",
        "",
        "## 71~78 전체 기준: era + topic_train 예측 TOP5",
        "",
        to_md_table(main_target[["rank", "label", "count", "ratio"]]),
        "",
        "## 71~78 전체 기준: era + topic_train 실제 TOP5",
        "",
        to_md_table(main_actual[["rank", "label", "count", "ratio"]]),
        "",
        "## 71~78 전체 기준: topic_train 예측 TOP5",
        "",
        to_md_table(topic_train_pred[["rank", "label", "count", "ratio"]]),
        "",
        "## 71~78 전체 기준: era 예측 TOP5",
        "",
        to_md_table(era_pred[["rank", "label", "count", "ratio"]]),
        "",
        "## 회차별 era + topic_train 예측 TOP5",
        "",
    ]

    for round_no in sorted(df["round_no"].unique()):
        round_top = by_round_df[
            (by_round_df["round_no"] == round_no)
            & (by_round_df["target"] == "era_topic_train")
            & (by_round_df["source"] == "predicted")
        ]
        md.extend([f"### {round_no}회차", "", to_md_table(round_top[["rank", "label", "count", "ratio"]]), ""])

    md.extend(
        [
            "## 생성 파일",
            "",
            f"- `{overall_csv.name}`",
            f"- `{by_round_csv.name}`",
        ]
    )

    report_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(report_md)
    print(overall_csv)
    print(by_round_csv)


if __name__ == "__main__":
    main()

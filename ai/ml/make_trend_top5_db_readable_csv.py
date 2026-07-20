from __future__ import annotations

from pathlib import Path

import pandas as pd


REPORT_DIR = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\reports")
SOURCE_CSV = REPORT_DIR / "trend_top5_for_db_2026-07-18.csv"
OUTPUT_CSV = REPORT_DIR / "trend_top5_for_db_보기쉬운정리_2026-07-18.csv"


SOURCE_NAME = {
    "recent5_actual": "최근5회차 실제",
    "predicted": "모델분류",
    "actual": "해당회차 실제",
}

TREND_NAME = {
    "era_topic_train": "시대+통합주제",
    "era": "시대",
    "topic_train": "통합주제",
    "topic": "세부주제",
}

TREND_ORDER = {
    "era_topic_train": 1,
    "era": 2,
    "topic_train": 3,
    "topic": 4,
}

SOURCE_ORDER = {
    "recent5_actual": 1,
    "predicted": 2,
    "actual": 3,
}


def display_label(row: pd.Series) -> str:
    if row["trend_type"] == "era_topic_train":
        value = row.get("combo_label_with_topic", "")
        if isinstance(value, str) and value.strip():
            return value
        return str(row.get("combo_label", ""))
    return str(row.get("label", ""))


def main() -> None:
    if not SOURCE_CSV.exists():
        raise FileNotFoundError(SOURCE_CSV)

    df = pd.read_csv(SOURCE_CSV)

    readable = pd.DataFrame(
        {
            "회차": df["target_round"].astype(int),
            "최근5회차": df["recent5_rounds"].astype(str),
            "분류": df["trend_type"].map(TREND_NAME).fillna(df["trend_type"]),
            "기준": df["source"].map(SOURCE_NAME).fillna(df["source"]),
            "순위": df["rank"].astype(int),
            "라벨": df.apply(display_label, axis=1),
            "시대": df["era"].fillna("").astype(str),
            "통합주제": df["topic_train"].fillna("").astype(str),
            "세부주제": df["topic"].fillna("").astype(str),
            "세부주제요약": df["topic_summary"].fillna("").astype(str),
            "개수": df["count"].astype(int),
            "비율": df["ratio_percent"].astype(float).map(lambda value: f"{value:.1f}%"),
            "source": df["source"].astype(str),
            "trend_type": df["trend_type"].astype(str),
        }
    )

    readable["_trend_order"] = df["trend_type"].map(TREND_ORDER).fillna(99)
    readable["_source_order"] = df["source"].map(SOURCE_ORDER).fillna(99)
    readable = (
        readable.sort_values(["회차", "_trend_order", "_source_order", "순위"])
        .drop(columns=["_trend_order", "_source_order"])
        .reset_index(drop=True)
    )

    readable.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(OUTPUT_CSV)
    print(readable.shape)


if __name__ == "__main__":
    main()

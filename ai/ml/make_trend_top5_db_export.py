from __future__ import annotations

from pathlib import Path

import pandas as pd


REPORT_DIR = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\reports")
SOURCE_CSV = REPORT_DIR / "최근5회차_TOP5_트렌드_topic포함_비교_상세.csv"
FEATURE_CSV = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\output\split_v2\full_features_v2.csv")
WALK_PRED_CSV = Path(
    r"C:\Users\Playdata\Downloads\eval_fixed_walk_forward_v2\eval_fixed_walk_forward_v2\walk_forward_all_rounds_combined_predictions.csv"
)
OUTPUT_CSV = REPORT_DIR / "trend_top5_for_db_2026-07-18.csv"


SOURCE_LABELS = {
    "recent5_actual": "직전 5회차 실제 라벨 기준 TOP5",
    "predicted": "해당 회차 모델 분류 기준 TOP5",
    "actual": "해당 회차 실제 라벨 기준 TOP5",
}

SOURCE_USAGE = {
    "recent5_actual": "최근 출제 경향 통계/학습 계획 참고",
    "predicted": "모델 분류 결과 검증/라벨링 품질 확인",
    "actual": "해당 회차 실제 분포 비교 기준",
}


def split_combo(label: str) -> tuple[str, str]:
    parts = str(label).split(" + ", maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return str(label).strip(), ""


def top_counts(df: pd.DataFrame, column: str, top_n: int = 5) -> pd.DataFrame:
    result = df[column].astype(str).value_counts().head(top_n).rename_axis("label").reset_index(name="count")
    source_total = len(df)
    result["ratio"] = result["count"] / source_total if source_total else 0.0
    return result


def make_single_label_rows(
    round_no: int,
    recent5_rounds: str,
    source: str,
    trend_type: str,
    top_df: pd.DataFrame,
) -> list[dict]:
    rows = []
    for rank, row in enumerate(top_df.itertuples(index=False), start=1):
        label = str(row.label)
        rows.append(
            {
                "target_round": round_no,
                "recent5_rounds": recent5_rounds,
                "source": source,
                "source_name": SOURCE_LABELS.get(source, source),
                "usage": SOURCE_USAGE.get(source, ""),
                "trend_type": trend_type,
                "rank": rank,
                "era": label if trend_type == "era" else "",
                "topic_train": label if trend_type == "topic_train" else "",
                "topic": label if trend_type == "topic" else "",
                "topic_summary": "",
                "label": label,
                "combo_label": "",
                "combo_label_with_topic": "",
                "count": int(row.count),
                "ratio": float(row.ratio),
                "ratio_percent": round(float(row.ratio) * 100, 2),
            }
        )
    return rows


def main() -> None:
    if not SOURCE_CSV.exists():
        raise FileNotFoundError(SOURCE_CSV)
    if not FEATURE_CSV.exists():
        raise FileNotFoundError(FEATURE_CSV)
    if not WALK_PRED_CSV.exists():
        raise FileNotFoundError(WALK_PRED_CSV)

    df = pd.read_csv(SOURCE_CSV)
    features = pd.read_csv(FEATURE_CSV)
    walk = pd.read_csv(WALK_PRED_CSV)

    features["round_no"] = pd.to_numeric(features["round_no"], errors="raise").astype(int)
    walk["round_no"] = pd.to_numeric(walk["round_no"], errors="raise").astype(int)

    eras = []
    topic_trains = []
    for label in df["label"].astype(str):
        era, topic_train = split_combo(label)
        eras.append(era)
        topic_trains.append(topic_train)

    export_df = pd.DataFrame(
        {
            "target_round": df["round_no"].astype(int),
            "recent5_rounds": df["recent5_rounds"].astype(str),
            "source": df["source"].astype(str),
            "source_name": df["source"].map(SOURCE_LABELS).fillna(df["source"]),
            "usage": df["source"].map(SOURCE_USAGE).fillna(""),
            "trend_type": "era_topic_train",
            "rank": df["rank"].astype(int),
            "era": eras,
            "topic_train": topic_trains,
            "topic": "",
            "topic_summary": df["topic_top"].fillna("").astype(str),
            "label": df["label"].astype(str),
            "combo_label": df["label"].astype(str),
            "combo_label_with_topic": df["label_with_topic"].astype(str),
            "count": df["count"].astype(int),
            "ratio": df["ratio"].astype(float),
            "ratio_percent": (df["ratio"].astype(float) * 100).round(2),
        }
    )

    single_rows = []
    for round_no in range(71, 79):
        prev_start = round_no - 5
        prev_end = round_no - 1
        recent5_rounds = f"{prev_start}~{prev_end}"
        prev_df = features[(features["round_no"] >= prev_start) & (features["round_no"] <= prev_end)].copy()
        target_df = walk[walk["round_no"] == round_no].copy()

        source_frames = {
            "recent5_actual": {
                "frame": prev_df,
                "era": "era",
                "topic_train": "topic_train",
                "topic": "topic",
            },
            "predicted": {
                "frame": target_df,
                "era": "pred_era",
                "topic_train": "pred_topic_train",
                "topic": "pred_topic",
            },
            "actual": {
                "frame": target_df,
                "era": "true_era",
                "topic_train": "true_topic_train",
                "topic": "true_topic",
            },
        }

        for source, config in source_frames.items():
            frame = config["frame"]
            for trend_type in ["era", "topic_train", "topic"]:
                top_df = top_counts(frame, config[trend_type])
                single_rows.extend(make_single_label_rows(round_no, recent5_rounds, source, trend_type, top_df))

    single_df = pd.DataFrame(single_rows)
    export_df = pd.concat([export_df, single_df], ignore_index=True)

    trend_type_order = {"era_topic_train": 0, "era": 1, "topic_train": 2, "topic": 3}
    source_order = {"recent5_actual": 0, "predicted": 1, "actual": 2}
    export_df["_trend_type_order"] = export_df["trend_type"].map(trend_type_order).fillna(99)
    export_df["_source_order"] = export_df["source"].map(source_order).fillna(99)
    export_df = (
        export_df.sort_values(["target_round", "_trend_type_order", "_source_order", "rank"])
        .drop(columns=["_trend_type_order", "_source_order"])
        .reset_index(drop=True)
    )
    export_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(OUTPUT_CSV)
    print(export_df.shape)


if __name__ == "__main__":
    main()

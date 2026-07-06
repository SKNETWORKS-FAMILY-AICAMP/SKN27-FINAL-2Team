from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = PROJECT_ROOT / "question_generation" / "outputs" / "topic_keywords_seed.csv"
DEFAULT_TIMELINE = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "processed" / "history_timeline_processed.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "question_generation" / "outputs" / "topic_keywords_seed_balanced.csv"


FIELD_TOPIC_TYPE = {
    "인물": "인물",
    "유물·유적": "문화재",
    "조직·단체": "단체",
    "사건": "사건",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Supplement topic seed with timeline topics for weak eras.")
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--timeline", type=Path, default=DEFAULT_TIMELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--per-era", type=int, default=80)
    return parser.parse_args()


def normalize_era(row: pd.Series) -> str:
    age = str(row.get("age") or "")
    year = row.get("year")
    try:
        year_int = int(year)
    except Exception:
        year_int = None

    if age == "현대" or (year_int is not None and year_int >= 1945):
        return "현대"
    if year_int is not None and 1910 <= year_int < 1945:
        return "일제강점기"
    if age == "근대" or (year_int is not None and 1876 <= year_int < 1910):
        return "개항기"
    if age == "고려":
        return "고려"
    if age == "조선":
        return "조선"
    if age == "고대":
        if year_int is not None and year_int < 300:
            return "선사·초기국가"
        if year_int is not None and year_int >= 676:
            return "남북국"
        return "삼국"
    return age or "기타"


def normalize_seed_era(value: Any) -> str:
    text = str(value or "").strip()
    return {
        "삼국 이전": "선사·초기국가",
        "삼국 시대": "삼국",
        "통일 신라와 발해": "남북국",
        "고려 시대": "고려",
        "조선 시대": "조선",
        "근대": "개항기",
    }.get(text, text or "기타")


def topic_type_from_field(field: Any) -> str:
    return FIELD_TOPIC_TYPE.get(str(field), "기타")


def add_missing_columns(df: pd.DataFrame) -> pd.DataFrame:
    for column, value in {
        "keyword_source": "pdf_textbook_term",
        "normalized_era": "",
        "source_field": "",
    }.items():
        if column not in df.columns:
            df[column] = value
    return df


def main() -> None:
    args = parse_args()
    seed = add_missing_columns(pd.read_csv(args.seed))
    seed["normalized_era"] = seed["normalized_era"].fillna("")
    seed.loc[seed["normalized_era"] == "", "normalized_era"] = seed["source_era"].map(normalize_seed_era)

    timeline = pd.read_csv(args.timeline)
    timeline = timeline.dropna(subset=["title"]).copy()
    timeline["topic"] = timeline["title"].astype(str).str.strip()
    timeline = timeline[timeline["topic"].str.len() >= 2]
    timeline["normalized_era"] = timeline.apply(normalize_era, axis=1)
    timeline["topic_type"] = timeline["field"].map(topic_type_from_field)

    existing = set(seed["topic"].astype(str))
    supplements: list[pd.DataFrame] = []
    for era, group in timeline.groupby("normalized_era", sort=False):
        missing = group[~group["topic"].isin(existing)].drop_duplicates("topic")
        supplements.append(missing.head(args.per_era))

    supplement = pd.concat(supplements, ignore_index=True) if supplements else pd.DataFrame()
    if not supplement.empty:
        base_columns = list(seed.columns)
        rows = []
        for _, row in supplement.iterrows():
            item = {column: "" for column in base_columns}
            item.update(
                {
                    "rank": 0,
                    "term_id": f"timeline:{row.get('year')}:{row['topic']}",
                    "topic": row["topic"],
                    "topic_type": row["topic_type"],
                    "source_era": row["normalized_era"],
                    "pdf_hit_total": 0,
                    "pdf_source_count": 0,
                    "pdf_score": 0,
                    "neo4j_eras": row["normalized_era"],
                    "keyword_source": "history_timeline",
                    "normalized_era": row["normalized_era"],
                    "source_field": row.get("field", ""),
                }
            )
            rows.append(item)
        supplement_df = pd.DataFrame(rows, columns=base_columns)
        combined = pd.concat([seed, supplement_df], ignore_index=True)
    else:
        combined = seed.copy()

    combined = combined.drop_duplicates("topic", keep="first").reset_index(drop=True)
    combined["rank"] = range(1, len(combined) + 1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.output, index=False, encoding="utf-8-sig")

    summary = {
        "seed_rows": int(len(seed)),
        "timeline_rows": int(len(timeline)),
        "written_rows": int(len(combined)),
        "output": str(args.output),
        "normalized_era_counts": combined["normalized_era"].fillna("").value_counts().to_dict(),
        "topic_type_counts": combined["topic_type"].fillna("").value_counts().to_dict(),
    }
    args.output.with_name(args.output.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

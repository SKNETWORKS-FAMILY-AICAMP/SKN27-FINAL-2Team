"""Normalize Korean history timeline CSV.

Outputs:
  - history_timeline_processed.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


ERA_BY_CODE = {
    "an": "고대",
    "go": "고려",
    "jo": "조선",
    "mo": "근대",
    "cu": "현대",
}

FIELD_BY_CODE = {
    "field_n": "인물",
    "field_e": "사건",
    "field_g": "조직·단체",
    "field_r": "유물·유적",
}

DROP_COLUMNS = {"content_type", "level_id", "source_url"}


def parse_content_type(value: str) -> tuple[str, str]:
    codes = set((value or "").split())
    era = next((name for code, name in ERA_BY_CODE.items() if code in codes), "")
    field = next((name for code, name in FIELD_BY_CODE.items() if code in codes), "")
    return era, field


def normalize_csv(input_path: Path, output_path: Path) -> int:
    with input_path.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        if not reader.fieldnames:
            raise ValueError(f"CSV header not found: {input_path}")

        fieldnames = [name for name in reader.fieldnames if name not in DROP_COLUMNS]
        for name in ("era", "field"):
            if name not in fieldnames:
                fieldnames.append(name)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8-sig", newline="") as dst:
            writer = csv.DictWriter(dst, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()

            count = 0
            for row in reader:
                era, field = parse_content_type(row.get("content_type", ""))
                row["era"] = era
                row["field"] = field
                writer.writerow({key: value for key, value in row.items() if key not in DROP_COLUMNS})
                count += 1
            return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("etl/raw_data/한국사연대기_연표/history_timeline.csv"))
    parser.add_argument("--output", type=Path, default=Path("etl/preprocessing/history/processed/history_timeline_processed.csv"))
    args = parser.parse_args()

    assert parse_content_type("jo field_n") == ("조선", "인물")
    assert parse_content_type("go field_r") == ("고려", "유물·유적")

    count = normalize_csv(args.input, args.output)
    print(f"rows={count}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()

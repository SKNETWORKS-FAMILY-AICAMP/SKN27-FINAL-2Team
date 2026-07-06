from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "question_generation" / "outputs"
DEFAULT_TOPIC_POOL = OUTPUT_DIR / "topic_keywords_seed_balanced.csv"
DEFAULT_SCHEMA_CACHE = OUTPUT_DIR / "sllm_type_schema_seed.csv"
DEFAULT_OUTPUT = OUTPUT_DIR / "select_seed_sample.json"
EXCLUDED_MATERIAL_TYPES = {"\uc2dc\uac01 \uc790\ub8cc \uc124\uba85"}

SCHEMA_FIELDS = (
    "topic_type",
    "material_type",
    "major_type",
    "minor_type",
    "question_task",
    "question_task_instruction",
    "difficulty_label",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select the first seed node for the SLLM question pipeline.")
    parser.add_argument("--topic-pool", type=Path, default=DEFAULT_TOPIC_POOL)
    parser.add_argument("--schema-source", type=Path, default=None)
    parser.add_argument("--schema-cache", type=Path, default=DEFAULT_SCHEMA_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=20260706)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--era", default=None, help="Optional era filter, e.g. 조선, 고려, 현대.")
    parser.add_argument("--difficulty", default=None, help="Optional difficulty filter.")
    parser.add_argument("--major-type", default=None, help="Optional major_type filter.")
    parser.add_argument("--question-task", default=None, help="Optional question_task filter.")
    parser.add_argument("--material-type", default=None, help="Optional material_type filter.")
    parser.add_argument(
        "--schema-sampling",
        choices=("weighted", "uniform"),
        default="weighted",
        help="weighted uses v41 training frequency; uniform gives each SLLM schema combination equal chance.",
    )
    return parser.parse_args()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [{k: clean(v) for k, v in row.items()} for row in csv.DictReader(f)]


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def discover_schema_source() -> Path:
    roots = [
        PROJECT_ROOT,
        Path.cwd(),
        Path.home() / "Desktop" / "hanneung_47_66_dataset",
    ]
    seen: set[Path] = set()
    matches: list[Path] = []

    for root in roots:
        if not root.exists():
            continue
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        matches.extend(sorted(root.rglob("*v41*all_problem_structured_pretty.json")))

    if not matches:
        raise FileNotFoundError(
            "Could not find a v41 all_problem_structured_pretty JSON. "
            "Pass --schema-source explicitly."
        )
    return matches[0]


def load_topics(path: Path, era: str | None) -> list[dict[str, Any]]:
    rows = read_csv_rows(path)
    topics: list[dict[str, Any]] = []

    for row in rows:
        topic = clean(row.get("topic"))
        topic_type = clean(row.get("topic_type"))
        if not topic or not topic_type:
            continue

        normalized_era = clean(row.get("normalized_era"))
        source_era = clean(row.get("source_era"))
        selected_era = normalized_era or source_era

        if era and era not in {normalized_era, source_era, selected_era}:
            continue

        topics.append(
            {
                "topic": topic,
                "topic_type": topic_type,
                "era": selected_era,
                "term_id": clean(row.get("term_id")),
                "rank": clean(row.get("rank")),
                "keyword_source": clean(row.get("keyword_source")),
                "source_field": clean(row.get("source_field")),
            }
        )

    if not topics:
        raise ValueError(f"No selectable topic rows found in {path}")
    return topics


def load_type_schema(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list in {path}")

    counts: Counter[tuple[str, ...]] = Counter()
    sample_ids: dict[tuple[str, ...], str] = {}

    for item in data:
        if not isinstance(item, dict):
            continue
        input_obj = item.get("input")
        if not isinstance(input_obj, dict):
            continue

        values = tuple(clean(input_obj.get(field)) for field in SCHEMA_FIELDS)
        if any(not value for value in values):
            continue

        counts[values] += 1
        sample_ids.setdefault(values, clean(item.get("source_id")))

    if not counts:
        raise ValueError(f"No SLLM schema combinations found in {path}")

    schema_rows: list[dict[str, Any]] = []
    for values, count in counts.most_common():
        row = dict(zip(SCHEMA_FIELDS, values, strict=True))
        row["training_count"] = count
        row["sample_source_id"] = sample_ids.get(values, "")
        schema_rows.append(row)
    return schema_rows


def filter_schema(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    filtered = [row for row in rows if row.get("material_type") not in EXCLUDED_MATERIAL_TYPES]
    if args.difficulty:
        filtered = [row for row in filtered if row["difficulty_label"] == args.difficulty]
    if args.major_type:
        filtered = [row for row in filtered if row["major_type"] == args.major_type]
    if args.question_task:
        filtered = [row for row in filtered if row["question_task"] == args.question_task]
    if args.material_type:
        filtered = [row for row in filtered if row["material_type"] == args.material_type]
    if not filtered:
        raise ValueError("No SLLM schema rows remain after filters.")
    return filtered


def choose_schema(rng: random.Random, rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    if mode == "uniform":
        return dict(rng.choice(rows))
    weights = [int(row["training_count"]) for row in rows]
    return dict(rng.choices(rows, weights=weights, k=1)[0])


def build_seed_item(index: int, topic: dict[str, Any], schema: dict[str, Any], seed: int) -> dict[str, Any]:
    topic_type = topic["topic_type"]
    if topic_type != schema["topic_type"]:
        schema = dict(schema)
        schema["topic_type"] = topic_type

    base_fields = {
        "topic": topic["topic"],
        "topic_type": schema["topic_type"],
        "era": topic["era"],
        "material_type": schema["material_type"],
        "major_type": schema["major_type"],
        "minor_type": schema["minor_type"],
        "question_task": schema["question_task"],
        "question_task_instruction": schema["question_task_instruction"],
        "difficulty_label": schema["difficulty_label"],
    }

    return {
        "seed_id": f"seed_{seed}_{index:03d}",
        "node": "select_seed",
        "schema_basis": "v41_sllm_input_schema",
        "selection": base_fields,
        "topic_source": {
            "term_id": topic["term_id"],
            "rank": topic["rank"],
            "keyword_source": topic["keyword_source"],
            "source_field": topic["source_field"],
        },
        "schema_source": {
            "training_count": schema["training_count"],
            "sample_source_id": schema["sample_source_id"],
        },
        "sllm_correct_choice_input_preview": {
            "task_type": "correct_choice_generation",
            "material": "<generated_by_material_node>",
            "answer_fact_basis": ["<generated_by_material_node>"],
            **base_fields,
        },
    }


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)

    schema_source = args.schema_source or discover_schema_source()
    topics = load_topics(args.topic_pool, args.era)
    schema_rows = load_type_schema(schema_source)
    schema_rows = filter_schema(schema_rows, args)

    cache_fields = list(SCHEMA_FIELDS) + ["training_count", "sample_source_id"]
    write_csv_rows(args.schema_cache, schema_rows, cache_fields)

    items = []
    for index in range(1, args.n + 1):
        topic = rng.choice(topics)
        schema = choose_schema(rng, schema_rows, args.schema_sampling)
        items.append(build_seed_item(index, topic, schema, args.seed))

    output = {
        "node": "select_seed",
        "schema_version": "v41_sllm_seed_v1",
        "topic_pool": str(args.topic_pool),
        "schema_source": str(schema_source),
        "schema_cache": str(args.schema_cache),
        "items": items,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

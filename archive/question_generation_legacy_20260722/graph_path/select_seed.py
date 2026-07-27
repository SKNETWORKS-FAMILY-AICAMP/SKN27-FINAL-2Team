"""토픽 목록과 V41 학습 스키마 분포를 조합하는 구형 seed 선택 CLI.

문제은행 pack이 준비된 현재 운영 경로에서는 사용하지 않고, 신규 Graph/RAG pack을
처음부터 실험할 때만 ``legacy_pack`` 앞단에서 사용한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "question_generation" / "outputs"
DEFAULT_TOPIC_POOL = OUTPUT_DIR / "topic_keywords_seed_balanced.csv"
DEFAULT_SCHEMA_CACHE = OUTPUT_DIR / "sllm_type_schema_seed.csv"
DEFAULT_OUTPUT = OUTPUT_DIR / "select_seed_sample.json"
EXCLUDED_MATERIAL_TYPES = {"\uc2dc\uac01 \uc790\ub8cc \uc124\uba85"}
SUPPORTED_QUESTION_TASKS = {"standard_select", "period_between", "timeline_position", "order"}
ENTITY_TYPE_TO_TOPIC_TYPE = {
    "인물": "인물",
    "문헌": "매체",
    "문화재": "문화유산",
    "Person": "인물",
    "Event": "사건",
    "Policy": "제도",
    "Organization": "집단",
    "Work": "매체",
    "Heritage": "문화유산",
    "State": "국가",
    "Concept": "개념",
}
ENTITY_TYPE_REQUIRED_SOURCES = {"pdf_textbook_term"}
GRAPH_SOURCE_FIELDS = {"neo4j.Term"}
GRAPH_ANCHOR_LABELS = {"Term", "EventGroup", "Event", "Person"}

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
    """토픽·V41 스키마 입력과 seed 선택 필터를 읽는다."""
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
        "--allow-non-graph-topics",
        action="store_true",
        help="Allow topics that cannot be anchored in GraphDB. Off by default because the pipeline uses graph retrieval.",
    )
    parser.add_argument(
        "--schema-sampling",
        choices=("weighted", "uniform"),
        default="weighted",
        help="weighted uses v41 training frequency; uniform gives each SLLM schema combination equal chance.",
    )
    return parser.parse_args()


def clean(value: Any) -> str:
    """CSV·JSON 값을 안전한 공백 제거 문자열로 바꾼다."""
    if value is None:
        return ""
    return str(value).strip()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """UTF-8 BOM을 허용해 CSV를 정규화된 dict 목록으로 읽는다."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [{k: clean(v) for k, v in row.items()} for row in csv.DictReader(f)]


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """필드 순서를 고정해 UTF-8 BOM CSV를 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def discover_schema_source() -> Path:
    """명시 경로가 없을 때 로컬에서 V41 구조화 학습 데이터 파일을 찾는다."""
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


def has_graph_anchor(row: dict[str, Any]) -> bool:
    """토픽 행에 사용 가능한 Neo4j anchor 식별자가 있는지 확인한다."""
    entity_id = clean(row.get("target_entity_id"))
    article_id = clean(row.get("target_article_id"))
    return (
        clean(row.get("anchor_status")) == "ok"
        and article_id.startswith("E")
        and entity_id == f"encykorea:{article_id}"
        and clean(row.get("expected_entity_type")) in ENTITY_TYPE_TO_TOPIC_TYPE
    )


def topic_key(topic: dict[str, Any]) -> str:
    """동일 토픽 중복 선택을 막기 위한 타입·이름 조합 키를 만든다."""
    return clean(topic.get("topic")).replace(" ", "")


def infer_graph_topic_type(
    *,
    anchor_label: str = "",
    entity_types: list[str] | tuple[str, ...] = (),
    themes: list[str] | tuple[str, ...] = (),
    categories: list[str] | tuple[str, ...] = (),
    description: str = "",
) -> str:
    """토픽 행과 Graph anchor 메타데이터에서 실제 topic_type을 추론한다."""
    if anchor_label in {"Event", "EventGroup"}:
        return "사건"
    if anchor_label == "Person":
        return "인물"

    category_text = " ".join(str(value) for value in categories if value)
    entity_text = " ".join(str(value) for value in entity_types if value)
    text = f"{category_text} {entity_text} {description}"

    if re.search(r"(고대국가|중국국가|종족·부족|농업생산물|농작물|초본식물|목본식물)", text):
        return "기타"
    if "인명" in text or "인물" in entity_text:
        return "인물"
    if "서명" in text or "문헌" in text:
        return "매체"
    if re.search(r"(문화·예술>종합예술|영화|희곡|시사만화|소설|작품|연출)", text):
        return "매체"
    if "의학·약학" in text:
        return "문화"
    if re.search(r"(문화재|유물|군수품>무기류|지명|무덤|고분|묘제|성곽|방어시설)", text):
        return "문화유산"
    if re.search(r"(행정기구|사회단체|정치단체|독립운동단체|관청)", category_text) or re.search(
        r"(단체|군대|부대|정부|관청|결사|의병진)(?:[.·]|$)", description.strip()
    ):
        return "집단"
    if re.search(r"(관직|>인사|풍속|의례|>군>|중앙군|지방군)", text):
        return "제도"
    if re.search(r"(정치사상|사회사상|사상·정책|>정책|>제도|법·법령)", category_text):
        return "제도"
    if (
        "전쟁·전투" in text
        or re.search(r"(전쟁|전투|변란|봉기|침입한 사건|일으킨 사건)", description)
        or re.search(r"(정치사건|관련사건|외교분쟁|사회운동|독립운동|의병운동|박해)", category_text)
        or re.search(r"(?:사건|박해)\.?$", description.strip())
    ):
        return "사건"

    for entity_type in entity_types:
        mapped = ENTITY_TYPE_TO_TOPIC_TYPE.get(str(entity_type).strip())
        if mapped:
            return mapped
    theme_map = {
        "단체": "집단",
        "사회": "제도",
        "외교": "제도",
        "경제": "제도",
        "정치": "제도",
        "군사": "제도",
        "사상·종교": "문화",
        "행사": "문화",
    }
    for theme in themes:
        value = str(theme).strip()
        if value in theme_map:
            return theme_map[value]
        if value in {"기타", "매체", "문화", "문화유산", "사건", "인물", "제도", "집단"}:
            return value
    return ""


def load_topics(path: Path, era: str | None, require_graph_anchor: bool = True) -> list[dict[str, Any]]:
    """토픽 CSV를 읽고 시대·Graph anchor 조건을 적용한다."""
    rows = read_csv_rows(path)
    topics: list[dict[str, Any]] = []

    for row in rows:
        if require_graph_anchor and not has_graph_anchor(row):
            continue
        topic = clean(row.get("topic"))
        topic_type = clean(row.get("topic_type"))
        entity_types = clean(row.get("neo4j_entity_types"))
        graph_categories = clean(row.get("graph_categories") or row.get("neo4j_categories"))
        graph_description = clean(row.get("graph_description") or row.get("neo4j_description"))
        if clean(row.get("keyword_source")) in ENTITY_TYPE_REQUIRED_SOURCES and not entity_types:
            continue
        graph_type = ENTITY_TYPE_TO_TOPIC_TYPE.get(
            clean(row.get("expected_entity_type"))
        ) or infer_graph_topic_type(
            anchor_label=clean(row.get("graph_anchor_label")),
            entity_types=tuple(value for value in entity_types.split("|") if value),
            themes=tuple(value for value in clean(row.get("neo4j_themes") or row.get("graph_themes")).split("|") if value),
            categories=tuple(value for value in graph_categories.split("|") if value),
            description=graph_description,
        )
        topic_type = graph_type or topic_type
        if not topic or not topic_type:
            continue

        normalized_era = clean(row.get("normalized_era") or row.get("era"))
        source_era = clean(row.get("source_era") or row.get("era"))
        selected_era = normalized_era or source_era

        if era and era not in {normalized_era, source_era, selected_era}:
            continue

        topics.append(
            {
                "topic": topic,
                "topic_type": topic_type,
                "era": selected_era,
                "source_era": source_era,
                "term_id": clean(row.get("term_id")),
                "target_entity_id": clean(row.get("target_entity_id")),
                "target_article_id": clean(row.get("target_article_id")),
                "expected_entity_type": clean(row.get("expected_entity_type")),
                "relation_axis_id": clean(row.get("relation_axis_id")),
                "rank": clean(row.get("rank")),
                "keyword_source": clean(row.get("keyword_source") or "v41_keyword_book"),
                "source_field": clean(row.get("source_field") or ("neo4j.Term" if "Term" in clean(row.get("graph_labels")).split("|") else "")),
                "graph_anchor_label": clean(row.get("graph_anchor_label") or clean(row.get("graph_labels")).split("|")[0]),
                "graph_entity_types": entity_types,
                "graph_themes": clean(row.get("neo4j_themes") or row.get("graph_themes")),
                "graph_eras": clean(row.get("neo4j_eras") or row.get("graph_eras")),
                "graph_categories": graph_categories,
                "graph_description": graph_description,
            }
        )

    if not topics:
        raise ValueError(f"No selectable topic rows found in {path}")
    return topics


def load_type_schema(path: Path) -> list[dict[str, Any]]:
    """V41 학습 입력에서 유형 조합과 출현 빈도를 집계한다."""
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
    """CLI 조건과 미지원 material_type을 기준으로 V41 스키마를 거른다."""
    filtered = [
        row
        for row in rows
        if row.get("material_type") not in EXCLUDED_MATERIAL_TYPES
        and row.get("question_task") in SUPPORTED_QUESTION_TASKS
    ]
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
    """V41 빈도 가중 또는 균등 방식으로 스키마 한 개를 선택한다."""
    if mode == "uniform":
        return dict(rng.choice(rows))
    weights = [int(row["training_count"]) for row in rows]
    return dict(rng.choices(rows, weights=weights, k=1)[0])


def build_seed_item(index: int, topic: dict[str, Any], schema: dict[str, Any], seed: int) -> dict[str, Any]:
    """선택된 topic과 V41 스키마를 구형 Graph/RAG seed item으로 합친다."""
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
        "target_entity_id": topic["target_entity_id"],
        "target_article_id": topic["target_article_id"],
        "expected_entity_type": topic["expected_entity_type"],
        "relation_axis_id": topic["relation_axis_id"],
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
            "graph_anchor_label": topic["graph_anchor_label"],
            "source_era": topic["source_era"],
            "graph_entity_types": topic["graph_entity_types"],
            "graph_themes": topic["graph_themes"],
            "graph_eras": topic["graph_eras"],
            "graph_categories": topic["graph_categories"],
            "graph_description": topic["graph_description"],
            "target_entity_id": topic["target_entity_id"],
            "target_article_id": topic["target_article_id"],
            "expected_entity_type": topic["expected_entity_type"],
            "relation_axis_id": topic["relation_axis_id"],
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
    """토픽과 스키마를 선택해 seed JSON과 스키마 캐시를 저장한다."""
    args = parse_args()
    rng = random.Random(args.seed)

    schema_source = args.schema_source or discover_schema_source()
    topics = load_topics(args.topic_pool, args.era, require_graph_anchor=False)
    schema_rows = load_type_schema(schema_source)
    schema_rows = filter_schema(schema_rows, args)

    cache_fields = list(SCHEMA_FIELDS) + ["training_count", "sample_source_id"]
    write_csv_rows(args.schema_cache, schema_rows, cache_fields)

    items = []
    attempts = max(args.n * 20, args.n)
    index = 1
    used_topics: set[str] = set()
    for _ in range(attempts):
        if len(items) >= args.n:
            break
        topic = rng.choice(topics)
        key = topic_key(topic)
        if key in used_topics:
            continue
        topic_schema_rows = [row for row in schema_rows if row["topic_type"] == topic["topic_type"]]
        if not topic_schema_rows:
            continue
        schema = choose_schema(rng, topic_schema_rows, args.schema_sampling)
        items.append(build_seed_item(index, topic, schema, args.seed))
        used_topics.add(key)
        index += 1
    if len(items) < args.n:
        raise ValueError(f"Only selected {len(items)} seeds for requested n={args.n}")

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

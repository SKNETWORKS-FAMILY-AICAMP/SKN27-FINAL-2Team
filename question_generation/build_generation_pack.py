from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever, result_to_payload
from question_generation.select_seed import (
    DEFAULT_TOPIC_POOL,
    build_seed_item,
    choose_schema,
    discover_schema_source,
    filter_schema,
    load_topics,
    load_type_schema,
)


OUTPUT_DIR = PROJECT_ROOT / "question_generation" / "outputs"
DEFAULT_OUTPUT = OUTPUT_DIR / "generation_pack_sample.json"
DEFAULT_SCHEMA_CACHE = OUTPUT_DIR / "sllm_type_schema_seed.csv"
DEFAULT_MATERIAL_EXAMPLES = OUTPUT_DIR / "material_type_examples_v41.json"
DEFAULT_MATERIAL_PROMPT_RULES = PROJECT_ROOT / "question_generation" / "material_type_prompt_rules.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build pre-SLLM generation packs.")
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260706)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--topic-pool", type=Path, default=DEFAULT_TOPIC_POOL)
    parser.add_argument("--schema-source", type=Path, default=None)
    parser.add_argument("--schema-cache", type=Path, default=DEFAULT_SCHEMA_CACHE)
    parser.add_argument("--material-examples", type=Path, default=DEFAULT_MATERIAL_EXAMPLES)
    parser.add_argument("--material-prompt-rules", type=Path, default=DEFAULT_MATERIAL_PROMPT_RULES)
    parser.add_argument("--no-material-example", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--material-top-k", type=int, default=5)
    parser.add_argument("--distractor-target-count", type=int, default=4)
    parser.add_argument("--era", default=None)
    parser.add_argument("--difficulty", default=None)
    parser.add_argument("--major-type", default=None)
    parser.add_argument("--question-task", default=None)
    parser.add_argument("--material-type", default=None)
    parser.add_argument("--schema-sampling", choices=("weighted", "uniform"), default="weighted")
    parser.add_argument("--dry-run", action="store_true", help="Skip OpenAI material generation.")
    return parser.parse_args()


def load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")


def compact(text: str | None, limit: int = 500) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "..."


def load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_material_examples(path: Path) -> dict[str, list[dict[str, Any]]]:
    return load_json_dict(path)


def choose_material_example(
    examples: dict[str, list[dict[str, Any]]],
    selection: dict[str, Any],
    seed: int,
) -> dict[str, Any] | None:
    topic = selection["topic"].replace(" ", "")
    pool = [
        item
        for item in (examples.get(selection["material_type"]) or [])
        if topic not in str(item.get("material", "")).replace(" ", "")
    ]
    if not pool:
        return None
    return random.Random(f"{seed}:{selection['seed_id']}:{selection['material_type']}").choice(pool)


def material_type_rules_text(rules: dict[str, Any], material_type: str) -> str:
    values = rules.get(material_type) or []
    if not isinstance(values, list):
        return ""
    return "\n".join(f"- {value}" for value in values if str(value).strip())


def material_query(selection: dict[str, Any]) -> str:
    return f"{selection['topic']} {selection['era']} {selection['topic_type']} 설명 특징 의의".strip()


def text_mentions(text: str, terms: list[str]) -> bool:
    compacted = (text or "").replace(" ", "")
    return any(term and term.replace(" ", "") in compacted for term in terms)


def clean_graph_name(value: str | None) -> str:
    text = (value or "").split("〔", 1)[0].split("(", 1)[0]
    return " ".join(text.split())


def graph_driver_or_none() -> Any:
    uri = os.getenv("NEO4J_URI") or f"bolt://localhost:{os.getenv('NEO4J_BOLT_PORT', '7687')}"
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        return None
    return GraphDatabase.driver(uri, auth=(os.getenv("NEO4J_USER", "neo4j"), password))


def graph_context(driver: Any, selection: dict[str, Any]) -> dict[str, Any]:
    if driver is None:
        return {"anchor_label": "", "anchor": {}, "required_clues": [], "graph_facts": []}

    topic = selection["topic"]
    with driver.session() as session:
        event_group = session.run(
            """
            MATCH (g:EventGroup {name:$topic})<-[:PART_OF_EVENT_GROUP]-(e:Event)
            RETURN properties(g) AS anchor, collect(e.name)[0..12] AS event_names
            LIMIT 1
            """,
            topic=topic,
        ).single()
        if event_group:
            names = [clean_graph_name(name) for name in event_group["event_names"] if clean_graph_name(name)]
            return {
                "anchor_label": "EventGroup",
                "anchor": event_group["anchor"],
                "required_clues": names[:5],
                "graph_facts": [f"{topic} 관련 하위 사건: {', '.join(names[:8])}"],
            }

        term = session.run(
            """
            MATCH (t:Term {name:$topic})
            OPTIONAL MATCH (t)-[:HAS_THEME]->(theme:Theme)
            OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
            OPTIONAL MATCH (t)-[:IN_ERA]->(era:Era)
            RETURN properties(t) AS anchor,
                   collect(DISTINCT theme.name)[0..3] AS themes,
                   collect(DISTINCT cat.category_path)[0..3] AS categories,
                   collect(DISTINCT era.name)[0..3] AS eras
            LIMIT 1
            """,
            topic=topic,
        ).single()
        if term:
            anchor = dict(term["anchor"])
            description = compact(anchor.get("description"), 180)
            return {
                "anchor_label": "Term",
                "anchor": anchor,
                "themes": term["themes"],
                "categories": term["categories"],
                "eras": term["eras"],
                "required_clues": [description] if description else [],
                "graph_facts": [description] if description else [],
            }

    return {"anchor_label": "", "anchor": {}, "required_clues": [], "graph_facts": []}


def graph_sources(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": f"neo4j:{context.get('anchor_label', 'unknown')}",
            "source_type": "neo4j_graph_fact",
            "title": f"{context.get('anchor_label', 'Graph')} fact",
            "score": 1.0,
            "snippet": fact,
        }
        for fact in context.get("graph_facts", [])
        if fact
    ]


def one_line_basis(target: str, description: str | None, events: list[str] | None = None) -> str:
    if description:
        return compact(description, 260)
    clean_events = [clean_graph_name(name) for name in (events or []) if clean_graph_name(name)]
    if clean_events:
        return compact(f"{target}은/는 {', '.join(clean_events[:3])} 등을 포함하는 사건군이다.", 260)
    return compact(f"{target}에 대한 그래프 DB 후보이다.", 260)


def retrieve_distractor_basis(driver: Any, selection: dict[str, Any], context: dict[str, Any], target_count: int) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    if driver is None or target_count <= 0:
        return [], []

    topic = selection["topic"]
    anchor_label = context.get("anchor_label")
    rows: list[dict[str, Any]] = []
    with driver.session() as session:
        if anchor_label == "EventGroup":
            rows = session.run(
                """
                MATCH (:EventGroup {name:$topic})<-[:PART_OF_EVENT_GROUP]-(seed:Event)-[:IN_ERA]->(era:Era)
                OPTIONAL MATCH (seed)-[:HAS_CATEGORY]->(seed_cat:CanonicalCategory)
                WITH collect(DISTINCT era.name) AS eras,
                     collect(DISTINCT seed_cat.category_path) AS seed_categories,
                     $topic AS topic
                MATCH (e:Event)-[:IN_ERA]->(era:Era)
                WHERE era.name IN eras
                MATCH (e)-[:PART_OF_EVENT_GROUP]->(g:EventGroup)
                WHERE g.name <> topic
                OPTIONAL MATCH (e)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
                OPTIONAL MATCH (t:Term {name:g.name})
                WITH g.name AS target, t.description AS description, t.period_text AS term_period,
                     count(DISTINCT e) AS event_count,
                     collect(DISTINCT e.name)[0..4] AS events,
                     collect(DISTINCT era.name)[0] AS event_era,
                     collect(DISTINCT cat.category_path) AS candidate_categories,
                     seed_categories
                WITH target, description, term_period, event_count, events, event_era,
                     size([x IN candidate_categories WHERE x IN seed_categories]) AS category_overlap
                RETURN target, description, term_period, event_count, events, event_era, category_overlap,
                       'same_era_event_group' AS relation_reason
                ORDER BY category_overlap DESC, event_count DESC, target
                LIMIT $limit
                """,
                topic=topic,
                limit=target_count,
            ).data()
        elif anchor_label == "Term":
            rows = session.run(
                """
                MATCH (seed:Term {name:$topic})
                OPTIONAL MATCH (seed)-[:IN_ERA]->(era:Era)
                OPTIONAL MATCH (seed)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
                OPTIONAL MATCH (seed)-[:HAS_THEME]->(theme:Theme)
                WITH seed,
                     collect(DISTINCT era.name) AS seed_eras,
                     collect(DISTINCT cat.category_path) AS seed_categories,
                     collect(DISTINCT theme.name) AS seed_themes
                MATCH (t:Term)
                WHERE t.name <> seed.name AND t.description IS NOT NULL
                OPTIONAL MATCH (t)-[:IN_ERA]->(te:Era)
                OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(tc:CanonicalCategory)
                OPTIONAL MATCH (t)-[:HAS_THEME]->(tt:Theme)
                WITH t, seed_eras, seed_categories, seed_themes,
                     collect(DISTINCT te.name) AS eras,
                     collect(DISTINCT tc.category_path) AS categories,
                     collect(DISTINCT tt.name) AS themes
                WITH t,
                     size([x IN eras WHERE x IN seed_eras]) AS era_overlap,
                     size([x IN categories WHERE x IN seed_categories]) AS category_overlap,
                     size([x IN themes WHERE x IN seed_themes]) AS theme_overlap
                WHERE era_overlap > 0 AND (category_overlap > 0 OR theme_overlap > 0)
                RETURN t.name AS target, t.description AS description,
                       category_overlap, theme_overlap, era_overlap,
                       'same_era_category_or_theme_term' AS relation_reason
                ORDER BY category_overlap DESC, theme_overlap DESC, era_overlap DESC, target
                LIMIT $limit
                """,
                topic=topic,
                limit=target_count,
            ).data()

    targets: list[dict[str, Any]] = []
    basis_list: list[list[dict[str, Any]]] = []
    for row in rows[:target_count]:
        target = row.get("target") or ""
        description = row.get("description")
        if anchor_label == "EventGroup" and row.get("term_period") and row.get("event_era"):
            if row["event_era"] not in row["term_period"]:
                description = None
        basis = one_line_basis(target, description, row.get("events"))
        targets.append(
            {
                "term_name": target,
                "term_times": "",
                "description": basis,
                "relation_reason": row.get("relation_reason", ""),
            }
        )
        basis_list.append(
            [
                {
                    "target": target,
                    "chunk_id": f"neo4j:distractor:{target}",
                    "source_type": "neo4j_graph_fact",
                    "title": f"{target} fact",
                    "score": 1.0,
                    "snippet": basis,
                }
            ]
        )
    return targets, basis_list


def retrieve_material_sources(retriever: PgVectorHybridRetriever, selection: dict[str, Any], top_k: int) -> list[dict[str, Any]]:
    results = [
        {
            "chunk_id": item["chunk_id"],
            "source_type": item["source_type"],
            "title": item["title"],
            "score": item["score"],
            "snippet": item["snippet"],
        }
        for item in (
            result_to_payload(result)
            for result in retriever.search(material_query(selection), top_k=max(top_k * 4, top_k))
        )
    ]
    focus_terms = [selection["topic"]]
    matched = [item for item in results if text_mentions(f"{item['title']} {item['snippet']}", focus_terms)]
    tail = [item for item in results if item not in matched]
    return (matched + tail)[:top_k]


def chat_json(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    timeout: int,
    max_retries: int,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if not model.startswith("gpt-5"):
        body["temperature"] = temperature
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"] or "{}"
            return json.loads(content)
        except (json.JSONDecodeError, KeyError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"OpenAI material generation failed: {last_error}")


def generate_material(
    *,
    selection: dict[str, Any],
    sources: list[dict[str, Any]],
    material_example: dict[str, Any] | None,
    material_rules: str,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float,
    timeout: int,
    max_retries: int,
    required_clues: list[str] | None = None,
    forbidden_terms: list[str] | None = None,
) -> dict[str, Any]:
    source_text = "\n".join(f"- {source['title']}: {source['snippet']}" for source in sources)
    required_clues = [clue for clue in (required_clues or []) if str(clue).strip()]
    forbidden_terms = forbidden_terms or [selection["topic"]]
    example_text = ""
    if material_example:
        example_text = f"""

v41 material 형식 예시({selection['material_type']}):
{json.dumps(material_example, ensure_ascii=False, indent=2)}

주의: 위 예시는 material의 길이·문체·형식만 참고한다. 예시의 topic과 역사 사실은 재사용하지 않는다.
""".rstrip()
    material_rules = material_rules or "- 정답명을 직접 노출하지 않고, topic을 추론하게 쓴다."
    messages = [
        {
            "role": "system",
            "content": (
                "너는 한국사능력검정시험 심화형 문항의 지문과 정답 근거를 만드는 도구다. "
                "반드시 JSON 객체만 출력한다."
            ),
        },
        {
            "role": "user",
            "content": f"""
다음 seed와 RAG 근거만 사용해 SLLM 입력용 material과 answer_fact_basis를 만들어라.

공통 규칙:
- material은 forbidden_terms를 직접 노출하지 말고, topic을 추론하게 하는 한능검식 자료 지문이어야 한다.
- required_clues가 있으면 material에 그중 최소 1개 이상을 문자열 그대로 포함한다.
- material은 반드시 topic을 식별하게 하는 핵심 단서에 집중한다.
- material은 RAG 근거 전체를 요약하지 말고, topic을 식별하는 강한 단서 2개만 골라 쓴다.
- topic의 결과·영향·파생 변화가 아니라, topic 자체를 알아볼 수 있는 시기·주체·전개·장소·대표 사건 단서를 우선 사용한다.
- RAG 근거에 topic명이 아닌 대표 사건명·인물명·장소명이 있으면 그중 1~2개를 구체 단서로 사용한다.
- topic_type이 사건이면 사건의 발생 시기, 주체, 직접 전개, 대표 전투·활동 단서를 중심으로 쓰고 결과·영향·의의 설명은 피한다.
- topic_type이 사건이고 연표 문제가 아니면 정확한 연도 범위를 그대로 쓰기보다 시대·전개 단서로 우회한다.
- RAG 근거에 주변 설명이 섞여 있어도, material은 topic 중심 단서만 선별한다.
- material에는 topic 식별 단서를 충분히 넣되, answer_fact_basis 문장을 그대로 반복하지 않는다.
- answer_fact_basis는 정답 선지 생성에 쓸 1~2문장 교과서식 근거여야 한다.
- answer_fact_basis는 material의 문장 반복이 아니라, material을 보고 추론한 대상에 연결되는 별도 정답 사실이어야 한다.
- answer_fact_basis는 단순히 topic을 반복하지 말고, 핵심 사실·배경·의의를 포함해야 한다.
- 없는 사실을 만들지 말고, 근거가 부족하면 RAG 근거 안에서 안전한 표현만 쓴다.
- v41 예시의 역사 내용은 절대 베끼지 말고, material 형식만 따른다.

material_type별 작성 규칙({selection['material_type']}):
{material_rules}

material_type 규칙은 반드시 지킨다. 문장 수·길이 제한을 넘기지 말고, 백과사전식 요약문으로 쓰지 않는다.

seed:
{json.dumps(selection, ensure_ascii=False, indent=2)}

forbidden_terms:
{json.dumps(forbidden_terms, ensure_ascii=False, indent=2)}

required_clues:
{json.dumps(required_clues, ensure_ascii=False, indent=2)}

RAG 근거:
{source_text}
{example_text}

출력 형식:
{{
  "material": "...",
  "answer_fact_basis": ["..."]
}}
""".strip(),
        },
    ]
    result = chat_json(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=messages,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )
    return {
        "material": compact(str(result.get("material") or ""), 900),
        "answer_fact_basis": [compact(str(item), 500) for item in result.get("answer_fact_basis", []) if str(item).strip()][:2],
    }


def build_correct_choice_input(selection: dict[str, Any], material_obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_type": "correct_choice_generation",
        "material": material_obj["material"],
        "answer_fact_basis": material_obj["answer_fact_basis"],
        "topic_type": selection["topic_type"],
        "topic": selection["topic"],
        "material_type": selection["material_type"],
        "major_type": selection["major_type"],
        "minor_type": selection["minor_type"],
        "question_task": selection["question_task"],
        "question_task_instruction": selection["question_task_instruction"],
        "difficulty_label": selection["difficulty_label"],
    }


def select_seeds(args: argparse.Namespace) -> list[dict[str, Any]]:
    rng = random.Random(args.seed)
    schema_source = args.schema_source or discover_schema_source()
    topics = load_topics(args.topic_pool, args.era)
    schema_rows = filter_schema(load_type_schema(schema_source), args)
    return [
        {
            **seed["selection"],
            "seed_id": seed["seed_id"],
            "topic_source": seed["topic_source"],
            "schema_source": seed["schema_source"],
        }
        for seed in (
            build_seed_item(
                index,
                rng.choice(topics),
                choose_schema(rng, schema_rows, args.schema_sampling),
                args.seed,
            )
            for index in range(1, args.n + 1)
        )
    ]


def main() -> None:
    args = parse_args()
    load_env()
    model = args.model or os.getenv("OPENAI_CHAT_MODEL", "gpt-5.4-mini")
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not args.dry_run and not api_key:
        raise RuntimeError("OPENAI_API_KEY is required unless --dry-run is set.")

    retriever = PgVectorHybridRetriever()
    graph_driver = graph_driver_or_none()
    material_examples = {} if args.no_material_example else load_material_examples(args.material_examples)
    material_prompt_rules = load_json_dict(args.material_prompt_rules)
    items: list[dict[str, Any]] = []

    try:
        for selection in select_seeds(args):
            context = graph_context(graph_driver, selection)
            required_clues = context.get("required_clues", [])
            forbidden_terms = [selection["topic"]]
            material_sources = graph_sources(context) + retrieve_material_sources(retriever, selection, args.material_top_k)
            distractor_targets, distractor_fact_basis_list = retrieve_distractor_basis(
                graph_driver,
                selection,
                context,
                args.distractor_target_count,
            )
            material_obj = (
                {"material": "", "answer_fact_basis": []}
                if args.dry_run
                else generate_material(
                    selection=selection,
                    sources=material_sources,
                    material_example=choose_material_example(material_examples, selection, args.seed),
                    material_rules=material_type_rules_text(material_prompt_rules, selection["material_type"]),
                    model=model,
                    base_url=args.base_url,
                    api_key=api_key,
                    temperature=args.temperature,
                    timeout=args.timeout,
                    max_retries=args.max_retries,
                    required_clues=required_clues,
                    forbidden_terms=forbidden_terms,
                )
            )

            items.append(
                {
                    "seed_id": selection["seed_id"],
                    "topic": selection["topic"],
                    "topic_type": selection["topic_type"],
                    "era": selection["era"],
                    "material_type": selection["material_type"],
                    "major_type": selection["major_type"],
                    "minor_type": selection["minor_type"],
                    "question_task": selection["question_task"],
                    "question_task_instruction": selection["question_task_instruction"],
                    "difficulty_label": selection["difficulty_label"],
                    "graph_context": context,
                    "forbidden_terms": forbidden_terms,
                    "required_clues": required_clues,
                    "material_sources": material_sources,
                    "material": material_obj["material"],
                    "answer_fact_basis": material_obj["answer_fact_basis"],
                    "answer_fact_status": "ok" if material_obj["answer_fact_basis"] else "needs_review",
                    "distractor_targets": distractor_targets,
                    "distractor_fact_basis_list": distractor_fact_basis_list,
                    "correct_choice_input": build_correct_choice_input(selection, material_obj),
                }
            )
    finally:
        if graph_driver is not None:
            graph_driver.close()

    output = {
        "schema_version": "generation_pack_v1",
        "model": model if not args.dry_run else "",
        "dry_run": args.dry_run,
        "items": items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

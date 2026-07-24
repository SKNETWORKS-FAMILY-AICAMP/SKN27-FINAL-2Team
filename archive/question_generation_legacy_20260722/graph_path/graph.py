"""Neo4j와 기존 pgvector를 사용하는 구형 실시간 Graph/RAG 검색 구현.
현재 ChoiceFact 문제은행 파이프라인의 오답 후보는 이 파일에서 찾지 않는다.
``graph_path.legacy_pack`` 전용으로 보존된 별도 경로이며, Graph anchor·후보 rerank·
근거 검색을 한 파일에 포함한다. 운영 문제은행 경로는 ``choice_pool.py``를 사용한다.
"""

from __future__ import annotations

import csv
import os
import re
from functools import lru_cache
from typing import Any

from neo4j import GraphDatabase

from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever, result_to_payload
from question_generation.core.difficulty import target_score_from_selection
from question_generation.core.text import compact
from question_generation.graph_path.fact_constraints import answer_fact_hint_sentences
from question_generation.graph_path.query_plan import material_query, retrieval_plan, text_mentions
from question_generation.graph_path.select_seed import DEFAULT_TOPIC_POOL, infer_graph_topic_type
from question_generation.graph_path.topic_keywords import normalize_seed_era


TIMELINE_SLOT_BY_INTENT = {
    "timeline_compare": {"before": "before_after_context", "during": "during_fact", "after": "before_after_context"},
    "timeline_position": {"before": "before_event", "during": "target_event", "after": "after_event"},
    "period_between": {"before": "period_start", "during": "target_between", "after": "period_end"},
    "timeline_order": {"before": "sequence_events", "during": "sequence_events", "after": "sequence_events"},
}


def distractor_query(selection: dict[str, Any], target: str) -> str:
    """오답 후보 target의 사실을 pgvector에서 찾을 검색 문자열을 만든다."""
    return f"{target} {selection['era']} {selection['topic_type']} 설명 특징 의의".strip()


def clean_graph_name(value: str | None) -> str:
    """Graph node 이름의 괄호·부가 표기를 제거해 비교용 이름으로 만든다."""
    text = (value or "").split("〔", 1)[0].split("(", 1)[0]
    return " ".join(text.split())


def graph_driver_or_none() -> Any:
    """Neo4j 환경변수가 있으면 driver를 만들고 없으면 Graph 기능을 비활성화한다."""
    uri = os.getenv("NEO4J_URI") or f"bolt://localhost:{os.getenv('NEO4J_BOLT_PORT', '7687')}"
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        return None
    return GraphDatabase.driver(uri, auth=(os.getenv("NEO4J_USER", "neo4j"), password))


def normalized_era(value: Any) -> str:
    """여러 시대 표기를 후보 비교용 표준 시대 문자열로 바꾼다."""
    return normalize_seed_era(str(value or "").replace(" ", ""))


def eras_are_close(left: Any, right: Any) -> bool:
    """두 시대가 같거나 표준 시대 순서에서 서로 인접하는지 확인한다."""
    order = ["선사·초기국가", "삼국", "남북국", "고려", "조선", "개항기", "일제강점기", "현대"]
    left_era, right_era = normalized_era(left), normalized_era(right)
    if left_era in {"", "기타"} or right_era in {"", "기타"}:
        return True
    if left_era not in order or right_era not in order:
        return left_era == right_era
    return abs(order.index(left_era) - order.index(right_era)) <= 1


def graph_context_score(
    selection: dict[str, Any],
    context: dict[str, Any],
    identity_sources: list[dict[str, Any]],
) -> tuple[int, list[str]]:
    """Graph anchor와 RAG 식별 근거가 seed와 맞는 정도를 점수와 사유로 반환한다."""
    score = 0
    reasons: list[str] = []
    expected_type = graph_expected_topic_type(context)
    actual_type = str(selection.get("topic_type") or "")
    if expected_type:
        matched = expected_type == actual_type
        score += 100 if matched else -100
        reasons.append("topic_type_match" if matched else "topic_type_mismatch")

    seed_era = normalized_era(selection.get("era"))
    anchor_era = normalized_era(context.get("anchor", {}).get("period_text"))
    if seed_era not in {"", "기타"} and anchor_era not in {"", "기타"}:
        matched = seed_era == anchor_era
        score += 30 if matched else -30
        reasons.append("era_match" if matched else "era_mismatch")

    requested_term_id = str(selection.get("topic_source", {}).get("term_id") or "").strip()
    if requested_term_id and graph_anchor_term_id(context) == requested_term_id:
        score += 10
        reasons.append("requested_term_id")

    if identity_sources:
        aligned = any(not source_contradicts_context(source, context, selection) for source in identity_sources)
        score += 120 if aligned else -200
        reasons.append("source_aligned" if aligned else "source_conflict")

    anchor = context.get("anchor", {})
    score += 3 if anchor.get("question_ready") == "Y" else 0
    score += 1 if anchor.get("is_exam_keyword") == "Y" else 0
    return score, reasons


def graph_context(
    driver: Any,
    selection: dict[str, Any],
    identity_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """topic에 대응하는 EventGroup·Term anchor와 연결 메타데이터를 조회한다."""
    del identity_sources
    if driver is None:
        return {"anchor_label": "", "anchor": {}, "required_clues": [], "graph_facts": []}

    topic = selection["topic"]
    term_id = str(selection.get("topic_source", {}).get("term_id") or "").strip()
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
            MATCH (t:Term)
            WHERE ($term_id <> '' AND t.term_id = $term_id) OR ($term_id = '' AND t.name = $topic)
            OPTIONAL MATCH (t)-[:HAS_THEME]->(theme:Theme)
            OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
            OPTIONAL MATCH (t)-[:IN_ERA]->(era:Era)
            RETURN properties(t) AS anchor,
                   collect(DISTINCT theme.name)[0..3] AS themes,
                   collect(DISTINCT cat.category_path)[0..3] AS categories,
                   collect(DISTINCT era.name)[0..3] AS eras
            LIMIT 1
            """,
            term_id=term_id,
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


def graph_expected_topic_type(context: dict[str, Any]) -> str:
    """Graph anchor label과 entity metadata에서 기대 topic_type을 결정한다."""
    return infer_graph_topic_type(
        anchor_label=str(context.get("anchor_label") or ""),
        entity_types=tuple(str(value) for value in context.get("entity_types", []) if value),
        themes=tuple(str(value) for value in context.get("themes", []) if value),
        categories=tuple(
            str(value)
            for value in [context.get("anchor", {}).get("category_text", ""), *context.get("categories", [])]
            if value
        ),
        description=str(context.get("anchor", {}).get("description") or ""),
    )


def graph_topic_type_status(selection: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """seed topic_type과 Graph가 기대하는 타입이 일치하는지 검사한다."""
    expected = graph_expected_topic_type(context)
    actual = str(selection.get("topic_type") or "")
    if not expected or actual == expected:
        return {"status": "ok", "expected_topic_type": expected, "actual_topic_type": actual}
    return {"status": "needs_review", "expected_topic_type": expected, "actual_topic_type": actual}


def graph_anchor_status(selection: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Graph anchor 존재 여부와 요청한 term_id 일치 여부를 검사한다."""
    errors: list[str] = []
    anchor = context.get("anchor", {})
    if not context.get("anchor_label") or not anchor:
        errors.append("missing_graph_anchor")
    topic = clean_graph_name(str(selection.get("topic") or ""))
    names = {
        clean_graph_name(str(value))
        for value in [anchor.get("name"), *(anchor.get("aliases") or [])]
        if value
    }
    if topic not in names:
        errors.append("graph_anchor_topic_mismatch")

    requested_entity_id = str(selection.get("target_entity_id") or "").strip()
    requested_article_id = str(selection.get("target_article_id") or "").strip()
    if requested_entity_id and requested_entity_id != str(anchor.get("entity_id") or ""):
        errors.append("graph_anchor_entity_id_mismatch")
    if requested_article_id and requested_article_id != str(anchor.get("article_id") or ""):
        errors.append("graph_anchor_article_id_mismatch")

    type_status = graph_topic_type_status(selection, context)
    if type_status["status"] != "ok":
        errors.append("graph_topic_type_mismatch")

    seed_era = normalized_era(selection.get("era"))
    anchor_era = normalized_era(anchor.get("historical_period") or anchor.get("era"))
    if seed_era not in {"", "기타"} and anchor_era not in {"", "기타"} and seed_era != anchor_era:
        errors.append("graph_anchor_era_mismatch")

    return {
        **type_status,
        "status": "ok" if not errors else "needs_review",
        "errors": errors,
        "target_entity_id": str(anchor.get("entity_id") or ""),
        "target_article_id": str(anchor.get("article_id") or ""),
        "hanja": str(anchor.get("hanja") or ""),
        "category": str(anchor.get("category_text") or ""),
        "description": str(anchor.get("description") or ""),
        "seed_era": seed_era,
        "anchor_era": anchor_era,
    }


def graph_fact_slots(plan: dict[str, Any]) -> list[str]:
    """검색 계획의 근거 슬롯 중 Graph가 직접 제공할 수 있는 슬롯을 반환한다."""
    graph_slots = {
        "identity_clue",
        "definition",
        "core_feature",
        "answer_basis",
        "inquiry_target",
        "order_basis",
        "during_fact",
        "target_event",
        "target_between",
        "position_basis",
        "effect_basis",
    }
    return [slot for slot in plan.get("slots", []) if slot in graph_slots]


def graph_sources(context: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Graph anchor 사실을 공통 RAG source record 목록으로 변환한다."""
    return [
        {
            "chunk_id": f"neo4j:{context.get('anchor_label', 'unknown')}",
            "source_type": "neo4j_graph_fact",
            "title": f"{context.get('anchor_label', 'Graph')} fact",
            "score": 1.0,
            "snippet": fact,
            "retrieval_slots": graph_fact_slots(plan),
        }
        for fact in context.get("graph_facts", [])
        if fact
    ]


def graph_timeline_sources(
    driver: Any,
    selection: dict[str, Any],
    context: dict[str, Any],
    plan: dict[str, Any],
    limit_per_phase: int = 3,
) -> list[dict[str, Any]]:
    """Graph에서 순서·연표·두 사건 사이 문제에 필요한 사건 근거를 조회한다."""
    if driver is None or context.get("anchor_label") != "Term":
        return []
    span = graph_anchor_year_span(context)
    if span is None:
        return []
    start_year, end_year = span
    term_id = graph_anchor_term_id(context)
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (seed:Term)
            WHERE ($term_id <> '' AND seed.term_id = $term_id) OR seed.name = $topic
            WITH seed
            ORDER BY CASE WHEN $term_id <> '' AND seed.term_id = $term_id THEN 0 ELSE 1 END
            LIMIT 1
            OPTIONAL MATCH (seed)-[:IN_ERA]->(seed_era:Era)
            OPTIONAL MATCH (seed)-[:HAS_CATEGORY]->(seed_cat:CanonicalCategory)
            WITH seed,
                 collect(DISTINCT seed_era.name) AS seed_eras,
                 collect(DISTINCT seed_cat.category_path) AS seed_categories,
                 toInteger($start_year) AS sy,
                 toInteger($end_year) AS ey
            MATCH (t:Term)
            WHERE t.name <> seed.name
              AND t.start_year IS NOT NULL
              AND t.description IS NOT NULL
            OPTIONAL MATCH (t)-[:IN_ERA]->(era:Era)
            OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
            WITH t, sy, ey, seed_eras, seed_categories,
                 collect(DISTINCT era.name) AS eras,
                 collect(DISTINCT cat.category_path) AS categories
            WITH t, sy, ey, eras, categories,
                 size([x IN eras WHERE x IN seed_eras]) AS era_overlap,
                 size([x IN categories WHERE x IN seed_categories]) AS category_overlap
            WHERE era_overlap > 0 OR category_overlap > 0 OR abs(toInteger(t.start_year) - sy) <= 80
            WITH t, eras, categories, era_overlap, category_overlap,
                 CASE
                   WHEN coalesce(toInteger(t.end_year), toInteger(t.start_year)) < sy THEN 'before'
                   WHEN toInteger(t.start_year) > ey THEN 'after'
                   ELSE 'during'
                 END AS phase,
                 CASE
                   WHEN coalesce(toInteger(t.end_year), toInteger(t.start_year)) < sy THEN sy - coalesce(toInteger(t.end_year), toInteger(t.start_year))
                   WHEN toInteger(t.start_year) > ey THEN toInteger(t.start_year) - ey
                   ELSE 0
                 END AS distance
            ORDER BY phase, category_overlap DESC, era_overlap DESC, distance ASC, t.name
            RETURN phase,
                   collect({
                     name: t.name,
                     description: t.description,
                     start_year: t.start_year,
                     end_year: t.end_year,
                     year_text: t.year_text,
                     categories: categories,
                     eras: eras,
                     era_overlap: era_overlap,
                     category_overlap: category_overlap,
                     distance: distance
                   })[0..$limit_per_phase] AS items
            """,
            topic=selection["topic"],
            term_id=term_id,
            start_year=int(start_year),
            end_year=int(end_year),
            limit_per_phase=limit_per_phase,
        ).data()

    sources: list[dict[str, Any]] = []
    phase_names = {"before": "이전", "during": "동시기", "after": "이후"}
    for row in rows:
        phase = row["phase"]
        for index, item in enumerate(row["items"], start=1):
            if not timeline_item_allowed(item):
                continue
            year = item.get("year_text") or f"{item.get('start_year', '')}-{item.get('end_year', '')}".strip("-")
            snippet = compact(f"{phase_names.get(phase, phase)} 사실: {item['name']}({year}) - {item['description']}", 360)
            sources.append(
                {
                    "chunk_id": f"neo4j:timeline:{phase}:{index}:{item['name']}",
                    "source_type": "neo4j_timeline_fact",
                    "title": f"{phase_names.get(phase, phase)} timeline fact",
                    "score": 1.0,
                    "snippet": snippet,
                    "retrieval_slot": phase,
                    "retrieval_slots": [TIMELINE_SLOT_BY_INTENT.get(plan.get("intent"), {}).get(phase, phase)],
                }
            )
    return sources


def one_line_basis(target: str, description: str | None, events: list[str] | None = None) -> str:
    """후보 설명이나 연결 사건을 오답 근거 한 문장으로 축약한다."""
    if description:
        return compact(description, 260)
    clean_events = [clean_graph_name(name) for name in (events or []) if clean_graph_name(name)]
    if clean_events:
        return compact(f"{target}은/는 {', '.join(clean_events[:3])} 등을 포함하는 사건군이다.", 260)
    return compact(f"{target}에 대한 그래프 DB 후보이다.", 260)


def valid_distractor_target(target: str, topic: str) -> bool:
    """빈 값·자기 자신·형식 노이즈 후보를 오답 대상에서 제외한다."""
    compact_target = re.sub(r"\s+", "", target.strip())
    compact_topic = re.sub(r"\s+", "", topic.strip())
    if len(compact_target) < 2:
        return False
    if compact_target == compact_topic or compact_target in compact_topic:
        return False
    if re.fullmatch(r"\d+[가-힣A-Za-z]*", compact_target):
        return False
    return True


def graph_anchor_term_id(context: dict[str, Any]) -> str:
    """context anchor의 안정적인 Term ID를 반환한다."""
    return str(context.get("anchor", {}).get("term_id") or "").strip()


def graph_anchor_year(context: dict[str, Any]) -> int | None:
    """context anchor에서 대표 연도 하나를 읽는다."""
    span = graph_anchor_year_span(context)
    return span[0] if span else None


def graph_anchor_year_span(context: dict[str, Any]) -> tuple[int, int] | None:
    """context anchor의 시작·종료 연도를 정규화된 범위로 반환한다."""
    anchor = context.get("anchor", {})
    start = anchor.get("start_year")
    end = anchor.get("end_year")
    if start is None:
        text = " ".join(str(anchor.get(key) or "") for key in ("description", "year_text", "period_text"))
        match = re.search(r"(\d{1,4})\s*[-~]\s*(\d{1,4})\s*년", text)
        if match:
            start = int(match.group(1))
            end = end if end is not None else int(match.group(2))
        else:
            years = [int(value) for value in re.findall(r"(?<!\d)(\d{3,4})\s*년", text)]
            if years:
                start, end = min(years), max(years)
    if start is None and end is None:
        return None
    start = int(start if start is not None else end)
    end = int(end if end is not None else start)
    return (min(start, end), max(start, end))


def category_parts(path: str) -> list[str]:
    """계층형 category_path를 의미 있는 단계 목록으로 분리한다."""
    return [part.strip() for part in str(path or "").split(">") if part.strip()]


def category_axis_score(seed_categories: list[str], candidate_categories: list[str]) -> int:
    """seed와 후보의 세부 category 경로 중첩 정도를 점수화한다."""
    score = 0
    for seed_category in seed_categories:
        seed_parts = category_parts(seed_category)
        if not seed_parts:
            continue
        for candidate_category in candidate_categories:
            candidate_parts = category_parts(candidate_category)
            if not candidate_parts:
                continue
            if seed_parts == candidate_parts:
                score = max(score, 80)
                continue
            prefix = 0
            for left, right in zip(seed_parts, candidate_parts, strict=False):
                if left != right:
                    break
                prefix += 1
            if prefix:
                score = max(score, min(65, 15 + prefix * 20))
            if seed_parts[-1] == candidate_parts[-1]:
                score = max(score, 55)
    return score


SEMANTIC_RULES = (
    (
        "문헌:역사서",
        ("역사서", "역사책", "통사서", "사서(史書)", "편년체", "기전체", "정사(正史)", "실록", "역사를기록", "역사를서술", "역사를수록", "역사시"),
    ),
    ("문헌:법전", ("법전", "법령", "율령", "조례", "법규")),
    ("문헌:불교문헌", ("불교서", "불서", "사경", "대장경", "간화선", "화엄", "선종")),
    ("문헌:유교문헌", ("유교의경전", "사서삼경", "경서", "성리학")),
    ("문헌:문집", ("문집", "시문집", "유고", "시문")),
    ("문헌:지리서", ("지리지", "지리서", "지도")),
    ("문헌:의학서", ("의서", "의학서", "의학")),
    ("인물:왕", ("재위", "왕으로", "왕이다", "왕위", "국왕", "군주")),
    ("인물:불교", ("승려", "고승", "선사", "국사", "대사")),
    ("인물:학자", ("학자", "문신", "실학자", "성리학자")),
    ("인물:무장", ("장군", "무신", "의병장", "독립군", "군인")),
    ("인물:독립운동", ("독립운동가", "독립운동")),
    ("제도:토지수취", ("토지제도", "수취", "전세", "공납", "군역", "녹읍")),
    ("제도:교육", ("교육기관", "학교", "교육")),
    ("제도:군사", ("군사제도", "군사조직", "군영", "부대", "군대", "중앙군", "오군", "국방·군사")),
    ("제도:의례풍속", ("제천의식", "제사", "제의", "의례", "풍속", "명절행사", "농경의례", "혼인풍속")),
    ("제도:행정", ("행정", "관청", "관제", "중앙정치", "지방행정", "기구")),
    ("문화재:탑", ("석탑", "불탑", "탑")),
    ("문화재:비석", ("비석", "비갈", "세운비", "기념물", "碑")),
    ("문화재:기록유산", ("대장경", "판본", "목판", "책판", "문서", "기록물")),
    ("문화재:불상", ("불상", "보살상", "여래상")),
    ("문화재:건축", ("궁궐", "궁.", "사찰", "건축", "전각", "성곽")),
    ("문화재:회화", ("불화", "초상화", "회화", "그림")),
    ("문화재:무형음악", ("판소리", "민속악", "고수", "북장단", "소리와아니리", "구연")),
    ("문화재:장소유적", ("섬", "전승지", "대첩", "유적지")),
    ("사건:전쟁", ("전쟁", "전투", "왜란", "호란")),
    ("사건:운동", ("운동", "항쟁", "봉기", "의거", "시위")),
)


def semantic_family(selection: dict[str, Any], context: dict[str, Any]) -> str:
    """topic_type과 Graph 메타데이터를 인물·사건·제도 등 비교 family로 묶는다."""
    text = "".join(
        str(value)
        for value in [
            selection.get("topic_type", ""),
            *context.get("entity_types", []),
            *context.get("categories", []),
        ]
    )
    if "문헌" in text or "서명" in text:
        return "문헌:"
    if "인물" in text:
        return "인물:"
    if "제도" in text:
        return "제도:"
    if "기관" in text or "단체" in text or "조직" in text or "기구" in text:
        return "제도:"
    if "문화재" in text or "유적" in text:
        return "문화재:"
    if "사건" in text:
        return "사건:"
    return ""


def semantic_tags(parts: list[Any], family: str = "") -> set[str]:
    """후보 설명과 분류에서 family 비교에 사용할 의미 태그를 만든다."""
    text = re.sub(r"\s+", "", " ".join(str(part) for part in parts if part))
    return {
        label
        for label, keywords in SEMANTIC_RULES
        if (not family or label.startswith(family)) and any(keyword in text for keyword in keywords)
    }


@lru_cache(maxsize=1)
def keyword_topic_scores() -> dict[str, int]:
    """대표 토픽 가중치 파일을 읽어 이름별 시험 중요도 점수로 캐시한다."""
    if not DEFAULT_TOPIC_POOL.exists():
        return {}
    scores: dict[str, int] = {}
    with DEFAULT_TOPIC_POOL.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            name = re.sub(r"\s+", "", row.get("topic") or row.get("term_name") or "")
            if not name:
                continue
            score = int(float(row.get("pdf_score") or 0)) + int(float(row.get("source_weight") or 0))
            scores[name] = max(scores.get(name, 0), score)
    return scores


def representative_score(row: dict[str, Any]) -> int:
    """시험 키워드·question_ready·빈도 메타데이터로 후보 대표성을 계산한다."""
    target = re.sub(r"\s+", "", str(row.get("target") or ""))
    return int(row.get("exam_score") or 0) * 4 + keyword_topic_scores().get(target, 0)


def representative_category_candidates(
    session: Any,
    selection: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """같은 세부 category에서 시험 대표성이 있는 Term 후보를 조회한다."""
    names = list(keyword_topic_scores())
    if not names or context.get("anchor_label") != "Term":
        return []
    return session.run(
        """
        MATCH (seed:Term)
        WHERE ($term_id <> '' AND seed.term_id = $term_id) OR seed.name = $topic
        WITH seed
        ORDER BY CASE WHEN $term_id <> '' AND seed.term_id = $term_id THEN 0 ELSE 1 END
        LIMIT 1
        MATCH (seed)-[:HAS_CATEGORY]->(seed_cat:CanonicalCategory)
        OPTIONAL MATCH (seed)-[:IN_ERA]->(seed_era:Era)
        WITH seed, seed.start_year AS seed_year, collect(DISTINCT seed_cat.category_path) AS seed_categories,
             collect(DISTINCT seed_era.name) AS seed_eras
        MATCH (t:Term)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
        WHERE t.name <> seed.name
          AND t.description IS NOT NULL
          AND replace(t.name, ' ', '') IN $names
          AND size(replace(t.name, ' ', '')) >= 3
        WITH t, seed_year, seed_categories, seed_eras, collect(DISTINCT cat.category_path) AS categories
        WHERE any(category IN categories WHERE category IN seed_categories)
          AND NOT ('인명' IN seed_categories AND '인명' IN categories)
        OPTIONAL MATCH (t)-[:IN_ERA]->(te:Era)
        WITH t, seed_year, seed_eras, categories, collect(DISTINCT te.name) AS eras
        RETURN t.name AS target, t.description AS description, t.period_text AS period_text, eras, categories,
               2 AS relation_depth,
               size([x IN eras WHERE x IN seed_eras]) AS era_overlap,
               ['keyword_category'] AS routes,
               35 AS graph_score,
               CASE WHEN t.question_ready = 'Y' THEN 2 ELSE 0 END
               + CASE WHEN t.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score,
               CASE WHEN seed_year IS NULL OR t.start_year IS NULL THEN 999999
                    ELSE abs(toInteger(t.start_year) - toInteger(seed_year)) END AS year_gap,
               categories AS route_details,
               'representative_same_category_term' AS relation_reason
        """,
        topic=selection["topic"],
        term_id=graph_anchor_term_id(context),
        names=names,
    ).data()


def temporal_distractor_candidates(
    session: Any,
    selection: dict[str, Any],
    context: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """정답 연도와의 상대적 거리로 비교 가능한 시대 후보를 조회한다."""
    span = graph_anchor_year_span(context)
    if span is None or not retrieval_plan(selection).get("needs_timeline"):
        return []
    start_year, end_year = span
    anchor_year = start_year
    outside_only = retrieval_plan(selection).get("intent") == "timeline_compare"
    return session.run(
        """
        MATCH (seed:Term)
        WHERE ($term_id <> '' AND seed.term_id = $term_id) OR seed.name = $topic
        WITH seed
        ORDER BY CASE WHEN $term_id <> '' AND seed.term_id = $term_id THEN 0 ELSE 1 END
        LIMIT 1
        OPTIONAL MATCH (seed)-[:IN_ERA]->(seed_era:Era)
        WITH seed, collect(DISTINCT seed_era.name) AS seed_eras
        MATCH (t:Term)
        WHERE t.name <> seed.name
          AND t.start_year IS NOT NULL
          AND t.description IS NOT NULL
          AND abs(toInteger(t.start_year) - $anchor_year) <= 180
          AND (
            $outside_only = false
            OR coalesce(toInteger(t.end_year), toInteger(t.start_year)) < $start_year
            OR toInteger(t.start_year) > $end_year
          )
        OPTIONAL MATCH (t)-[:IN_ERA]->(era:Era)
        OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
        WITH t, seed_eras,
             collect(DISTINCT era.name) AS eras,
             collect(DISTINCT cat.category_path) AS categories
        RETURN t.name AS target, t.description AS description, t.period_text AS period_text, eras, categories,
               2 AS relation_depth,
               size([x IN eras WHERE x IN seed_eras]) AS era_overlap,
               ['timeline_near'] AS routes,
               45 + size([x IN eras WHERE x IN seed_eras]) * 5 AS graph_score,
               CASE WHEN t.question_ready = 'Y' THEN 2 ELSE 0 END
               + CASE WHEN t.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score,
               abs(toInteger(t.start_year) - $anchor_year) AS year_gap,
               eras AS route_details,
               'near_timeline_term' AS relation_reason
        ORDER BY exam_score DESC, year_gap ASC, target
        LIMIT $limit
        """,
        topic=selection["topic"],
        term_id=graph_anchor_term_id(context),
        anchor_year=int(anchor_year),
        start_year=int(start_year),
        end_year=int(end_year),
        outside_only=outside_only,
        limit=limit,
    ).data()


def era_distractor_candidates(
    session: Any,
    selection: dict[str, Any],
    context: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """같거나 인접한 시대에서 category·theme가 겹치는 후보를 조회한다."""
    eras = [str(value) for value in context.get("eras", []) if value]
    if not eras or retrieval_plan(selection).get("needs_timeline"):
        return []
    return session.run(
        """
        MATCH (t:Term)-[:IN_ERA]->(era:Era)
        WHERE t.name <> $topic
          AND t.description IS NOT NULL
          AND era.name IN $eras
        OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
        RETURN t.name AS target, t.description AS description, t.period_text AS period_text,
               collect(DISTINCT cat.category_path) AS categories,
               3 AS relation_depth,
               1 AS era_overlap,
               ['era'] AS routes,
               20 AS graph_score,
               CASE WHEN t.question_ready = 'Y' THEN 2 ELSE 0 END
               + CASE WHEN t.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score,
               999999 AS year_gap,
               $eras AS route_details,
               'same_era_term_fallback' AS relation_reason
        ORDER BY exam_score DESC, target
        LIMIT $limit
        """,
        topic=selection["topic"],
        eras=eras,
        limit=limit,
    ).data()


def answer_axis(selection: dict[str, Any], context: dict[str, Any]) -> str:
    """출제 유형과 Graph anchor에서 오답이 맞춰야 할 사실축을 결정한다."""
    task = str(selection.get("question_task") or "")
    categories = [str(value) for value in context.get("categories", []) if value]
    if task == "order":
        return f"순서/{categories[0]}" if categories else "순서"
    if task in {"timeline_position", "period_between"}:
        return f"시기/{categories[0]}" if categories else "시기"
    if categories:
        return categories[0]
    return str(selection.get("topic_type") or "기타")


def graph_axis_score(selection: dict[str, Any], context: dict[str, Any], row: dict[str, Any]) -> int:
    """후보가 정답과 같은 Graph 관계축을 공유하는 정도를 계산한다."""
    categories = [str(value) for value in row.get("categories", []) if value]
    score = category_axis_score([str(value) for value in context.get("categories", []) if value], categories)
    if selection.get("topic_type") == "인물" and "person_related" in set(row.get("routes", [])):
        score = max(score, 80)
    elif selection.get("topic_type") == "사건" and "event_group" in set(row.get("routes", [])):
        score = max(score, 80)
    elif "timeline_near" in set(row.get("routes", [])) and retrieval_plan(selection).get("needs_timeline"):
        score = max(score, 60)
    elif "era" in set(row.get("routes", [])) and context.get("eras"):
        score = max(score, 30)
    elif generic_person_root_candidate(selection, context, row):
        score = min(score, 20)
    return score


def generic_person_root_candidate(selection: dict[str, Any], context: dict[str, Any], row: dict[str, Any]) -> bool:
    """세부 관계가 없는 범용 인물 root 후보인지 판정한다."""
    if selection.get("topic_type") != "인물":
        return False
    if "person_related" in set(row.get("routes", [])):
        return False
    seed_categories = {str(value) for value in context.get("categories", []) if value}
    candidate_categories = {str(value) for value in row.get("categories", []) if value}
    return "인명" in seed_categories and "인명" in candidate_categories


def semantic_axis_score(
    selection: dict[str, Any],
    context: dict[str, Any],
    row: dict[str, Any],
) -> tuple[int, set[str], set[str]]:
    """후보 설명·분류 태그가 문제의 의미 family와 맞는 정도를 계산한다."""
    family = semantic_family(selection, context)
    if not family:
        return 0, set(), set()
    answer_tags = semantic_tags(
        [
            context.get("anchor", {}).get("description", ""),
            *context.get("graph_facts", []),
            *selection.get("answer_fact_basis", []),
            *context.get("categories", []),
            *context.get("entity_types", []),
        ],
        family,
    )
    candidate_tags = semantic_tags(
        [
            row.get("target", ""),
            row.get("description", ""),
            *row.get("categories", []),
            *row.get("route_details", []),
        ],
        family,
    )
    if not answer_tags:
        return 0, answer_tags, candidate_tags
    if answer_tags & candidate_tags:
        return 50, answer_tags, candidate_tags
    if candidate_tags:
        return -100, answer_tags, candidate_tags
    return -20, answer_tags, candidate_tags


def compatible_topic_type(actual: str, expected: str) -> bool:
    """서로 다른 표기지만 같은 비교 대상으로 허용되는 topic_type인지 확인한다."""
    if not expected:
        return True
    if actual == expected:
        return True
    if {actual, expected} <= {"문헌", "매체"}:
        return True
    return {actual, expected} <= {"문화", "문화유산"}


def candidate_expected_topic_type(row: dict[str, Any]) -> str:
    """후보 row의 entity/category 메타데이터에서 topic_type을 추론한다."""
    return graph_expected_topic_type(
        {
            "anchor_label": "Term",
            "anchor": {
                "category_text": " ".join(str(value) for value in row.get("categories", [])),
                "description": row.get("description", ""),
            },
            "categories": row.get("categories", []),
            "entity_types": [],
        }
    )


def timeline_item_allowed(item: dict[str, Any]) -> bool:
    """후보 근거가 연표 문항에 사용할 수 있는 사건·시기 사실인지 검사한다."""
    if ("era_overlap" in item or "category_overlap" in item) and int(item.get("era_overlap") or 0) <= 0 and int(item.get("category_overlap") or 0) <= 0:
        return False
    candidate_type = candidate_expected_topic_type(
        {
            "target": item.get("name") or item.get("target", ""),
            "description": item.get("description", ""),
            "categories": item.get("categories", []),
        }
    )
    return candidate_type != "인물"


def rerank_for_question_axis(
    rows: list[dict[str, Any]],
    selection: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Graph·의미·시대·대표성 점수를 합쳐 문제축에 맞는 후보 순서를 만든다."""
    ranked: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        plan = retrieval_plan(selection)
        item["answer_axis"] = answer_axis(selection, context)
        semantic_score, answer_tags, candidate_tags = semantic_axis_score(selection, context, row)
        if set(row.get("routes", [])) & {"timeline_near", "event_group"} and semantic_score < 0:
            semantic_score = 0
        rep_score = representative_score(row)
        easy_bonus = rep_score if selection.get("difficulty_label") == "쉬움" else rep_score // 3
        generic_penalty = 0
        if generic_person_root_candidate(selection, context, row):
            easy_bonus = min(easy_bonus, 20)
            generic_penalty = -60
        candidate_type = candidate_expected_topic_type(row)
        type_mismatch_penalty = 0
        routes = set(row.get("routes", []))
        if not plan.get("needs_timeline") and not compatible_topic_type(
            str(selection.get("topic_type") or ""),
            candidate_type,
        ):
            type_mismatch_penalty = -999
        if plan.get("intent") == "timeline_compare" and int(row.get("year_gap") or 999999) >= 999999 and "timeline_near" not in routes:
            type_mismatch_penalty = -999
        if "era" in routes and semantic_score < 0:
            type_mismatch_penalty = -999
        if not plan.get("needs_timeline") and context.get("eras") and "keyword_category" in routes and int(row.get("era_overlap") or 0) <= 0:
            generic_penalty -= 80
        item["semantic_score"] = semantic_score
        item["answer_semantic_tags"] = sorted(answer_tags)
        item["semantic_tags"] = sorted(candidate_tags)
        item["representative_score"] = rep_score
        item["generic_category_penalty"] = generic_penalty
        item["candidate_topic_type"] = candidate_type
        item["type_mismatch_penalty"] = type_mismatch_penalty
        item["axis_score"] = max(
            0,
            graph_axis_score(selection, context, row)
            + semantic_score
            + easy_bonus
            + generic_penalty
            + type_mismatch_penalty,
        )
        ranked.append(item)
    return sorted(
        ranked,
        key=lambda row: (
            -int(row.get("axis_score") or 0),
            -int(row.get("graph_score") or 0),
            -int(row.get("exam_score") or 0),
            int(row.get("year_gap") or 999999),
            int(row.get("relation_depth") or 3),
            str(row.get("target") or ""),
        ),
    )


def retrieve_distractor_sources(
    retriever: PgVectorHybridRetriever,
    selection: dict[str, Any],
    target: str,
    fallback_basis: str,
    top_k: int,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """선택한 후보 owner 범위 안에서만 pgvector 오답 근거를 조회한다."""
    metadata = metadata or {}
    metadata_fields = {
        key: metadata[key]
        for key in (
            "relation_reason",
            "graph_score",
            "exam_score",
            "year_gap",
            "graph_routes",
            "answer_axis",
            "axis_score",
            "semantic_score",
            "answer_semantic_tags",
            "semantic_tags",
            "representative_score",
            "generic_category_penalty",
            "candidate_topic_type",
            "candidate_era",
            "type_mismatch_penalty",
        )
        if key in metadata
    }
    fallback = {
        "target": target,
        "chunk_id": f"neo4j:distractor:{target}",
        "source_type": "neo4j_graph_fact",
        "title": f"{target} fact",
        "score": 1.0,
        "snippet": fallback_basis,
        **metadata_fields,
    }
    if top_k <= 0:
        return [fallback]
    results = [
        {
            "target": target,
            "chunk_id": item["chunk_id"],
            "source_type": item["source_type"],
            "title": item["title"],
            "score": item["score"],
            "snippet": item["snippet"],
            **metadata_fields,
        }
        for item in (
            result_to_payload(result)
            for result in retriever.search(distractor_query(selection, target), top_k=max(top_k * 4, top_k))
        )
    ]
    matched = [item for item in results if text_mentions(f"{item['title']} {item['snippet']}", [target])]
    if matched:
        return matched[:top_k]
    return [fallback]


def distractor_basis_status(
    targets: list[dict[str, Any]],
    basis_list: list[list[dict[str, Any]]],
    expected_count: int,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """후보 수·owner·근거 존재 여부와 난이도 적합성을 한 번에 검사한다."""
    missing_basis = [
        target.get("term_name", "")
        for target, basis in zip(targets, basis_list, strict=False)
        if not any(str(item.get("snippet") or item.get("description") or "").strip() for item in basis)
    ]
    weak_targets = [target.get("term_name", "") for target in targets if weak_distractor_target(target)]
    weak_target_details = [
        {
            "term_name": target.get("term_name", ""),
            "relation_reason": target.get("relation_reason", ""),
            "axis_score": target.get("axis_score", 0),
            "graph_score": target.get("graph_score", 0),
            "candidate_topic_type": target.get("candidate_topic_type", ""),
            "candidate_era": target.get("candidate_era", ""),
        }
        for target in targets
        if weak_distractor_target(target)
    ]
    target_score = target_score_from_selection(selection)
    effective_targets = [target.get("term_name", "") for target in targets if effective_distractor_target(target, target_score)]
    required_effective = {2: 2, 3: 4}.get(target_score, 0)
    missing_count = max(expected_count - len(targets), 0)
    weak_limit = {1: 2, 2: 2, 3: 0}.get(target_score, 0)
    effective_shortage = max(required_effective - len(effective_targets), 0)
    return {
        "status": "ok"
        if missing_count == 0 and not missing_basis and len(weak_targets) <= weak_limit and effective_shortage == 0
        else "needs_review",
        "target_count": len(targets),
        "expected_count": expected_count,
        "missing_count": missing_count,
        "missing_basis": missing_basis,
        "weak_targets": weak_targets,
        "weak_target_details": weak_target_details,
        "weak_limit": weak_limit,
        "target_score": target_score or None,
        "effective_targets": effective_targets,
        "required_effective_targets": required_effective,
        "effective_shortage": effective_shortage,
        "relation_reasons": [target.get("relation_reason", "") for target in targets],
    }


def weak_distractor_target(target: dict[str, Any]) -> bool:
    """fallback·범용 category·낮은 축 점수 후보를 약한 오답 대상으로 판정한다."""
    reason = str(target.get("relation_reason") or "")
    return (
        "fallback" in reason
        or ("no_era" in reason and normalized_era(target.get("candidate_era")) in {"", "기타"})
        or reason == "same_era_root_category_term"
        or int(target.get("axis_score") or 0) < 50
        or (reason == "representative_same_category_term" and int(target.get("year_gap") or 999999) >= 999999)
    )


def effective_distractor_target(target: dict[str, Any], target_score: int) -> bool:
    """목표 난이도에서 실제 사용할 수 있는 충분히 가까운 후보인지 판정한다."""
    if weak_distractor_target(target):
        return False
    if target_score != 3:
        return True
    if normalized_era(target.get("candidate_era")) in {"", "기타"}:
        return False
    reason = str(target.get("relation_reason") or "")
    return int(target.get("axis_score") or 0) >= 80 and reason in {
        "shared_node_sibling",
        "person_related_term",
        "taxonomy_facet_term",
        "same_era_detail_category_term",
        "near_timeline_term",
        "representative_same_category_term",
    }


def _legacy_retrieve_distractor_basis(
    driver: Any,
    retriever: PgVectorHybridRetriever,
    selection: dict[str, Any],
    context: dict[str, Any],
    target_count: int,
    basis_top_k: int,
    encykorea_api_key: str = "",
    timeout: int = 60,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """여러 Graph route를 합치고 rerank한 뒤 후보별 RAG 근거를 조회하는 활성 legacy 구현."""
    if driver is None or target_count <= 0:
        return [], []

    topic = selection["topic"]
    anchor_label = context.get("anchor_label")
    term_id = graph_anchor_term_id(context)
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
            rows = [
                {
                    **row,
                    "period_text": row.get("term_period") or row.get("event_era") or "",
                    "categories": row.get("candidate_categories") or [],
                    "era_overlap": 1,
                    "routes": ["event_group"],
                    "graph_score": 80 + int(row.get("category_overlap") or 0) * 10,
                    "exam_score": 0,
                    "year_gap": 0,
                    "route_details": row.get("candidate_categories") or [],
                }
                for row in rows
            ]
        elif anchor_label == "Term":
            rows = session.run(
                """
                MATCH (seed:Term)
                WHERE ($term_id <> '' AND seed.term_id = $term_id) OR seed.name = $topic
                WITH seed
                ORDER BY CASE WHEN $term_id <> '' AND seed.term_id = $term_id THEN 0 ELSE 1 END
                LIMIT 1
                OPTIONAL MATCH (seed)-[:IN_ERA]->(era:Era)
                WITH seed, collect(DISTINCT era.name) AS graph_eras, seed.start_year AS seed_year
                WITH seed, [x IN graph_eras + [$selection_era] WHERE x IS NOT NULL AND x <> ''] AS seed_eras, seed_year
                CALL {
                    WITH seed, seed_eras, seed_year
                    MATCH (seed)-[:REFERS_TO]->(:Person)-[rel:RELATED_TO]-(:Person)<-[:REFERS_TO]-(t:Term)
                    WHERE t.name <> seed.name AND t.description IS NOT NULL
                    OPTIONAL MATCH (t)-[:IN_ERA]->(te:Era)
                    OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
                    WITH t, seed_eras, seed_year, collect(DISTINCT cat.category_path) AS categories,
                         collect(DISTINCT te.name) AS eras,
                         collect(DISTINCT coalesce(rel.raw_relation_type, rel.normalized_relation_type, rel.relation_group, type(rel))) AS details,
                         max(CASE rel.relation_group
                             WHEN 'SOCIAL' THEN 18
                             WHEN 'SOCIAL_TEACHER' THEN 15
                             WHEN 'SOCIAL_STUDENT' THEN 15
                             WHEN 'FAMILY_SIBLING' THEN 8
                             WHEN 'FAMILY_PARENT' THEN 4
                             WHEN 'FAMILY_CHILD' THEN 4
                             WHEN 'SPOUSE' THEN 4
                             ELSE 0
                         END) AS relation_bonus
                    WITH t, categories, 1 AS relation_depth, size([x IN eras WHERE x IN seed_eras]) AS era_overlap,
                         seed_eras, seed_year, details, relation_bonus
                    RETURN t, categories, relation_depth, era_overlap, 'person_related' AS route,
                           70 + relation_bonus + era_overlap * 10 AS graph_score, details AS route_details,
                           CASE WHEN t.question_ready = 'Y' THEN 2 ELSE 0 END
                           + CASE WHEN t.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score,
                           CASE WHEN seed_year IS NULL OR t.start_year IS NULL THEN 999999
                                ELSE abs(toInteger(t.start_year) - toInteger(seed_year)) END AS year_gap
                    UNION
                    WITH seed, seed_eras, seed_year
                    MATCH (seed)-[:ABOUT_TAXONOMY_FACET]->(facet:TaxonomyFacet)<-[:ABOUT_TAXONOMY_FACET]-(t:Term)
                    WHERE t.name <> seed.name AND t.description IS NOT NULL
                    OPTIONAL MATCH (t)-[:IN_ERA]->(te:Era)
                    OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
                    WITH t, seed_eras, seed_year, collect(DISTINCT cat.category_path) AS categories,
                         collect(DISTINCT te.name) AS eras,
                         collect(DISTINCT facet.name) AS details
                    WITH t, categories, 1 AS relation_depth, size([x IN eras WHERE x IN seed_eras]) AS era_overlap,
                         seed_eras, seed_year, details
                    WHERE size(seed_eras) = 0 OR era_overlap > 0
                    RETURN t, categories, relation_depth, era_overlap, 'taxonomy_facet' AS route,
                           62 + era_overlap * 10 AS graph_score, details AS route_details,
                           CASE WHEN t.question_ready = 'Y' THEN 2 ELSE 0 END
                           + CASE WHEN t.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score,
                           CASE WHEN seed_year IS NULL OR t.start_year IS NULL THEN 999999
                                ELSE abs(toInteger(t.start_year) - toInteger(seed_year)) END AS year_gap
                    UNION
                    WITH seed, seed_eras, seed_year
                    MATCH (seed)-[:HAS_CATEGORY]->(cat:CanonicalCategory)<-[:HAS_CATEGORY]-(t:Term)
                    WHERE t.name <> seed.name AND t.description IS NOT NULL
                    OPTIONAL MATCH (t)-[:IN_ERA]->(te:Era)
                    WITH t, seed_eras, seed_year, collect(DISTINCT cat.category_path) AS categories,
                         collect(DISTINCT te.name) AS eras,
                         max(CASE
                             WHEN coalesce(cat.depth, size(split(coalesce(cat.category_path, ''), '>'))) > 1 THEN 1
                             ELSE 0
                         END) AS has_detail
                    WITH t, categories, 1 AS relation_depth, size([x IN eras WHERE x IN seed_eras]) AS era_overlap,
                         seed_eras, seed_year, has_detail
                    WHERE size(seed_eras) = 0 OR era_overlap > 0 OR (has_detail = 0 AND NOT '인명' IN categories)
                    RETURN t, categories, relation_depth, era_overlap,
                           CASE WHEN has_detail = 1 THEN 'category_detail' ELSE 'category_root' END AS route,
                           CASE WHEN has_detail = 1 THEN 68 ELSE 10 END + era_overlap * 10 AS graph_score,
                           categories AS route_details,
                           CASE WHEN t.question_ready = 'Y' THEN 2 ELSE 0 END
                           + CASE WHEN t.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score,
                           CASE WHEN seed_year IS NULL OR t.start_year IS NULL THEN 999999
                                ELSE abs(toInteger(t.start_year) - toInteger(seed_year)) END AS year_gap
                    UNION
                    WITH seed, seed_eras, seed_year
                    MATCH (seed)-[:HAS_THEME]->(theme:Theme)<-[:HAS_THEME]-(t:Term)
                    WHERE t.name <> seed.name AND t.description IS NOT NULL
                    OPTIONAL MATCH (t)-[:IN_ERA]->(te:Era)
                    OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
                    WITH t, seed_eras, seed_year, collect(DISTINCT cat.category_path) AS categories,
                         collect(DISTINCT te.name) AS eras,
                         collect(DISTINCT theme.name) AS details
                    WITH t, categories, 2 AS relation_depth, size([x IN eras WHERE x IN seed_eras]) AS era_overlap,
                         seed_eras, seed_year, details
                    WHERE size(seed_eras) = 0 OR era_overlap > 0
                    RETURN t, categories, relation_depth, era_overlap, 'theme' AS route,
                           12 + era_overlap * 5 AS graph_score, details AS route_details,
                           CASE WHEN t.question_ready = 'Y' THEN 2 ELSE 0 END
                           + CASE WHEN t.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score,
                           CASE WHEN seed_year IS NULL OR t.start_year IS NULL THEN 999999
                                ELSE abs(toInteger(t.start_year) - toInteger(seed_year)) END AS year_gap
                }
                WITH t, categories, relation_depth, era_overlap, route, graph_score, route_details, exam_score, year_gap
                ORDER BY graph_score DESC, exam_score DESC, year_gap ASC, era_overlap DESC, relation_depth, t.name
                WITH t,
                     collect(categories) AS category_groups,
                     min(relation_depth) AS relation_depth,
                     max(era_overlap) AS era_overlap,
                     collect(DISTINCT route) AS routes,
                     max(graph_score) AS graph_score,
                     max(exam_score) AS exam_score,
                     min(year_gap) AS year_gap,
                     collect(route_details) AS route_detail_groups
                RETURN t.name AS target, t.description AS description, t.period_text AS period_text,
                       reduce(acc = [], values IN category_groups | acc + values) AS categories,
                       relation_depth, era_overlap, routes, graph_score, exam_score, year_gap,
                       reduce(acc = [], values IN route_detail_groups | acc + values) AS route_details,
                       CASE
                         WHEN 'person_related' IN routes THEN 'person_related_term'
                         WHEN 'category_detail' IN routes THEN 'same_era_detail_category_term'
                         WHEN 'taxonomy_facet' IN routes THEN 'taxonomy_facet_term'
                         WHEN 'category_root' IN routes THEN 'same_era_root_category_term'
                         ELSE 'same_era_theme_term'
                       END AS relation_reason
                ORDER BY graph_score DESC, exam_score DESC, year_gap ASC, era_overlap DESC, relation_depth, target
                LIMIT $limit
                """,
                topic=topic,
                term_id=term_id,
                selection_era=selection.get("era", ""),
                limit=max(target_count * 60, target_count),
            ).data()
            if not rows:
                rows = session.run(
                    """
                    MATCH (seed:Term)
                    WHERE ($term_id <> '' AND seed.term_id = $term_id) OR seed.name = $topic
                    WITH seed
                    ORDER BY CASE WHEN $term_id <> '' AND seed.term_id = $term_id THEN 0 ELSE 1 END
                    LIMIT 1
                    OPTIONAL MATCH (seed)-[:IN_ERA]->(era:Era)
                    WITH seed, [x IN collect(DISTINCT era.name) + [$selection_era] WHERE x IS NOT NULL AND x <> ''] AS seed_eras
                    MATCH (t:Term)-[:IN_ERA]->(era:Era)
                    WHERE t.name <> seed.name AND t.description IS NOT NULL AND era.name IN seed_eras
                    RETURN t.name AS target, t.description AS description, t.period_text AS period_text,
                           3 AS relation_depth, 1 AS era_overlap, ['era'] AS routes, 4 AS graph_score,
                           CASE WHEN t.question_ready = 'Y' THEN 2 ELSE 0 END
                           + CASE WHEN t.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score,
                           999999 AS year_gap,
                           'same_era_term_fallback' AS relation_reason
                    ORDER BY t.question_ready DESC, t.is_exam_keyword DESC, target
                    LIMIT $limit
                    """,
                    topic=topic,
                    term_id=term_id,
                    selection_era=selection.get("era", ""),
                    limit=max(target_count * 12, target_count),
                ).data()
            if not rows:
                rows = session.run(
                    """
                    MATCH (seed:Term)
                    WHERE ($term_id <> '' AND seed.term_id = $term_id) OR seed.name = $topic
                    WITH seed
                    ORDER BY CASE WHEN $term_id <> '' AND seed.term_id = $term_id THEN 0 ELSE 1 END
                    LIMIT 1
                    CALL {
                        WITH seed
                        MATCH (seed)-[:HAS_CATEGORY]->(cat:CanonicalCategory)<-[:HAS_CATEGORY]-(t:Term)
                        WHERE t.name <> seed.name AND t.description IS NOT NULL
                        WITH t, collect(DISTINCT cat.category_path) AS categories
                        RETURN t, categories,
                               2 AS relation_depth, 0 AS era_overlap, ['category_no_era'] AS routes,
                               45 AS graph_score, categories AS route_details,
                               'same_category_no_era_term' AS relation_reason
                        UNION
                        WITH seed
                        MATCH (seed)-[:HAS_THEME]->(theme:Theme)<-[:HAS_THEME]-(t:Term)
                        WHERE t.name <> seed.name AND t.description IS NOT NULL
                        OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
                        RETURN t, collect(DISTINCT cat.category_path) AS categories,
                               3 AS relation_depth, 0 AS era_overlap, ['theme_no_era'] AS routes,
                               12 AS graph_score, collect(DISTINCT theme.name) AS route_details,
                               'same_theme_no_era_term' AS relation_reason
                    }
                    RETURN t.name AS target, t.description AS description, t.period_text AS period_text, categories, relation_depth,
                           era_overlap, routes, graph_score,
                           CASE WHEN t.question_ready = 'Y' THEN 2 ELSE 0 END
                           + CASE WHEN t.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score,
                           999999 AS year_gap, route_details, relation_reason
                    ORDER BY graph_score DESC, exam_score DESC, target
                    LIMIT $limit
                    """,
                    topic=topic,
                    term_id=term_id,
                    limit=max(target_count * 12, target_count),
                ).data()
            rows.extend(temporal_distractor_candidates(session, selection, context, max(target_count * 100, target_count)))
            rows.extend(era_distractor_candidates(session, selection, context, max(target_count * 20, target_count)))
            rows.extend(representative_category_candidates(session, selection, context))
        rows = rerank_for_question_axis(rows, selection, context)

    targets: list[dict[str, Any]] = []
    basis_list: list[list[dict[str, Any]]] = []
    seen_targets: set[str] = set()
    for row in rows:
        if len(targets) >= target_count:
            break
        target = row.get("target") or ""
        if not target or target in seen_targets or not valid_distractor_target(str(target), topic):
            continue
        if anchor_label == "Term" and int(row.get("axis_score") or 0) <= 0:
            continue
        candidate_period = row.get("term_period") or row.get("period_text") or ""
        if target_score_from_selection(selection) == 3:
            candidate_era = normalized_era(candidate_period)
            if candidate_era in {"", "기타"} or not eras_are_close(selection.get("era"), candidate_period):
                continue
        seen_targets.add(target)
        description = row.get("description")
        if anchor_label == "EventGroup" and row.get("term_period") and row.get("event_era"):
            if row["event_era"] not in row["term_period"]:
                description = None
        basis = one_line_basis(target, description, row.get("events"))
        target_metadata = {
            "term_name": target,
            "term_times": "",
            "description": basis,
            "relation_reason": row.get("relation_reason", ""),
            "graph_score": row.get("graph_score", 0),
            "exam_score": row.get("exam_score", 0),
            "year_gap": row.get("year_gap", 999999),
            "graph_routes": row.get("routes", []),
            "answer_axis": row.get("answer_axis", answer_axis(selection, context)),
            "axis_score": row.get("axis_score", 0),
            "semantic_score": row.get("semantic_score", 0),
            "answer_semantic_tags": row.get("answer_semantic_tags", []),
            "semantic_tags": row.get("semantic_tags", []),
            "representative_score": row.get("representative_score", 0),
            "generic_category_penalty": row.get("generic_category_penalty", 0),
            "candidate_topic_type": row.get("candidate_topic_type", ""),
            "candidate_era": normalized_era(candidate_period),
            "type_mismatch_penalty": row.get("type_mismatch_penalty", 0),
        }
        candidate_context = {
            "anchor_label": "Term",
            "anchor": {
                "name": target,
                "term_id": row.get("term_id", ""),
                "description": description or basis,
                "period_text": candidate_period,
            },
            "categories": row.get("categories", []),
            "entity_types": [],
            "eras": row.get("eras", []),
            "graph_facts": [basis],
        }
        if encykorea_api_key:
            external_basis = [
                {**source, "target": target, **target_metadata, "source_alignment": "graph_anchor"}
                for source in retrieve_encykorea_sources(target, encykorea_api_key, timeout)
                if not source_contradicts_context(
                    source,
                    candidate_context,
                    {"topic_type": target_metadata["candidate_topic_type"]},
                )
            ]
            if not external_basis:
                continue
            fact_hints = answer_fact_hint_sentences(selection, external_basis, {"intent": "identity"}, limit=2)
            if not fact_hints:
                continue
            basis_sources = [
                {**external_basis[0], "chunk_id": f"{external_basis[0]['chunk_id']}:fact:{index}", "snippet": hint}
                for index, hint in enumerate(fact_hints, start=1)
            ]
        else:
            basis_sources = retrieve_distractor_sources(
                retriever,
                selection,
                target,
                basis,
                basis_top_k,
                target_metadata,
            )
        targets.append(target_metadata)
        basis_list.append(basis_sources)
    return targets, basis_list


def retrieve_distractor_basis_legacy(
    driver: Any,
    retriever: PgVectorHybridRetriever,
    selection: dict[str, Any],
    context: dict[str, Any],
    target_count: int,
    basis_top_k: int,
    encykorea_api_key: str = "",
    timeout: int = 60,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """단일 shared CanonicalCategory sibling만 사용하는 이전 후보 검색 구현."""
    del retriever, basis_top_k, encykorea_api_key, timeout
    if driver is None or target_count <= 0 or context.get("anchor_label") != "Term":
        return [], []

    topic = str(selection.get("topic") or "")
    term_id = graph_anchor_term_id(context)
    if not term_id:
        return [], []

    with driver.session() as session:
        rows = session.run(
            """
            MATCH (seed:Term {term_id:$term_id})-[:HAS_CATEGORY]->(shared:CanonicalCategory)
            WITH seed, shared,
                 coalesce(toInteger(shared.depth), size([part IN split(coalesce(shared.category_path, ''), '>') WHERE trim(part) <> ''])) AS shared_depth
            WHERE shared_depth >= 2
            MATCH (candidate:Term)-[:HAS_CATEGORY]->(shared)
            WHERE candidate.term_id <> seed.term_id
            WITH seed, shared, shared_depth, count(DISTINCT candidate) AS sibling_count
            WHERE sibling_count >= $required_count
            ORDER BY shared_depth DESC, sibling_count ASC, shared.category_path
            LIMIT 1
            MATCH (candidate:Term)-[:HAS_CATEGORY]->(shared)
            WHERE candidate.term_id <> seed.term_id
            OPTIONAL MATCH (seed)-[:IN_ERA]->(seed_era:Era)
            OPTIONAL MATCH (candidate)-[:IN_ERA]->(era:Era)
            OPTIONAL MATCH (candidate)-[:HAS_CATEGORY]->(category:CanonicalCategory)
            OPTIONAL MATCH (candidate)-[:HAS_ENTITY_TYPE]->(entity:EntityType)
            WITH seed, shared, shared_depth, sibling_count, candidate,
                 collect(DISTINCT seed_era.name) AS seed_eras,
                 collect(DISTINCT era.name) AS eras,
                 collect(DISTINCT category.category_path) AS categories,
                 collect(DISTINCT entity.name) AS entity_types
            RETURN candidate.name AS target,
                   candidate.term_id AS candidate_term_id,
                   candidate.description AS description,
                   candidate.period_text AS period_text,
                   candidate.start_year AS start_year,
                   seed_eras, eras, categories, entity_types,
                   coalesce(shared.category_id, elementId(shared)) AS shared_node_id,
                   elementId(shared) AS shared_node_element_id,
                   shared.category_path AS shared_node_name,
                   labels(shared)[0] AS shared_node_type,
                   shared_depth, sibling_count,
                   CASE WHEN candidate.question_ready = 'Y' THEN 2 ELSE 0 END
                     + CASE WHEN candidate.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score,
                   CASE WHEN seed.start_year IS NULL OR candidate.start_year IS NULL THEN 999999
                        ELSE abs(toInteger(candidate.start_year) - toInteger(seed.start_year)) END AS year_gap
            ORDER BY year_gap ASC, candidate.name, candidate.term_id
            """,
            term_id=term_id,
            required_count=4,
        ).data()

    targets: list[dict[str, Any]] = []
    basis_list: list[list[dict[str, Any]]] = []
    for row in rows:
        target = str(row.get("target") or "")
        candidate_term_id = str(row.get("candidate_term_id") or "")
        if not target or not candidate_term_id or not valid_distractor_target(target, topic):
            continue
        description = compact(row.get("description"), 500)
        shared_node_id = str(row.get("shared_node_id") or "")
        graph_path = [
            {"node_type": "Term", "node_id": term_id, "node_name": topic},
            {"relationship": "HAS_CATEGORY", "direction": "OUT"},
            {
                "node_type": str(row.get("shared_node_type") or "CanonicalCategory"),
                "node_id": shared_node_id,
                "node_name": str(row.get("shared_node_name") or ""),
            },
            {"relationship": "HAS_CATEGORY", "direction": "IN"},
            {"node_type": "Term", "node_id": candidate_term_id, "node_name": target},
        ]
        candidate_era = "|".join(str(value) for value in row.get("eras", []) if value) or str(row.get("period_text") or "")
        target_metadata = {
            "term_name": target,
            "candidate_term_id": candidate_term_id,
            "description": description,
            "relation_reason": "shared_node_sibling",
            "graph_score": 100,
            "exam_score": int(row.get("exam_score") or 0),
            "year_gap": int(row.get("year_gap") or 999999),
            "graph_routes": ["shared_node_sibling", "category_detail"],
            "answer_axis": str(row.get("shared_node_name") or ""),
            "axis_score": 100,
            "semantic_score": 0,
            "representative_score": int(row.get("exam_score") or 0) * 4,
            "candidate_topic_type": candidate_expected_topic_type(row),
            "candidate_era": normalized_era(candidate_era),
            "shared_node_id": shared_node_id,
            "shared_node_element_id": str(row.get("shared_node_element_id") or ""),
            "shared_node_type": str(row.get("shared_node_type") or "CanonicalCategory"),
            "shared_node_name": str(row.get("shared_node_name") or ""),
            "shared_node_depth": int(row.get("shared_depth") or 0),
            "shared_node_sibling_count": int(row.get("sibling_count") or 0),
            "graph_path": graph_path,
            "fallback_used": False,
        }
        targets.append(target_metadata)
        basis_list.append(
            [
                {
                    "target": target,
                    "chunk_id": f"neo4j:term:{candidate_term_id}:description",
                    "source_type": "neo4j_term_description",
                    "title": target,
                    "score": 1.0,
                    "snippet": description,
                    "truth_owner": target,
                    "truth_owner_id": candidate_term_id,
                    "fact_owner_verification": "term_id_direct_property",
                    "shared_node_id": shared_node_id,
                    "graph_path": graph_path,
                    "fallback_used": False,
                }
            ]
        )
    if len(targets) <= target_count:
        selected_indexes = range(len(targets))
    elif selection.get("difficulty_label") == "어려움":
        selected_indexes = range(target_count)
    elif selection.get("difficulty_label") == "쉬움":
        selected_indexes = range(len(targets) - target_count, len(targets))
    else:
        start = (len(targets) - target_count) // 2
        selected_indexes = range(start, start + target_count)
    selected_indexes = list(selected_indexes)
    for relative_rank, index in enumerate(selected_indexes, start=1):
        targets[index]["relative_rank"] = relative_rank
        targets[index]["relative_pool_size"] = len(targets)
    return [targets[index] for index in selected_indexes], [basis_list[index] for index in selected_indexes]


def retrieve_distractor_basis(
    driver: Any,
    retriever: PgVectorHybridRetriever,
    selection: dict[str, Any],
    context: dict[str, Any],
    target_count: int,
    basis_top_k: int,
    encykorea_api_key: str = "",
    timeout: int = 60,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """구형 Graph/RAG pack 생성기가 호출하는 오답 후보·근거 검색 공개 함수."""
    return _legacy_retrieve_distractor_basis(
        driver,
        retriever,
        selection,
        context,
        target_count,
        basis_top_k,
        encykorea_api_key,
        timeout,
    )


def retrieve_material_sources(retriever: PgVectorHybridRetriever, selection: dict[str, Any], top_k: int) -> list[dict[str, Any]]:
    """pgvector에서 topic 중심 지문 근거를 검색하고 직접 일치 결과를 앞세운다."""
    if top_k <= 0:
        return []
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
    return matched[:top_k]


@lru_cache(maxsize=2048)
def retrieve_encykorea_sources(topic: str, api_key: str, timeout: int) -> list[dict[str, Any]]:
    """구형 민백 API 어댑터가 있으면 정확히 같은 표제어의 근거를 가져온다."""
    if not api_key:
        return []
    try:
        from question_generation.encykorea_sllm_smoke import article_sources, fetch_article_basis, norm

        article = fetch_article_basis(topic, api_key, timeout)
        if norm(article["title"]) != norm(topic):
            return []
        return article_sources(article)
    except Exception:
        return []


def graph_comparison_sources(
    driver: Any,
    selection: dict[str, Any],
    context: dict[str, Any],
    limit: int = 3,
) -> list[dict[str, Any]]:
    """같은 category·theme에 속한 비교 대상을 Graph source로 조회한다."""
    if driver is None or context.get("anchor_label") != "Term":
        return []
    term_id = graph_anchor_term_id(context)
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (seed:Term)
            WHERE ($term_id <> '' AND seed.term_id = $term_id) OR seed.name = $topic
            WITH seed
            ORDER BY CASE WHEN $term_id <> '' AND seed.term_id = $term_id THEN 0 ELSE 1 END
            LIMIT 1
            OPTIONAL MATCH (seed)-[:HAS_CATEGORY]->(seed_cat:CanonicalCategory)
            OPTIONAL MATCH (seed)-[:HAS_THEME]->(seed_theme:Theme)
            WITH seed,
                 collect(DISTINCT seed_cat.category_path) AS seed_categories,
                 collect(DISTINCT seed_theme.name) AS seed_themes
            MATCH (t:Term)
            WHERE t.name <> seed.name AND t.description IS NOT NULL
            OPTIONAL MATCH (t)-[:HAS_CATEGORY]->(cat:CanonicalCategory)
            OPTIONAL MATCH (t)-[:HAS_THEME]->(theme:Theme)
            WITH t, seed_categories, seed_themes,
                 collect(DISTINCT cat.category_path) AS categories,
                 collect(DISTINCT theme.name) AS themes
            WITH t, categories, themes,
                 size([x IN categories WHERE x IN seed_categories]) AS category_overlap,
                 size([x IN themes WHERE x IN seed_themes]) AS theme_overlap
            WHERE category_overlap > 0 OR theme_overlap > 0
            RETURN t.name AS name, t.description AS description, categories, themes,
                   category_overlap, theme_overlap,
                   CASE WHEN t.question_ready = 'Y' THEN 2 ELSE 0 END
                   + CASE WHEN t.is_exam_keyword = 'Y' THEN 1 ELSE 0 END AS exam_score
            ORDER BY category_overlap DESC, theme_overlap DESC, exam_score DESC, t.name
            LIMIT $limit
            """,
            topic=selection["topic"],
            term_id=term_id,
            limit=limit,
        ).data()

    return [
        {
            "chunk_id": f"neo4j:comparison:{row['name']}",
            "source_type": "neo4j_comparison_fact",
            "title": f"{row['name']} comparison fact",
            "score": 1.0,
            "snippet": compact(f"비교 후보: {row['name']} - {row['description']}", 360),
            "retrieval_slots": ["comparison_basis"],
            "graph_categories": row.get("categories", []),
            "graph_themes": row.get("themes", []),
        }
        for row in rows
        if row.get("name") and row.get("description")
    ]


def graph_inquiry_sources(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Graph category·theme·era를 탐구 활동 검색 키워드 source로 만든다."""
    values = [
        *[str(value) for value in context.get("categories", []) if value],
        *[str(value) for value in context.get("themes", []) if value],
        *[str(value) for value in context.get("eras", []) if value],
    ]
    if not values:
        return []
    return [
        {
            "chunk_id": "neo4j:inquiry:keywords",
            "source_type": "neo4j_inquiry_keyword",
            "title": "Graph inquiry keywords",
            "score": 1.0,
            "snippet": f"탐구 검색 기준: {', '.join(values[:8])}",
            "retrieval_slots": ["search_keyword"],
        }
    ]


def source_contradicts_context(
    source: dict[str, Any],
    context: dict[str, Any],
    selection: dict[str, Any] | None = None,
) -> bool:
    """RAG source의 의미가 Graph anchor의 장소·문헌·유형 문맥과 충돌하는지 검사한다."""
    text = f"{source.get('title', '')} {source.get('snippet', '')}"
    categories = " ".join(str(value) for value in context.get("categories", []))
    entities = " ".join(str(value) for value in context.get("entity_types", []))
    if ("지명" in categories or "장소" in entities) and re.search(r"서사시|시집|소설|희곡|영화|작품|문학", text):
        return True
    place_markers = re.findall(r"높이|해발|산맥|고개|봉우리|사면|지류|산록|터널|도로|위치", text)
    if (selection or {}).get("topic_type") not in {"문화유산", "기타"} and len(set(place_markers)) >= 3:
        return True
    span = graph_anchor_year_span(context)
    source_years = [int(value) for value in re.findall(r"(?<!\d)(\d{3,4})\s*년", text)]
    if span and source_years:
        start, end = span
        if all(year < start - 2 or year > end + 2 for year in source_years):
            return True
    family = semantic_family(selection or {}, context)
    context_tags = semantic_tags(
        [
            context.get("anchor", {}).get("description", ""),
            *context.get("graph_facts", []),
            *context.get("categories", []),
            *context.get("entity_types", []),
        ],
        family,
    )
    source_tags = semantic_tags([text], family)
    if context_tags and source_tags and not (context_tags & source_tags):
        return True
    return False

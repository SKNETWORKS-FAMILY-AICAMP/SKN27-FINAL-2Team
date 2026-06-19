from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class KingEntity:
    entity_id: str
    display_name: str
    dynasty: str
    posthumous_name: str
    personal_name: str
    reign_start: int | None
    reign_end: int | None
    era: str
    aliases: tuple[str, ...]


KINGS: tuple[KingEntity, ...] = (
    KingEntity("goguryeo_gwanggaeto", "고구려 광개토대왕", "고구려", "광개토대왕", "담덕", 391, 413, "삼국 시대", ("광개토대왕", "광개토왕", "호태왕", "담덕")),
    KingEntity("goguryeo_jangsu", "고구려 장수왕", "고구려", "장수왕", "거련", 413, 491, "삼국 시대", ("장수왕", "거련")),
    KingEntity("baekje_geunchogo", "백제 근초고왕", "백제", "근초고왕", "", 346, 375, "삼국 시대", ("근초고왕",)),
    KingEntity("baekje_muryeong", "백제 무령왕", "백제", "무령왕", "사마", 501, 523, "삼국 시대", ("무령왕", "사마왕", "사마")),
    KingEntity("baekje_seong", "백제 성왕", "백제", "성왕", "명농", 523, 554, "삼국 시대", ("성왕", "명농")),
    KingEntity("silla_naemul", "신라 내물왕", "신라", "내물왕", "", 356, 402, "삼국 시대", ("내물왕", "내물마립간")),
    KingEntity("silla_jijeung", "신라 지증왕", "신라", "지증왕", "", 500, 514, "삼국 시대", ("지증왕", "지증마립간")),
    KingEntity("silla_beopheung", "신라 법흥왕", "신라", "법흥왕", "", 514, 540, "삼국 시대", ("법흥왕",)),
    KingEntity("silla_jinheung", "신라 진흥왕", "신라", "진흥왕", "", 540, 576, "삼국 시대", ("진흥왕",)),
    KingEntity("unified_silla_munmu", "신라 문무왕", "신라", "문무왕", "김법민", 661, 681, "통일 신라와 발해", ("문무왕", "김법민")),
    KingEntity("unified_silla_sinmun", "신라 신문왕", "신라", "신문왕", "", 681, 692, "통일 신라와 발해", ("신문왕",)),
    KingEntity("balhae_go", "발해 고왕", "발해", "고왕", "대조영", 698, 719, "통일 신라와 발해", ("고왕", "대조영")),
    KingEntity("balhae_mu", "발해 무왕", "발해", "무왕", "대무예", 719, 737, "통일 신라와 발해", ("무왕", "대무예")),
    KingEntity("balhae_mun", "발해 문왕", "발해", "문왕", "대흠무", 737, 793, "통일 신라와 발해", ("문왕", "대흠무")),
    KingEntity("goryeo_taejo", "고려 태조", "고려", "태조", "왕건", 918, 943, "고려 시대", ("고려 태조", "태조 왕건", "왕건")),
    KingEntity("goryeo_gwangjong", "고려 광종", "고려", "광종", "왕소", 949, 975, "고려 시대", ("광종", "왕소", "고려 광종")),
    KingEntity("goryeo_seongjong", "고려 성종", "고려", "성종", "왕치", 981, 997, "고려 시대", ("고려 성종", "왕치")),
    KingEntity("goryeo_hyeonjong", "고려 현종", "고려", "현종", "왕순", 1009, 1031, "고려 시대", ("고려 현종", "왕순")),
    KingEntity("goryeo_gongmin", "고려 공민왕", "고려", "공민왕", "왕전", 1351, 1374, "고려 시대", ("공민왕", "왕전")),
    KingEntity("joseon_taejo", "조선 태조", "조선", "태조", "이성계", 1392, 1398, "조선 전기", ("조선 태조", "태조 이성계", "이성계")),
    KingEntity("joseon_taejong", "조선 태종", "조선", "태종", "이방원", 1400, 1418, "조선 전기", ("조선 태종", "태종 이방원", "이방원")),
    KingEntity("joseon_sejong", "조선 세종", "조선", "세종", "이도", 1418, 1450, "조선 전기", ("조선 세종", "세종대왕", "세종", "이도")),
    KingEntity("joseon_munjong", "조선 문종", "조선", "문종", "이향", 1450, 1452, "조선 전기", ("조선 문종", "이향")),
    KingEntity("joseon_sejo", "조선 세조", "조선", "세조", "이유", 1455, 1468, "조선 전기", ("조선 세조", "수양대군", "이유")),
    KingEntity("joseon_seongjong", "조선 성종", "조선", "성종", "이혈", 1469, 1494, "조선 전기", ("조선 성종", "이혈")),
    KingEntity("joseon_yeonsangun", "조선 연산군", "조선", "연산군", "이융", 1494, 1506, "조선 전기", ("연산군", "이융")),
    KingEntity("joseon_jungjong", "조선 중종", "조선", "중종", "이역", 1506, 1544, "조선 전기", ("중종", "조선 중종", "이역")),
    KingEntity("joseon_myeongjong", "조선 명종", "조선", "명종", "이환", 1545, 1567, "조선 전기", ("조선 명종", "이환")),
    KingEntity("joseon_seonjo", "조선 선조", "조선", "선조", "이연", 1567, 1608, "조선 후기", ("선조", "조선 선조", "이연")),
    KingEntity("joseon_gwanghae", "조선 광해군", "조선", "광해군", "이혼", 1608, 1623, "조선 후기", ("광해군", "이혼")),
    KingEntity("joseon_injo", "조선 인조", "조선", "인조", "이종", 1623, 1649, "조선 후기", ("인조", "조선 인조", "이종")),
    KingEntity("joseon_hyojong", "조선 효종", "조선", "효종", "이호", 1649, 1659, "조선 후기", ("효종", "조선 효종", "이호")),
    KingEntity("joseon_hyeonjong", "조선 현종", "조선", "현종", "이연", 1659, 1674, "조선 후기", ("조선 현종",)),
    KingEntity("joseon_sukjong", "조선 숙종", "조선", "숙종", "이순", 1674, 1720, "조선 후기", ("숙종", "조선 숙종", "이순")),
    KingEntity("joseon_yeongjo", "조선 영조", "조선", "영조", "이금", 1724, 1776, "조선 후기", ("영조", "조선 영조", "이금")),
    KingEntity("joseon_jeongjo", "조선 정조", "조선", "정조", "이산", 1776, 1800, "조선 후기", ("정조", "조선 정조", "이산")),
    KingEntity("joseon_heungseon", "흥선 대원군", "조선", "흥선 대원군", "이하응", 1863, 1873, "근대", ("흥선 대원군", "대원군", "이하응")),
    KingEntity("joseon_gojong", "고종", "조선/대한제국", "고종", "이형", 1863, 1907, "근대", ("고종", "대한제국 고종", "조선 고종", "광무황제", "이형")),
    KingEntity("joseon_sunjong", "순종", "대한제국", "순종", "이척", 1907, 1910, "근대", ("순종", "융희황제", "이척")),
)


CONTEXT_BY_DYNASTY = {
    "고구려": ("고구려",),
    "백제": ("백제",),
    "신라": ("신라", "통일 신라", "통일신라"),
    "발해": ("발해",),
    "고려": ("고려", "고려 시대"),
    "조선": ("조선 전기", "조선 후기", "조선 초기", "조선 중기", "조선 왕조", "조선왕조"),
    "대한제국": ("대한제국", "광무", "융희"),
}


def load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")


def connect_db():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "history_rag"),
        user=os.getenv("POSTGRES_USER", "himate"),
        password=os.getenv("POSTGRES_PASSWORD", "himate1234"),
    )


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


SHORT_PERSON_ALIAS_ALLOWLIST = {"왕건", "왕소", "왕순", "왕전", "사마", "담덕", "거련"}


def contains_alias(text: str, alias: str, entity: KingEntity) -> bool:
    compact_alias = normalize_text(alias)
    if not compact_alias:
        return False
    if len(compact_alias) <= 2 and alias != entity.posthumous_name and alias not in SHORT_PERSON_ALIAS_ALLOWLIST:
        return False

    escaped_chars = [re.escape(char) for char in alias if not char.isspace()]
    loose_alias = r"\s*".join(escaped_chars)
    if alias.startswith("조선"):
        pattern = rf"(?<!고){loose_alias}"
        return re.search(pattern, text) is not None
    if alias.startswith(("고려", "백제", "신라", "발해", "고구려", "대한제국")):
        return re.search(loose_alias, text) is not None

    if alias == entity.posthumous_name:
        return has_royal_context(text, alias)

    compact_text = normalize_text(text)
    return compact_alias in compact_text


def has_royal_context(text: str, alias: str) -> bool:
    escaped_chars = [re.escape(char) for char in alias if not char.isspace()]
    loose_alias = r"\s*".join(escaped_chars)
    pattern = rf"(?<![가-힣]){loose_alias}\s*(왕|때|대|즉위|재위|의\s*업적|의\s*정책)"
    return re.search(pattern, text) is not None


def dynasty_context_matches(text: str, dynasty: str) -> bool:
    if "조선" in dynasty and "고조선" in text and not any(
        marker in text for marker in CONTEXT_BY_DYNASTY["조선"]
    ):
        return False
    for key, aliases in CONTEXT_BY_DYNASTY.items():
        if key in dynasty and any(alias in text for alias in aliases):
            return True
    return False


def metadata_context(metadata: dict[str, Any]) -> str:
    chronology = metadata.get("chronology") or {}
    parts = [
        str(metadata.get("period") or ""),
        str(metadata.get("era") or ""),
        str(metadata.get("dynasty") or ""),
        str(chronology.get("era") or ""),
        str(chronology.get("dynasty") or ""),
        " ".join(metadata.get("category") or [] if isinstance(metadata.get("category"), list) else []),
    ]
    return " ".join(parts)


def king_payload(entity: KingEntity, matched_aliases: list[str]) -> dict[str, Any]:
    payload = asdict(entity)
    payload["aliases"] = list(entity.aliases)
    payload["matched_aliases"] = matched_aliases
    return payload


def extract_kings(title: str, chunk_text: str, metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = f"{title}\n{chunk_text}"
    context = f"{metadata_context(metadata)}\n{text[:800]}"
    mentioned: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []

    for entity in KINGS:
        matched_aliases: list[str] = []
        strong_match = False
        for alias in entity.aliases:
            if not contains_alias(text, alias, entity):
                continue
            matched_aliases.append(alias)
            if alias != entity.posthumous_name or len(alias) >= 4:
                strong_match = True

        if not matched_aliases:
            continue

        if strong_match or dynasty_context_matches(context, entity.dynasty):
            mentioned.append(king_payload(entity, matched_aliases))
        else:
            ambiguous.append(
                {
                    "posthumous_name": entity.posthumous_name,
                    "candidate_display_name": entity.display_name,
                    "candidate_dynasty": entity.dynasty,
                    "matched_aliases": matched_aliases,
                }
            )

    unique_mentioned: dict[str, dict[str, Any]] = {king["entity_id"]: king for king in mentioned}
    return list(unique_mentioned.values()), ambiguous


def enrich_metadata(row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    metadata = row["metadata"] or {}
    mentioned_kings, ambiguous = extract_kings(row["title"], row["chunk_text"], metadata)
    updated = dict(metadata)

    if mentioned_kings:
        updated["mentioned_kings"] = mentioned_kings
        updated["king_aliases"] = sorted({alias for king in mentioned_kings for alias in king["matched_aliases"]})
        updated["king_dynasties"] = sorted({king["dynasty"] for king in mentioned_kings})
    else:
        updated.pop("mentioned_kings", None)
        updated.pop("king_aliases", None)
        updated.pop("king_dynasties", None)

    if ambiguous:
        updated["ambiguous_king_mentions"] = ambiguous
    else:
        updated.pop("ambiguous_king_mentions", None)

    return updated, updated != metadata


def fetch_rows(conn, limit: int | None) -> list[dict[str, Any]]:
    query = """
        SELECT chunk_id, title, chunk_text, metadata
        FROM rag.document_chunks
        ORDER BY id
    """
    params: tuple[Any, ...] = ()
    if limit:
        query += " LIMIT %s"
        params = (limit,)
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def update_rows(conn, rows: list[tuple[str, dict[str, Any]]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            UPDATE rag.document_chunks AS target
            SET metadata = data.metadata::jsonb,
                updated_at = NOW()
            FROM (VALUES %s) AS data(chunk_id, metadata)
            WHERE target.chunk_id = data.chunk_id
            """,
            [(chunk_id, Json(metadata)) for chunk_id, metadata in rows],
            template="(%s, %s)",
            page_size=500,
        )
    conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich existing RAG chunk metadata with Korean king entities")
    parser.add_argument("--limit", type=int, default=None, help="Limit chunks for a test run")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without updating PostgreSQL")
    parser.add_argument("--sample", type=int, default=10, help="Number of matched samples to print")
    return parser.parse_args()


def main() -> None:
    load_env()
    args = parse_args()
    conn = connect_db()
    rows = fetch_rows(conn, args.limit)
    updates: list[tuple[str, dict[str, Any]]] = []
    matched_samples: list[dict[str, Any]] = []
    ambiguous_count = 0

    for row in rows:
        enriched, changed = enrich_metadata(row)
        if not changed:
            continue
        updates.append((row["chunk_id"], enriched))
        if enriched.get("mentioned_kings") and len(matched_samples) < args.sample:
            matched_samples.append(
                {
                    "chunk_id": row["chunk_id"],
                    "title": row["title"],
                    "mentioned_kings": [
                        king["display_name"] for king in enriched.get("mentioned_kings", [])
                    ],
                }
            )
        if enriched.get("ambiguous_king_mentions"):
            ambiguous_count += 1

    if not args.dry_run:
        update_rows(conn, updates)

    print(
        json.dumps(
            {
                "scanned_chunks": len(rows),
                "updated_chunks": len(updates),
                "ambiguous_chunks": ambiguous_count,
                "dry_run": args.dry_run,
                "samples": matched_samples,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

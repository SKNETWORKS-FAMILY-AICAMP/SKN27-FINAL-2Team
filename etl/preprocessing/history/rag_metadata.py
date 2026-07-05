from __future__ import annotations

import re
from typing import Iterable


ERA_RULES = [
    {
        "aliases": ["선사", "구석기", "신석기"],
        "era": "선사",
        "era_order": 5,
        "dynasty": "",
        "start_year": -700000,
        "end_year": -1500,
    },
    {
        "aliases": ["고조선", "부여", "삼한", "삼국 이전", "청동기", "철기"],
        "era": "삼국 이전",
        "era_order": 10,
        "dynasty": "고조선/초기 국가",
        "start_year": -2333,
        "end_year": 300,
    },
    {
        "aliases": ["삼국 시대", "고구려", "백제", "신라", "가야"],
        "era": "삼국 시대",
        "era_order": 20,
        "dynasty": "삼국",
        "start_year": 300,
        "end_year": 668,
    },
    {
        "aliases": ["통일 신라", "통일신라", "발해", "남북국"],
        "era": "통일 신라와 발해",
        "era_order": 30,
        "dynasty": "통일 신라/발해",
        "start_year": 668,
        "end_year": 935,
    },
    {
        "aliases": ["고려"],
        "era": "고려 시대",
        "era_order": 40,
        "dynasty": "고려",
        "start_year": 918,
        "end_year": 1392,
    },
    {
        "aliases": ["조선 전기", "조선 초기", "조선왕조의 성립", "조선 초기의"],
        "era": "조선 전기",
        "era_order": 50,
        "dynasty": "조선",
        "start_year": 1392,
        "end_year": 1592,
    },
    {
        "aliases": ["조선 중기", "조선 후기", "조선 말기"],
        "era": "조선 후기",
        "era_order": 60,
        "dynasty": "조선",
        "start_year": 1592,
        "end_year": 1863,
    },
    {
        "aliases": ["근대", "개항", "대한제국", "일제", "국권", "개화"],
        "era": "근대",
        "era_order": 70,
        "dynasty": "근대",
        "start_year": 1863,
        "end_year": 1945,
    },
    {
        "aliases": ["현대", "대한민국", "광복 이후"],
        "era": "현대",
        "era_order": 80,
        "dynasty": "대한민국",
        "start_year": 1945,
        "end_year": None,
    },
]

YEAR_PATTERN = re.compile(r"(?<!\d)([12]\d{3})(?:\s*년)?")
TAG_SPLIT_PATTERN = re.compile(r"[>|,/·ㆍ：:\-－\s]+")
HEADING_PREFIX_PATTERN = re.compile(
    r"^\s*(?:"
    r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?\s*|"
    r"\(?\d+\)?[.)]?\s*|"
    r"[가-힣]\.\s*|"
    r"[A-Za-z]\.\s*"
    r")+"
)


def dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = normalize_tag(value)
        if value and value not in result:
            result.append(value)
    return result


def normalize_tag(value: str | None) -> str:
    if not value:
        return ""
    value = normalize_heading(value)
    value = value.strip(" #`[]()")
    return value


def normalize_heading(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value.strip())
    previous = None
    while previous != value:
        previous = value
        value = HEADING_PREFIX_PATTERN.sub("", value).strip()
    return value.strip(" -－")


def split_category_path(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"\s*>\s*|\|", value)
    return dedupe(parts)


def normalize_periods(period: str | None, periods: Iterable[str] | None = None) -> tuple[str, list[str]]:
    period_values = dedupe([*(periods or []), *(split_category_path(period) if period else [])])
    return (period_values[0] if period_values else "", period_values)


def keyword_tags(value: str | None) -> list[str]:
    if not value:
        return []
    return dedupe(token for token in TAG_SPLIT_PATTERN.split(value) if len(token.strip()) >= 2)


def build_category_tags(
    *,
    title: str = "",
    period: str = "",
    field: str = "",
    category: str = "",
    keywords: Iterable[str] | None = None,
    extra: Iterable[str] | None = None,
) -> list[str]:
    tags: list[str] = []
    tags.extend(split_category_path(period))
    tags.extend(split_category_path(field))
    tags.extend(split_category_path(category))
    tags.extend(keyword_tags(title))
    tags.extend(keywords or [])
    tags.extend(extra or [])
    return dedupe(tags)[:50]


def extract_years(*texts: str) -> list[int]:
    years: list[int] = []
    for text in texts:
        for match in YEAR_PATTERN.finditer(text or ""):
            year = int(match.group(1))
            if 1 <= year <= 2100 and year not in years:
                years.append(year)
    return years[:20]


def empty_chronology() -> dict:
    return {
        "era": "",
        "era_order": None,
        "dynasty": "",
        "start_year": None,
        "end_year": None,
    }


def rule_to_chronology(rule: dict) -> dict:
    return {
        "era": rule["era"],
        "era_order": rule["era_order"],
        "dynasty": rule["dynasty"],
        "start_year": rule["start_year"],
        "end_year": rule["end_year"],
    }


def matching_era_rules(text: str) -> list[dict]:
    matches = []
    for rule in ERA_RULES:
        if any(alias in text for alias in rule["aliases"]):
            matches.append(rule)
    return matches


def infer_era(metadata_text: str, content_text: str = "") -> dict:
    metadata_matches = matching_era_rules(metadata_text)
    if metadata_matches:
        return rule_to_chronology(metadata_matches[0])

    return empty_chronology()


def build_chronology(
    *,
    title: str = "",
    period: str = "",
    category: str = "",
    content: str = "",
    extra_text: str = "",
) -> dict:
    years = extract_years(title, period, category, content, extra_text)
    metadata_text = " ".join(text for text in (period, category, title, extra_text) if text)
    chronology = infer_era(metadata_text, content[:1200])
    chronology.update(
        {
            "period_label": period,
            "mentioned_years": years,
            "primary_year": years[0] if years else None,
        }
    )
    return chronology

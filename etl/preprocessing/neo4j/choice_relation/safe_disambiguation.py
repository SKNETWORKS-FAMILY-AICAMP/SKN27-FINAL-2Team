from __future__ import annotations

from collections import defaultdict
from json import JSONDecodeError, dumps, loads
from pathlib import Path
import re

import pandas as pd

from choice_relation.deterministic_candidates import parse_json_list


def normalize_hanja(value: object) -> str:
    """한자 비교에 필요 없는 공백과 문장부호를 제거한다."""
    return "".join(
        re.findall(r"[\u3400-\u9fff]", str(value or ""))
    )


def normalize_era(value: object) -> str:
    """시대 표기의 공백과 계층 구분자를 제거한다."""
    return "".join(
        re.findall(r"[가-힣A-Za-z0-9]+", str(value or ""))
    ).lower()


def tokenize_definition(
    value: object,
    minimum_token_length: int,
    stopwords: set[str],
) -> set[str]:
    """정의 비교에 사용할 의미 있는 한글·숫자 토큰을 만든다."""
    return {
        token.lower()
        for token in re.findall(
            rf"[가-힣A-Za-z0-9]{{{minimum_token_length},}}",
            str(value or ""),
        )
        if token.lower() not in stopwords
    }


def collect_relevant_canonical_ids(
    official_checks: pd.DataFrame,
) -> set[str]:
    """공식 원문 검증 대상에 실제로 포함된 canonical ID만 모은다."""
    canonical_ids: set[str] = set()
    for row in official_checks.to_dict("records"):
        canonical_ids.update(
            parse_json_list(row.get("existing_canonical_ids_json", ""))
        )
        recovered_mentions = loads(
            str(row.get("recovered_mentions_json") or "[]")
        )
        if not isinstance(recovered_mentions, list):
            continue
        for mention in recovered_mentions:
            if not isinstance(mention, dict):
                continue
            canonical_id = str(mention.get("canonical_id") or "")
            if canonical_id:
                canonical_ids.add(canonical_id)
    return canonical_ids


def build_canonical_source_profiles(
    canonical_registry: pd.DataFrame,
    source_records: pd.DataFrame,
    relevant_canonical_ids: set[str],
    policy: dict,
) -> tuple[
    dict[str, dict[str, object]],
    dict[str, list[str]],
]:
    """canonical별 승인 원천의 한자·시대·정의 프로필을 만든다."""
    text_policy = policy[
        "exam_relation_official_text_corroboration"
    ]
    disambiguation_policy = text_policy["safe_disambiguation"]
    accepted_status = str(
        policy["exam_relation_official_corroboration"][
            "accepted_registry_status"
        ]
    )
    active_registry_rows = [
        row
        for row in canonical_registry.to_dict("records")
        if str(row["lifecycle_status"]) == accepted_status
    ]
    relevant_names = {
        str(row["display_name"]).strip()
        for row in active_registry_rows
        if str(row["canonical_id"]) in relevant_canonical_ids
    }
    registry_rows = [
        row
        for row in active_registry_rows
        if str(row["display_name"]).strip() in relevant_names
    ]
    source_ids_by_canonical = {
        str(row["canonical_id"]): parse_json_list(
            row.get("identity_member_source_ids_json", "")
        )
        for row in registry_rows
    }
    required_source_ids = {
        source_id
        for source_ids in source_ids_by_canonical.values()
        for source_id in source_ids
    }
    source_row_by_id = {
        str(row["source_record_id"]): row
        for row in source_records.to_dict("records")
        if str(row["source_record_id"]) in required_source_ids
    }
    minimum_token_length = int(
        disambiguation_policy["minimum_definition_token_length"]
    )
    stopwords = {
        str(value).lower()
        for value in disambiguation_policy["definition_stopwords"]
    }
    profiles: dict[str, dict[str, object]] = {}
    canonical_ids_by_name: dict[str, list[str]] = defaultdict(list)
    for registry_row in registry_rows:
        canonical_id = str(registry_row["canonical_id"])
        display_name = str(registry_row["display_name"]).strip()
        profile: dict[str, object] = {
            "canonical_id": canonical_id,
            "display_name": display_name,
            "hanja_values": set(),
            "era_values": set(),
            "definition_token_sets": [],
            "source_record_ids": [],
        }
        for source_id in source_ids_by_canonical[canonical_id]:
            source_row = source_row_by_id.get(source_id)
            if source_row is None:
                continue
            try:
                metadata = loads(
                    str(source_row.get("source_metadata_json") or "{}")
                )
            except (JSONDecodeError, TypeError):
                continue
            if not isinstance(metadata, dict):
                continue
            hanja_value = normalize_hanja(
                metadata.get("hanja")
                or metadata.get("origin")
                or metadata.get("headwordOrigin")
            )
            era_value = normalize_era(metadata.get("era"))
            definition = (
                metadata.get("description")
                or metadata.get("definition")
                or ""
            )
            definition_tokens = tokenize_definition(
                definition,
                minimum_token_length,
                stopwords,
            )
            if hanja_value:
                profile["hanja_values"].add(hanja_value)
            if era_value:
                profile["era_values"].add(era_value)
            if definition_tokens:
                profile["definition_token_sets"].append(
                    definition_tokens
                )
            profile["source_record_ids"].append(source_id)
        profiles[canonical_id] = profile
        canonical_ids_by_name[display_name].append(canonical_id)
    return profiles, dict(canonical_ids_by_name)


def era_values_overlap(
    aks_era: str,
    canonical_eras: set[str],
) -> bool:
    """상·하위 시대 표기가 포함 관계면 같은 시대로 본다."""
    if not aks_era:
        return False
    return any(
        aks_era in canonical_era or canonical_era in aks_era
        for canonical_era in canonical_eras
        if canonical_era
    )


def best_definition_overlap(
    aks_tokens: set[str],
    canonical_token_sets: list[set[str]],
) -> tuple[int, float]:
    """여러 승인 원천 정의 중 가장 강한 토큰 중첩을 반환한다."""
    best_count = 0
    best_ratio = 0.0
    for canonical_tokens in canonical_token_sets:
        overlap_count = len(aks_tokens.intersection(canonical_tokens))
        denominator = min(len(aks_tokens), len(canonical_tokens))
        overlap_ratio = 0.0
        if denominator > 0:
            overlap_ratio = overlap_count / denominator
        if (overlap_count, overlap_ratio) > (
            best_count,
            best_ratio,
        ):
            best_count = overlap_count
            best_ratio = overlap_ratio
    return best_count, best_ratio


def build_safe_aks_disambiguation(
    official_checks: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    source_records: pd.DataFrame,
    aks_list_path: str,
    policy: dict,
) -> tuple[pd.DataFrame, dict[str, str], dict[str, int]]:
    """이름·한자·시대·정의가 모두 맞는 유일한 AKS EID만 연결한다."""
    text_policy = policy[
        "exam_relation_official_text_corroboration"
    ]
    disambiguation_policy = text_policy["safe_disambiguation"]
    relevant_ids = collect_relevant_canonical_ids(official_checks)
    profiles, canonical_ids_by_name = build_canonical_source_profiles(
        canonical_registry,
        source_records,
        relevant_ids,
        policy,
    )
    ambiguous_names = {
        name
        for name, canonical_ids in canonical_ids_by_name.items()
        if len(canonical_ids) > 1
    }
    minimum_token_length = int(
        disambiguation_policy["minimum_definition_token_length"]
    )
    minimum_overlap_count = int(
        disambiguation_policy["minimum_definition_overlap_count"]
    )
    minimum_overlap_ratio = float(
        disambiguation_policy["minimum_definition_overlap_ratio"]
    )
    stopwords = {
        str(value).lower()
        for value in disambiguation_policy["definition_stopwords"]
    }
    statuses = disambiguation_policy["statuses"]
    rows: list[dict] = []
    safe_map: dict[str, str] = {}
    statistics = {
        "ambiguous_relevant_name_count": len(ambiguous_names),
        "aks_same_name_article_count": 0,
        "safe_disambiguation_count": 0,
        "ambiguous_disambiguation_count": 0,
        "no_safe_match_count": 0,
        "aks_list_invalid_json_count": 0,
    }
    with Path(aks_list_path).open("r", encoding="utf-8") as source_file:
        for line in source_file:
            try:
                article = loads(line)
            except (JSONDecodeError, TypeError):
                statistics["aks_list_invalid_json_count"] += 1
                continue
            if not isinstance(article, dict):
                statistics["aks_list_invalid_json_count"] += 1
                continue
            headword = str(article.get("headword") or "").strip()
            if headword not in ambiguous_names:
                continue
            statistics["aks_same_name_article_count"] += 1
            aks_eid = str(article.get("eid") or "")
            aks_hanja = normalize_hanja(
                article.get("origin")
                or article.get("headwordOrigin")
            )
            aks_era = normalize_era(article.get("era"))
            aks_tokens = tokenize_definition(
                article.get("definition"),
                minimum_token_length,
                stopwords,
            )
            candidate_results: list[dict] = []
            passing_ids: list[str] = []
            for canonical_id in canonical_ids_by_name[headword]:
                profile = profiles[canonical_id]
                hanja_match = bool(aks_hanja) and (
                    aks_hanja in profile["hanja_values"]
                )
                era_match = era_values_overlap(
                    aks_era,
                    profile["era_values"],
                )
                overlap_count, overlap_ratio = (
                    best_definition_overlap(
                        aks_tokens,
                        profile["definition_token_sets"],
                    )
                )
                definition_match = (
                    overlap_count >= minimum_overlap_count
                    and overlap_ratio >= minimum_overlap_ratio
                )
                all_fields_match = (
                    hanja_match and era_match and definition_match
                )
                if all_fields_match:
                    passing_ids.append(canonical_id)
                candidate_results.append(
                    {
                        "canonical_id": canonical_id,
                        "hanja_match": hanja_match,
                        "era_match": era_match,
                        "definition_overlap_count": overlap_count,
                        "definition_overlap_ratio": round(
                            overlap_ratio,
                            6,
                        ),
                        "all_fields_match": all_fields_match,
                        "source_record_ids": profile[
                            "source_record_ids"
                        ],
                    }
                )
            status = str(statuses["no_safe_match"])
            resolved_canonical_id = ""
            if len(passing_ids) == 1:
                status = str(statuses["safe_match"])
                resolved_canonical_id = passing_ids[0]
                safe_map[aks_eid] = resolved_canonical_id
                statistics["safe_disambiguation_count"] += 1
            elif len(passing_ids) > 1:
                status = str(statuses["ambiguous"])
                statistics["ambiguous_disambiguation_count"] += 1
            if not passing_ids:
                statistics["no_safe_match_count"] += 1
            rows.append(
                {
                    "aks_eid": aks_eid,
                    "headword": headword,
                    "aks_hanja": aks_hanja,
                    "aks_era": str(article.get("era") or ""),
                    "aks_definition": str(
                        article.get("definition") or ""
                    ),
                    "candidate_count": len(
                        canonical_ids_by_name[headword]
                    ),
                    "passing_candidate_count": len(passing_ids),
                    "resolved_canonical_id": (
                        resolved_canonical_id
                    ),
                    "disambiguation_status": status,
                    "candidate_results_json": dumps(
                        candidate_results,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "llm_used": False,
                    "policy_version": str(
                        disambiguation_policy["policy_version"]
                    ),
                }
            )
    columns = [
        "aks_eid",
        "headword",
        "aks_hanja",
        "aks_era",
        "aks_definition",
        "candidate_count",
        "passing_candidate_count",
        "resolved_canonical_id",
        "disambiguation_status",
        "candidate_results_json",
        "llm_used",
        "policy_version",
    ]
    return pd.DataFrame(rows, columns=columns), safe_map, statistics

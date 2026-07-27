from __future__ import annotations

from collections import Counter, defaultdict
from json import dumps, loads
from pathlib import Path
import re
import unicodedata

import pandas as pd

from source_relationships.build import create_stable_id


def normalize_entity_name(value: object) -> str:
    """엔티티 이름을 기존 파이프라인과 같은 방식으로 정규화한다."""
    normalized = unicodedata.normalize("NFC", str(value or "")).casefold()
    return re.sub(r"\s+", "", normalized)


def clean_attribute_value(
    value: object,
    strip_trailing_parenthetical: bool,
) -> str:
    """구조화 속성값 끝의 한자·연도 괄호를 제거한다."""
    cleaned = str(value or "").strip()
    if strip_trailing_parenthetical:
        cleaned = re.sub(
            r"\s*[\(（][^\(\)（）]*[\)）]\s*$",
            "",
            cleaned,
        ).strip()
    return cleaned


def build_resolution_indexes(
    canonical_registry: pd.DataFrame,
    source_resolutions: pd.DataFrame,
    projection_policy: dict,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, set[str]],
    dict[str, set[str]],
    dict[str, set[str]],
]:
    """활성 CanonicalEntity와 승인된 AKS 출처 해석 인덱스를 만든다."""
    registry_columns = {
        "canonical_id",
        "display_name",
        "entity_type",
        "lifecycle_status",
    }
    resolution_columns = {
        "source_record_id",
        "canonical_id",
        "match_status",
    }
    missing_registry_columns = registry_columns.difference(
        canonical_registry.columns
    )
    missing_resolution_columns = resolution_columns.difference(
        source_resolutions.columns
    )
    if missing_registry_columns:
        missing_text = ", ".join(sorted(missing_registry_columns))
        raise ValueError(
            f"Canonical registry 필수 열이 없습니다: {missing_text}"
        )
    if missing_resolution_columns:
        missing_text = ", ".join(sorted(missing_resolution_columns))
        raise ValueError(
            f"출처 해석 필수 열이 없습니다: {missing_text}"
        )

    accepted_registry_status = str(
        projection_policy["accepted_registry_status"]
    )
    active_registry = canonical_registry[
        canonical_registry["lifecycle_status"].eq(
            accepted_registry_status
        )
    ]
    canonical_types = {
        str(row["canonical_id"]).strip(): str(
            row["entity_type"]
        ).strip()
        for row in active_registry.to_dict("records")
        if str(row["canonical_id"]).strip()
    }
    canonical_names = {
        str(row["canonical_id"]).strip(): str(
            row["display_name"]
        ).strip()
        for row in active_registry.to_dict("records")
        if str(row["canonical_id"]).strip()
    }
    name_to_canonical_ids: dict[str, set[str]] = defaultdict(set)
    for canonical_id, display_name in canonical_names.items():
        normalized_name = normalize_entity_name(display_name)
        if normalized_name:
            name_to_canonical_ids[normalized_name].add(canonical_id)

    accepted_match_status = str(
        projection_policy["accepted_match_status"]
    )
    eid_to_canonical_ids: dict[str, set[str]] = defaultdict(set)
    eid_to_source_record_ids: dict[str, set[str]] = defaultdict(set)
    accepted_resolutions = source_resolutions[
        source_resolutions["match_status"].eq(accepted_match_status)
    ]
    for row in accepted_resolutions.to_dict("records"):
        source_record_id = str(row["source_record_id"]).strip()
        canonical_id = str(row["canonical_id"]).strip()
        parts = source_record_id.split(":")
        if len(parts) < 4:
            continue
        if parts[0] != "AKS" or parts[1] != "ARTICLE":
            continue
        if canonical_id not in canonical_types:
            continue
        eid = parts[2]
        eid_to_canonical_ids[eid].add(canonical_id)
        eid_to_source_record_ids[eid].add(source_record_id)

    return (
        canonical_types,
        canonical_names,
        name_to_canonical_ids,
        eid_to_canonical_ids,
        eid_to_source_record_ids,
    )


def build_aks_attribute_tables(
    articles_path: Path,
    canonical_registry: pd.DataFrame,
    source_resolutions: pd.DataFrame,
    policy: dict,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """AKS 구조화 속성과 관련 문서를 안전한 관계 후보로 변환한다."""
    if not articles_path.is_file():
        raise FileNotFoundError(
            f"AKS 상세 JSONL 파일이 없습니다: {articles_path}"
        )
    if "aks_attribute_projection" not in policy:
        raise ValueError("AKS 구조화 속성 투영 정책이 없습니다.")

    projection_policy = policy["aks_attribute_projection"]
    identifier_policy = policy["identifier"]
    attribute_rules = projection_policy["attribute_rules"]
    value_delimiter = re.compile(
        str(projection_policy["value_delimiter_pattern"])
    )
    strip_parenthetical = bool(
        projection_policy["strip_trailing_parenthetical"]
    )
    (
        canonical_types,
        canonical_names,
        name_to_canonical_ids,
        eid_to_canonical_ids,
        eid_to_source_record_ids,
    ) = build_resolution_indexes(
        canonical_registry,
        source_resolutions,
        projection_policy,
    )

    relationship_rows: list[dict[str, str]] = []
    exclusion_rows: list[dict[str, str]] = []
    related_article_rows: list[dict[str, str]] = []
    counters: Counter[str] = Counter()
    exclusion_reasons: Counter[str] = Counter()
    attribute_names: Counter[str] = Counter()
    relation_types: Counter[str] = Counter()
    scopes: Counter[str] = Counter()

    with articles_path.open("r", encoding="utf-8-sig") as articles_file:
        for line_number, line in enumerate(articles_file, start=1):
            if not line.strip():
                continue
            try:
                article = loads(line)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"AKS JSONL {line_number}행을 읽을 수 없습니다."
                ) from error

            counters["article_count"] += 1
            eid = str(article.get("eid") or "").strip()
            source_canonical_ids = eid_to_canonical_ids.get(eid, set())
            attributes = article.get("articleAttributes") or []
            related_articles = article.get("relatedArticles") or []
            counters["attribute_row_count"] += len(attributes)
            counters["related_article_row_count"] += len(related_articles)

            if len(source_canonical_ids) != 1:
                if attributes:
                    counters[
                        "attribute_rows_from_unresolved_source"
                    ] += len(attributes)
                if related_articles:
                    counters[
                        "related_rows_from_unresolved_source"
                    ] += len(related_articles)
                continue

            source_canonical_id = next(iter(source_canonical_ids))
            source_entity_type = canonical_types[source_canonical_id]
            source_record_ids = sorted(
                eid_to_source_record_ids.get(eid, set())
            )
            source_record_id = ""
            if source_record_ids:
                source_record_id = source_record_ids[0]
            source_release = ""
            if source_record_id.count(":") >= 3:
                source_release = source_record_id.split(":", 3)[3]
            headword = str(article.get("headword") or "").strip()
            source_url = str(article.get("url") or "").strip()
            counters["resolved_source_article_count"] += 1

            for attribute in attributes:
                if not isinstance(attribute, dict):
                    counters["invalid_attribute_row_count"] += 1
                    continue
                attribute_name = str(
                    attribute.get("attrName") or ""
                ).strip()
                attribute_value = str(
                    attribute.get("attrValue") or ""
                ).strip()
                if attribute_name not in attribute_rules:
                    counters["unsupported_attribute_row_count"] += 1
                    continue
                rule = attribute_rules[attribute_name]
                source_types = set(rule["source_entity_types"])
                target_types = set(rule["target_entity_types"])
                raw_values = [
                    value.strip()
                    for value in value_delimiter.split(attribute_value)
                    if value.strip()
                ]
                counters["supported_attribute_value_count"] += len(
                    raw_values
                )
                attribute_names[attribute_name] += len(raw_values)

                for raw_value in raw_values:
                    cleaned_value = clean_attribute_value(
                        raw_value,
                        strip_parenthetical,
                    )
                    normalized_target_name = normalize_entity_name(
                        cleaned_value
                    )
                    all_target_ids = set(
                        name_to_canonical_ids.get(
                            normalized_target_name,
                            set(),
                        )
                    )
                    target_ids = {
                        canonical_id
                        for canonical_id in all_target_ids
                        if canonical_types[canonical_id] in target_types
                    }
                    exclusion_reason = ""
                    if source_entity_type not in source_types:
                        exclusion_reason = "SOURCE_TYPE_MISMATCH"
                    elif not normalized_target_name:
                        exclusion_reason = "EMPTY_TARGET_NAME"
                    elif not all_target_ids:
                        exclusion_reason = "TARGET_UNRESOLVED"
                    elif not target_ids:
                        exclusion_reason = "TARGET_TYPE_MISMATCH"
                    elif len(target_ids) > 1:
                        exclusion_reason = "TARGET_AMBIGUOUS"

                    if exclusion_reason:
                        exclusion_reasons[exclusion_reason] += 1
                        exclusion_rows.append(
                            {
                                "source_article_eid": eid,
                                "source_canonical_id": source_canonical_id,
                                "source_entity_type": source_entity_type,
                                "attribute_name": attribute_name,
                                "attribute_value": raw_value,
                                "normalized_target_name": (
                                    normalized_target_name
                                ),
                                "relation_type": str(
                                    rule["relation_type"]
                                ),
                                "candidate_target_canonical_ids_json": (
                                    dumps(
                                        sorted(all_target_ids),
                                        ensure_ascii=False,
                                    )
                                ),
                                "exclusion_reason": exclusion_reason,
                                "evidence_url": source_url,
                                "policy_version": str(
                                    projection_policy["policy_version"]
                                ),
                            }
                        )
                        continue

                    target_canonical_id = next(iter(target_ids))
                    direction = str(rule["direction"])
                    start_canonical_id = source_canonical_id
                    end_canonical_id = target_canonical_id
                    if direction == "target_to_source":
                        start_canonical_id = target_canonical_id
                        end_canonical_id = source_canonical_id
                    elif direction != "source_to_target":
                        raise ValueError(
                            f"지원하지 않는 AKS 관계 방향입니다: {direction}"
                        )
                    if start_canonical_id == end_canonical_id:
                        exclusion_reasons["SELF_RELATIONSHIP"] += 1
                        continue

                    relation_type = str(rule["relation_type"])
                    scope = str(rule["scope"])
                    candidate_status = str(
                        projection_policy[
                            "discovery_candidate_status"
                        ]
                    )
                    if scope == projection_policy["fact_scope"]:
                        candidate_status = str(
                            projection_policy[
                                "fact_candidate_status"
                            ]
                        )
                    elif scope != projection_policy["discovery_scope"]:
                        raise ValueError(
                            f"지원하지 않는 AKS 관계 범위입니다: {scope}"
                        )
                    release_evidence: list[str] = []
                    if source_release:
                        release_evidence.append(source_release)
                    url_evidence: list[str] = []
                    if source_url:
                        url_evidence.append(source_url)
                    relationship_id = create_stable_id(
                        identifier_policy[
                            "aks_attribute_relationship_prefix"
                        ],
                        [
                            eid,
                            attribute_name,
                            raw_value,
                            start_canonical_id,
                            relation_type,
                            end_canonical_id,
                            str(projection_policy["policy_version"]),
                        ],
                        policy,
                    )
                    evidence_text = (
                        f"{headword} | {attribute_name}: {raw_value}"
                    )
                    relationship_rows.append(
                        {
                            "attribute_relationship_id": relationship_id,
                            "start_canonical_id": start_canonical_id,
                            "end_canonical_id": end_canonical_id,
                            "relation_type": relation_type,
                            "source_article_eid": eid,
                            "source_article_headword": headword,
                            "attribute_name": attribute_name,
                            "attribute_value": raw_value,
                            "target_display_name": canonical_names[
                                target_canonical_id
                            ],
                            "description_mention_ids_json": "[]",
                            "source_relationship_ids_json": dumps(
                                [relationship_id],
                                ensure_ascii=False,
                            ),
                            "raw_relation_types_json": dumps(
                                [attribute_name],
                                ensure_ascii=False,
                            ),
                            "source_datasets_json": dumps(
                                [projection_policy["source_dataset"]],
                                ensure_ascii=False,
                            ),
                            "source_releases_json": dumps(
                                release_evidence,
                                ensure_ascii=False,
                            ),
                            "evidence_urls_json": dumps(
                                url_evidence,
                                ensure_ascii=False,
                            ),
                            "detail_urls_json": dumps(
                                url_evidence,
                                ensure_ascii=False,
                            ),
                            "evidence_sentences_json": dumps(
                                [evidence_text],
                                ensure_ascii=False,
                            ),
                            "scopes_json": dumps(
                                [scope],
                                ensure_ascii=False,
                            ),
                            "evidence_count": "1",
                            "source_row_count": "1",
                            "verification_status": str(
                                projection_policy[
                                    "verification_status"
                                ]
                            ),
                            "candidate_status": candidate_status,
                            "policy_version": str(
                                projection_policy["policy_version"]
                            ),
                        }
                    )
                    relation_types[relation_type] += 1
                    scopes[scope] += 1

            for related_article in related_articles:
                if not isinstance(related_article, dict):
                    counters["invalid_related_article_row_count"] += 1
                    continue
                target_eid = str(
                    related_article.get("targetEID") or ""
                ).strip()
                target_canonical_ids = eid_to_canonical_ids.get(
                    target_eid,
                    set(),
                )
                if len(target_canonical_ids) != 1:
                    counters[
                        "related_article_target_unresolved_count"
                    ] += 1
                    continue
                target_canonical_id = next(iter(target_canonical_ids))
                if source_canonical_id == target_canonical_id:
                    counters["related_article_self_count"] += 1
                    continue

                relation_type = str(
                    projection_policy[
                        "related_article_relation_type"
                    ]
                )
                relationship_id = create_stable_id(
                    identifier_policy["aks_related_article_prefix"],
                    [
                        eid,
                        target_eid,
                        source_canonical_id,
                        target_canonical_id,
                        str(projection_policy["policy_version"]),
                    ],
                    policy,
                )
                target_url = str(
                    related_article.get("targetUrl") or ""
                ).strip()
                related_article_rows.append(
                    {
                        "related_article_candidate_id": relationship_id,
                        "start_canonical_id": source_canonical_id,
                        "end_canonical_id": target_canonical_id,
                        "relation_type": relation_type,
                        "source_article_eid": eid,
                        "target_article_eid": target_eid,
                        "source_article_headword": headword,
                        "target_article_headword": str(
                            related_article.get("headword") or ""
                        ).strip(),
                        "source_url": source_url,
                        "target_url": target_url,
                        "scope": str(
                            projection_policy["discovery_scope"]
                        ),
                        "candidate_status": str(
                            projection_policy[
                                "discovery_candidate_status"
                            ]
                        ),
                        "verification_status": str(
                            projection_policy["verification_status"]
                        ),
                        "policy_version": str(
                            projection_policy["policy_version"]
                        ),
                    }
                )

    relationship_columns = [
        "attribute_relationship_id",
        "start_canonical_id",
        "end_canonical_id",
        "relation_type",
        "source_article_eid",
        "source_article_headword",
        "attribute_name",
        "attribute_value",
        "target_display_name",
        "description_mention_ids_json",
        "source_relationship_ids_json",
        "raw_relation_types_json",
        "source_datasets_json",
        "source_releases_json",
        "evidence_urls_json",
        "detail_urls_json",
        "evidence_sentences_json",
        "scopes_json",
        "evidence_count",
        "source_row_count",
        "verification_status",
        "candidate_status",
        "policy_version",
    ]
    exclusion_columns = [
        "source_article_eid",
        "source_canonical_id",
        "source_entity_type",
        "attribute_name",
        "attribute_value",
        "normalized_target_name",
        "relation_type",
        "candidate_target_canonical_ids_json",
        "exclusion_reason",
        "evidence_url",
        "policy_version",
    ]
    related_columns = [
        "related_article_candidate_id",
        "start_canonical_id",
        "end_canonical_id",
        "relation_type",
        "source_article_eid",
        "target_article_eid",
        "source_article_headword",
        "target_article_headword",
        "source_url",
        "target_url",
        "scope",
        "candidate_status",
        "verification_status",
        "policy_version",
    ]
    tables = {
        "aks_attribute_relationships": pd.DataFrame(
            relationship_rows,
            columns=relationship_columns,
        ).sort_values(
            [
                "candidate_status",
                "relation_type",
                "start_canonical_id",
                "end_canonical_id",
            ],
            kind="stable",
        ).reset_index(drop=True),
        "aks_attribute_exclusions": pd.DataFrame(
            exclusion_rows,
            columns=exclusion_columns,
        ).sort_values(
            [
                "exclusion_reason",
                "attribute_name",
                "source_article_eid",
            ],
            kind="stable",
        ).reset_index(drop=True),
        "aks_related_article_candidates": pd.DataFrame(
            related_article_rows,
            columns=related_columns,
        ).sort_values(
            [
                "start_canonical_id",
                "end_canonical_id",
                "source_article_eid",
            ],
            kind="stable",
        ).reset_index(drop=True),
    }
    for table_name, id_column in [
        (
            "aks_attribute_relationships",
            "attribute_relationship_id",
        ),
        (
            "aks_related_article_candidates",
            "related_article_candidate_id",
        ),
    ]:
        if tables[table_name][id_column].duplicated().any():
            raise ValueError(
                f"{table_name}의 안정 ID가 중복되었습니다."
            )

    connected_ids = {
        str(row["start_canonical_id"])
        for row in relationship_rows
    }.union(
        {
            str(row["end_canonical_id"])
            for row in relationship_rows
        }
    )
    statistics: dict[str, object] = {
        **{key: int(value) for key, value in counters.items()},
        "attribute_relationship_count": len(relationship_rows),
        "fact_candidate_count": int(
            scopes[str(projection_policy["fact_scope"])]
        ),
        "attribute_discovery_count": int(
            scopes[str(projection_policy["discovery_scope"])]
        ),
        "related_article_candidate_count": len(
            related_article_rows
        ),
        "exclusion_count": len(exclusion_rows),
        "connected_canonical_entity_count": len(connected_ids),
        "attribute_name_counts": dict(
            sorted(attribute_names.items())
        ),
        "relation_type_counts": dict(
            sorted(relation_types.items())
        ),
        "scope_counts": dict(sorted(scopes.items())),
        "exclusion_reason_counts": dict(
            sorted(exclusion_reasons.items())
        ),
    }
    return tables, statistics

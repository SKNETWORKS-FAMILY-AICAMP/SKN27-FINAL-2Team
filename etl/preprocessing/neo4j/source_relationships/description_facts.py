from __future__ import annotations

import re
from collections import defaultdict
from json import JSONDecodeError, dumps, loads

import pandas as pd

from source_relationships.build import create_stable_id


def parse_metadata(value: object) -> dict:
    """SourceRecord metadata JSON을 객체로 읽는다."""
    try:
        parsed = loads(str(value or "{}"))
    except (JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def is_safe_mention(
    sentence: str,
    match: re.Match,
    projection_policy: dict,
) -> bool:
    """긴 한국어 단어 내부의 우연한 부분문자열 일치를 제거한다."""
    word_character = re.compile(r"[\uac00-\ud7a3A-Za-z0-9]")
    if (
        match.start() > 0
        and word_character.fullmatch(sentence[match.start() - 1])
    ):
        return False
    tail = sentence[match.end() :]
    if not tail or not word_character.match(tail[0]):
        return True
    allowed_following_values = [
        str(value)
        for value in (
            projection_policy["following_particles"]
            + projection_policy["allowed_compound_suffixes"]
        )
    ]
    return any(
        tail.startswith(value) for value in allowed_following_values
    )


def build_description_fact_tables(
    canonical_registry: pd.DataFrame,
    source_record_candidates: pd.DataFrame,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """공식 설명문의 명시적 언급과 고정 패턴으로 사실 관계를 만든다."""
    projection_policy = policy["description_projection"]
    identifier_policy = policy["identifier"]
    minimum_length = int(projection_policy["minimum_mention_length"])
    registry_rows = canonical_registry.to_dict("records")
    canonical_ids = {
        str(row["canonical_id"]) for row in registry_rows
    }
    entity_type_by_id = {
        str(row["canonical_id"]): str(row["entity_type"])
        for row in registry_rows
    }
    display_name_by_id = {
        str(row["canonical_id"]): str(row["display_name"])
        for row in registry_rows
    }

    canonical_ids_by_name: dict[str, set[str]] = defaultdict(set)
    for row in registry_rows:
        display_name = str(row["display_name"]).strip()
        if len(display_name) < minimum_length:
            continue
        canonical_ids_by_name[display_name].add(
            str(row["canonical_id"])
        )
    canonical_id_by_unique_name = {
        name: next(iter(name_canonical_ids))
        for name, name_canonical_ids in canonical_ids_by_name.items()
        if len(name_canonical_ids) == 1
    }
    ordered_names = sorted(
        canonical_id_by_unique_name,
        key=lambda value: (-len(value), value),
    )
    mention_pattern: re.Pattern | None = None
    if ordered_names:
        mention_pattern = re.compile(
            "|".join(re.escape(name) for name in ordered_names)
        )

    source_by_record_id = {
        str(row["source_record_id"]): row
        for row in source_record_candidates.to_dict("records")
    }
    specific_person_ids: set[str] = set()
    person_hanja_by_id: dict[str, set[str]] = defaultdict(set)
    for registry_row in registry_rows:
        canonical_id = str(registry_row["canonical_id"])
        if entity_type_by_id[canonical_id] != "Person":
            continue
        source_record_ids = loads(
            str(
                registry_row["identity_member_source_ids_json"]
                or "[]"
            )
        )
        for source_record_id_value in source_record_ids:
            source_row = source_by_record_id.get(
                str(source_record_id_value),
                {},
            )
            source = str(source_row.get("source") or "")
            metadata = parse_metadata(
                source_row.get("source_metadata_json")
            )
            for metadata_field in [
                "origin",
                "headword_origin",
                "hanja",
            ]:
                metadata_value = str(
                    metadata.get(metadata_field) or ""
                )
                person_hanja_by_id[canonical_id].update(
                    re.findall(
                        r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+",
                        metadata_value,
                    )
                )
            has_identity_evidence = source == "ITKC_PERSON"
            if source == "AKS" and (
                str(metadata.get("origin") or "")
                or str(metadata.get("headword_origin") or "")
            ):
                has_identity_evidence = True
            elif (
                source == "THESAURUS"
                and str(metadata.get("hanja") or "")
            ):
                has_identity_evidence = True
            if has_identity_evidence:
                specific_person_ids.add(canonical_id)
                break
    mention_rows: dict[str, dict] = {}
    asserted_mentions: list[dict] = []
    if mention_pattern is not None:
        for registry_row in registry_rows:
            subject_canonical_id = str(registry_row["canonical_id"])
            source_record_ids = loads(
                str(
                    registry_row[
                        "identity_member_source_ids_json"
                    ]
                    or "[]"
                )
            )
            for source_record_id_value in source_record_ids:
                source_record_id = str(source_record_id_value)
                source_row = source_by_record_id.get(
                    source_record_id,
                    {},
                )
                source = str(source_row.get("source") or "")
                source_policy = projection_policy["sources"].get(
                    source,
                    {},
                )
                if not source_policy:
                    continue
                metadata = parse_metadata(
                    source_row.get("source_metadata_json")
                )
                text_field = str(source_policy["text_field"])
                description = str(
                    metadata.get(text_field) or ""
                ).strip()
                if not description:
                    continue
                sentences = re.split(
                    r"(?<=[.!?。])\s+|[\r\n]+",
                    description,
                )
                for sentence_value in sentences:
                    sentence = str(sentence_value).strip()
                    if not sentence:
                        continue
                    for mention_match in mention_pattern.finditer(
                        sentence
                    ):
                        if not is_safe_mention(
                            sentence,
                            mention_match,
                            projection_policy,
                        ):
                            continue
                        matched_name = mention_match.group(0)
                        object_canonical_id = (
                            canonical_id_by_unique_name[matched_name]
                        )
                        if object_canonical_id == subject_canonical_id:
                            continue
                        mention_id = create_stable_id(
                            identifier_policy[
                                "description_mention_prefix"
                            ],
                            [
                                subject_canonical_id,
                                object_canonical_id,
                                source_record_id,
                                sentence,
                                str(mention_match.start()),
                            ],
                            policy,
                        )
                        subject_entity_type = entity_type_by_id[
                            subject_canonical_id
                        ]
                        object_entity_type = entity_type_by_id[
                            object_canonical_id
                        ]
                        relation_type = ""
                        relation_rule_id = ""
                        uncertainty_matched = any(
                            re.search(str(pattern), sentence)
                            for pattern in projection_policy.get(
                                "uncertainty_patterns",
                                [],
                            )
                        )
                        for rule in projection_policy[
                            "relation_rules"
                        ]:
                            if uncertainty_matched:
                                continue
                            allowed_subject_types = {
                                str(value)
                                for value in rule[
                                    "subject_entity_types"
                                ]
                            }
                            allowed_object_types = {
                                str(value)
                                for value in rule[
                                    "object_entity_types"
                                ]
                            }
                            if (
                                allowed_subject_types
                                and subject_entity_type
                                not in allowed_subject_types
                            ):
                                continue
                            if (
                                allowed_object_types
                                and object_entity_type
                                not in allowed_object_types
                            ):
                                continue
                            if (
                                bool(
                                    rule.get(
                                        "requires_specific_person_object"
                                    )
                                )
                                and object_entity_type == "Person"
                                and object_canonical_id
                                not in specific_person_ids
                            ):
                                continue
                            local_parenthetical_match = re.match(
                                r"\(([^)]+)\)",
                                sentence[mention_match.end() :],
                            )
                            local_hanja_values: set[str] = set()
                            if local_parenthetical_match:
                                local_hanja_values.update(
                                    re.findall(
                                        r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+",
                                        local_parenthetical_match.group(1),
                                    )
                                )
                            if (
                                bool(
                                    rule.get(
                                        "requires_local_hanja_for_short_person_object"
                                    )
                                )
                                and object_entity_type == "Person"
                                and len(matched_name) <= 2
                                and not local_hanja_values.intersection(
                                    person_hanja_by_id.get(
                                        object_canonical_id,
                                        set(),
                                    )
                                )
                            ):
                                continue
                            pattern_matched = False
                            for template in rule["patterns"]:
                                mention_expression = (
                                    re.escape(matched_name)
                                    + r"(?:\([^)]+\))?"
                                )
                                pattern = str(template).replace(
                                    "{mention}",
                                    mention_expression,
                                )
                                if re.search(pattern, sentence):
                                    pattern_matched = True
                                    break
                            if not pattern_matched:
                                continue
                            negative_pattern_matched = False
                            for template in rule.get(
                                "negative_patterns",
                                [],
                            ):
                                mention_expression = (
                                    re.escape(matched_name)
                                    + r"(?:\([^)]+\))?"
                                )
                                pattern = str(template).replace(
                                    "{mention}",
                                    mention_expression,
                                )
                                if re.search(pattern, sentence):
                                    negative_pattern_matched = True
                                    break
                            if negative_pattern_matched:
                                continue
                            relation_type = str(
                                rule["relation_type"]
                            )
                            relation_rule_id = str(rule["rule_id"])
                            break
                        extraction_status = str(
                            projection_policy["candidate_status"]
                        )
                        if relation_type:
                            extraction_status = str(
                                projection_policy[
                                    "verification_status"
                                ]
                            )
                        source_url = str(
                            metadata.get("source_url") or ""
                        )
                        mention_row = {
                            "description_mention_id": mention_id,
                            "subject_canonical_id": (
                                subject_canonical_id
                            ),
                            "object_canonical_id": (
                                object_canonical_id
                            ),
                            "subject_name": display_name_by_id[
                                subject_canonical_id
                            ],
                            "object_name": display_name_by_id[
                                object_canonical_id
                            ],
                            "subject_entity_type": (
                                subject_entity_type
                            ),
                            "object_entity_type": object_entity_type,
                            "source_record_id": source_record_id,
                            "source": source,
                            "source_release": str(
                                source_row.get("source_release") or ""
                            ),
                            "evidence_field": text_field,
                            "evidence_sentence": sentence,
                            "evidence_url": source_url,
                            "matched_name": matched_name,
                            "match_start": str(mention_match.start()),
                            "relation_rule_id": relation_rule_id,
                            "proposed_relation_type": relation_type,
                            "extraction_status": extraction_status,
                            "policy_version": str(
                                projection_policy["policy_version"]
                            ),
                        }
                        mention_rows[mention_id] = mention_row
                        if relation_type:
                            asserted_mentions.append(mention_row)

    grouped_mentions: dict[tuple[str, str, str], list[dict]] = (
        defaultdict(list)
    )
    for mention in asserted_mentions:
        grouped_mentions[
            (
                str(mention["subject_canonical_id"]),
                str(mention["proposed_relation_type"]),
                str(mention["object_canonical_id"]),
            )
        ].append(mention)

    relationship_rows: list[dict] = []
    for keys, evidence_mentions in grouped_mentions.items():
        start_canonical_id, relation_type, end_canonical_id = keys
        relationship_id = create_stable_id(
            identifier_policy["description_relationship_prefix"],
            [
                start_canonical_id,
                relation_type,
                end_canonical_id,
                projection_policy["policy_version"],
            ],
            policy,
        )
        relationship_rows.append(
            {
                "canonical_relationship_id": relationship_id,
                "start_canonical_id": start_canonical_id,
                "end_canonical_id": end_canonical_id,
                "relation_type": relation_type,
                "description_mention_ids_json": dumps(
                    sorted(
                        {
                            str(row["description_mention_id"])
                            for row in evidence_mentions
                        }
                    ),
                    ensure_ascii=False,
                ),
                "source_relationship_ids_json": "[]",
                "raw_relation_types_json": dumps(
                    sorted(
                        {
                            str(row["relation_rule_id"])
                            for row in evidence_mentions
                        }
                    ),
                    ensure_ascii=False,
                ),
                "source_datasets_json": dumps(
                    sorted(
                        {
                            f"{row['source']}_DESCRIPTION"
                            for row in evidence_mentions
                        }
                    ),
                    ensure_ascii=False,
                ),
                "source_releases_json": dumps(
                    sorted(
                        {
                            str(row["source_release"])
                            for row in evidence_mentions
                            if str(row["source_release"])
                        }
                    ),
                    ensure_ascii=False,
                ),
                "evidence_urls_json": dumps(
                    sorted(
                        {
                            str(row["evidence_url"])
                            for row in evidence_mentions
                            if str(row["evidence_url"])
                        }
                    ),
                    ensure_ascii=False,
                ),
                "detail_urls_json": dumps(
                    sorted(
                        {
                            str(row["evidence_url"])
                            for row in evidence_mentions
                            if str(row["evidence_url"])
                        }
                    ),
                    ensure_ascii=False,
                ),
                "evidence_sentences_json": dumps(
                    sorted(
                        {
                            str(row["evidence_sentence"])
                            for row in evidence_mentions
                        }
                    ),
                    ensure_ascii=False,
                ),
                "scopes_json": '["OFFICIAL_DESCRIPTION"]',
                "evidence_count": str(len(evidence_mentions)),
                "source_row_count": str(len(evidence_mentions)),
                "verification_status": str(
                    projection_policy["verification_status"]
                ),
            }
        )

    mention_columns = [
        "description_mention_id",
        "subject_canonical_id",
        "object_canonical_id",
        "subject_name",
        "object_name",
        "subject_entity_type",
        "object_entity_type",
        "source_record_id",
        "source",
        "source_release",
        "evidence_field",
        "evidence_sentence",
        "evidence_url",
        "matched_name",
        "match_start",
        "relation_rule_id",
        "proposed_relation_type",
        "extraction_status",
        "policy_version",
    ]
    relationship_columns = [
        "canonical_relationship_id",
        "start_canonical_id",
        "end_canonical_id",
        "relation_type",
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
    ]
    mentions = pd.DataFrame(
        list(mention_rows.values()),
        columns=mention_columns,
    )
    relationships = pd.DataFrame(
        relationship_rows,
        columns=relationship_columns,
    )
    review = mentions[
        mentions["proposed_relation_type"].eq("")
    ].copy()
    review["review_status"] = str(
        projection_policy["review_status"]
    )
    review["review_reason"] = "NO_DETERMINISTIC_RELATION_PATTERN"

    relationship_endpoint_ids = set(
        relationships.get(
            "start_canonical_id",
            pd.Series(dtype=str),
        )
    ).union(
        relationships.get(
            "end_canonical_id",
            pd.Series(dtype=str),
        )
    )
    unknown_endpoint_ids = relationship_endpoint_ids.difference(
        canonical_ids
    )
    if unknown_endpoint_ids:
        raise ValueError(
            "설명문 관계가 없는 CanonicalEntity를 참조합니다: "
            f"{len(unknown_endpoint_ids)}건"
        )
    if relationships["canonical_relationship_id"].duplicated().any():
        raise ValueError("설명문 canonical 관계 ID가 중복되었습니다.")
    if (
        relationships["start_canonical_id"]
        == relationships["end_canonical_id"]
    ).any():
        raise ValueError("설명문 canonical 자기 관계가 생성됐습니다.")

    return {
        "description_mention_candidates": mentions,
        "description_canonical_relationships": relationships,
        "description_relation_review": review,
    }

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import new as new_hash
from itertools import combinations
from json import dumps
from pathlib import Path
import re

import pandas as pd

from common import load_policy_file, normalize_history_term
from choice_relation.deterministic_candidates import parse_json_list


def load_exam_relation_official_policy(policy_path: str) -> dict:
    """기출 관계 공식 검증 정책을 읽고 필수 구성을 검사한다."""
    policy = load_policy_file(Path(policy_path))
    policy_key = "exam_relation_official_corroboration"
    if policy_key not in policy:
        raise ValueError(f"{policy_key} 정책이 없습니다.")
    corroboration_policy = policy[policy_key]
    required_fields = {
        "policy_version",
        "input_candidate_statuses",
        "trusted_fact_verification_statuses",
        "minimum_recovered_name_length_with_known_endpoint",
        "minimum_recovered_name_length_without_known_endpoint",
        "accepted_registry_status",
        "official_fact_neighbor_resolution_method",
        "following_particles",
        "predicate_family_relation_types",
        "verification_statuses",
        "identifier",
        "outputs",
    }
    missing_fields = required_fields.difference(corroboration_policy)
    if missing_fields:
        missing_text = ", ".join(sorted(missing_fields))
        raise ValueError(
            f"기출 관계 공식 검증 정책 필드가 없습니다: {missing_text}"
        )
    return policy


def create_corroboration_id(
    prefix: str,
    values: list[str],
    policy: dict,
) -> str:
    """입력과 정책 버전에 고정되는 공식 검증 ID를 만든다."""
    corroboration_policy = policy[
        "exam_relation_official_corroboration"
    ]
    identifier = corroboration_policy["identifier"]
    hasher = new_hash(str(identifier["hash_algorithm"]))
    source = "|".join(
        [*values, str(corroboration_policy["policy_version"])]
    )
    hasher.update(source.encode("utf-8"))
    digest_length = int(identifier["digest_length"])
    return f"{prefix}{hasher.hexdigest()[:digest_length]}"


def build_unique_name_entries(
    canonical_registry: pd.DataFrame,
    policy: dict,
) -> list[dict]:
    """활성 Canonical 중 이름이 유일한 엔티티만 복구 색인에 넣는다."""
    corroboration_policy = policy[
        "exam_relation_official_corroboration"
    ]
    accepted_status = str(
        corroboration_policy["accepted_registry_status"]
    )
    minimum_length = int(
        corroboration_policy[
            "minimum_recovered_name_length_with_known_endpoint"
        ]
    )
    rows_by_normalized_name: dict[str, list[dict]] = defaultdict(list)
    for row in canonical_registry.to_dict("records"):
        if str(row["lifecycle_status"]) != accepted_status:
            continue
        display_name = str(row["display_name"]).strip()
        normalized_name = normalize_history_term(display_name)
        if len(normalized_name) < minimum_length:
            continue
        rows_by_normalized_name[normalized_name].append(row)

    entries: list[dict] = []
    for normalized_name, rows in rows_by_normalized_name.items():
        canonical_ids = {
            str(row["canonical_id"]).strip() for row in rows
        }
        if len(canonical_ids) != 1:
            continue
        row = rows[0]
        entries.append(
            {
                "canonical_id": next(iter(canonical_ids)),
                "display_name": str(row["display_name"]).strip(),
                "normalized_name": normalized_name,
                "entity_type": str(row["entity_type"]).strip(),
            }
        )
    entries.sort(
        key=lambda row: (
            -len(str(row["normalized_name"])),
            str(row["normalized_name"]),
        )
    )
    return entries


def collect_recovered_mentions(
    text: str,
    name_entries: list[dict],
    minimum_length: int,
    following_particles: list[str],
) -> list[dict]:
    """문장 안의 유일한 Canonical 이름을 긴 이름 우선으로 복구한다."""
    word_character = re.compile(r"[\uac00-\ud7a3A-Za-z0-9]")
    matches: list[dict] = []
    for entry in name_entries:
        normalized_name = str(entry["normalized_name"])
        if len(normalized_name) < minimum_length:
            continue
        display_name = str(entry["display_name"])
        for name_match in re.finditer(re.escape(display_name), text):
            start_index = name_match.start()
            end_index = name_match.end()
            if (
                start_index > 0
                and word_character.fullmatch(text[start_index - 1])
            ):
                continue
            tail = text[end_index:]
            safe_tail = not tail or not word_character.match(tail[0])
            if not safe_tail:
                safe_tail = any(
                    tail.startswith(particle)
                    for particle in following_particles
                )
            if not safe_tail:
                continue
            matches.append(
                {
                    **entry,
                    "start": start_index,
                    "end": end_index,
                }
            )
            break
    matches.sort(
        key=lambda row: (
            -len(str(row["normalized_name"])),
            int(row["start"]),
            str(row["canonical_id"]),
        )
    )

    selected: list[dict] = []
    occupied_spans: list[tuple[int, int]] = []
    selected_ids: set[str] = set()
    for match in matches:
        canonical_id = str(match["canonical_id"])
        if canonical_id in selected_ids:
            continue
        start_index = int(match["start"])
        end_index = int(match["end"])
        overlaps = any(
            start_index < occupied_end and end_index > occupied_start
            for occupied_start, occupied_end in occupied_spans
        )
        if overlaps:
            continue
        selected.append(match)
        selected_ids.add(canonical_id)
        occupied_spans.append((start_index, end_index))
    selected.sort(
        key=lambda row: (
            int(row["start"]),
            str(row["canonical_id"]),
        )
    )
    return selected


def build_fact_pair_index(
    canonical_facts: pd.DataFrame,
    policy: dict,
) -> dict[tuple[str, str], list[dict]]:
    """신뢰 상태의 기존 공식 사실을 무방향 endpoint 쌍으로 색인한다."""
    corroboration_policy = policy[
        "exam_relation_official_corroboration"
    ]
    trusted_statuses = {
        str(value)
        for value in corroboration_policy[
            "trusted_fact_verification_statuses"
        ]
    }
    fact_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in canonical_facts.to_dict("records"):
        if str(row["verification_status"]) not in trusted_statuses:
            continue
        start_id = str(row["start_canonical_id"]).strip()
        end_id = str(row["end_canonical_id"]).strip()
        if not start_id or not end_id or start_id == end_id:
            continue
        pair_key = tuple(sorted([start_id, end_id]))
        fact_index[pair_key].append(row)
    return dict(fact_index)


def build_fact_endpoint_index(
    fact_pair_index: dict[tuple[str, str], list[dict]],
) -> tuple[dict[str, list[dict]], list[dict]]:
    """신뢰 사실을 endpoint별로 찾을 수 있는 역색인으로 만든다."""
    facts_by_endpoint: dict[str, list[dict]] = defaultdict(list)
    facts_by_id: dict[str, dict] = {}
    for facts in fact_pair_index.values():
        for fact in facts:
            fact_id = str(fact["canonical_relationship_id"])
            facts_by_id[fact_id] = fact
    for fact in facts_by_id.values():
        start_id = str(fact["start_canonical_id"])
        end_id = str(fact["end_canonical_id"])
        facts_by_endpoint[start_id].append(fact)
        facts_by_endpoint[end_id].append(fact)
    return dict(facts_by_endpoint), list(facts_by_id.values())


def display_name_appears(
    text: str,
    display_name: str,
    minimum_length: int,
    following_particles: list[str],
) -> bool:
    """띄어쓰기 차이를 허용하되 단어 내부 부분 일치는 제외한다."""
    compact_name = re.sub(r"\s+", "", display_name)
    if len(compact_name) < minimum_length:
        return False
    name_pattern = re.compile(
        r"\s*".join(
            re.escape(character) for character in compact_name
        )
    )
    word_character = re.compile(r"[\uac00-\ud7a3A-Za-z0-9]")
    for name_match in name_pattern.finditer(text):
        if (
            name_match.start() > 0
            and word_character.fullmatch(
                text[name_match.start() - 1]
            )
        ):
            continue
        tail = text[name_match.end():]
        safe_tail = not tail or not word_character.match(tail[0])
        if not safe_tail:
            safe_tail = any(
                tail.startswith(particle)
                for particle in following_particles
            )
        if safe_tail:
            return True
    return False


def recover_fact_neighbor(
    text: str,
    seed_ids: set[str],
    allowed_relation_types: set[str],
    facts_by_endpoint: dict[str, list[dict]],
    trusted_facts: list[dict],
    registry_by_id: dict[str, dict],
    minimum_length: int,
    following_particles: list[str],
) -> dict:
    """문장 표기와 기존 공식 사실이 유일하게 만나는 endpoint를 찾는다."""
    candidate_facts: dict[str, dict] = {}
    if seed_ids:
        for seed_id in seed_ids:
            for fact in facts_by_endpoint.get(seed_id, []):
                candidate_facts[
                    str(fact["canonical_relationship_id"])
                ] = fact
    if not seed_ids:
        candidate_facts = {
            str(fact["canonical_relationship_id"]): fact
            for fact in trusted_facts
        }
    matching_facts: list[dict] = []
    for fact in candidate_facts.values():
        if str(fact["relation_type"]) not in allowed_relation_types:
            continue
        endpoint_ids = {
            str(fact["start_canonical_id"]),
            str(fact["end_canonical_id"]),
        }
        if seed_ids and not seed_ids.intersection(endpoint_ids):
            continue
        unmatched_ids = endpoint_ids.difference(seed_ids)
        if not unmatched_ids:
            continue
        surface_match = True
        for endpoint_id in unmatched_ids:
            registry_row = registry_by_id.get(endpoint_id)
            if registry_row is None:
                surface_match = False
                break
            if not display_name_appears(
                text,
                str(registry_row["display_name"]),
                minimum_length,
                following_particles,
            ):
                surface_match = False
                break
        if surface_match:
            matching_facts.append(fact)
    if len(matching_facts) == 1:
        return matching_facts[0]
    return {}


def merge_fact_json_values(
    facts: list[dict],
    column: str,
) -> str:
    """여러 공식 사실 행의 JSON 배열 값을 중복 없이 합친다."""
    values: set[str] = set()
    for fact in facts:
        values.update(parse_json_list(fact.get(column, "")))
    return dumps(sorted(values), ensure_ascii=False)


def verify_exam_relation_candidate(
    candidate: dict,
    name_entries: list[dict],
    fact_pair_index: dict[tuple[str, str], list[dict]],
    facts_by_endpoint: dict[str, list[dict]],
    trusted_facts: list[dict],
    registry_by_id: dict[str, dict],
    policy: dict,
) -> dict:
    """한 기출 관계 후보를 기존 공식 사실과 보수적으로 대조한다."""
    corroboration_policy = policy[
        "exam_relation_official_corroboration"
    ]
    statuses = corroboration_policy["verification_statuses"]
    identifier = corroboration_policy["identifier"]
    existing_ids = {
        str(candidate.get("start_canonical_id") or "").strip(),
        str(candidate.get("end_canonical_id") or "").strip(),
    }
    existing_ids.discard("")
    minimum_length = int(
        corroboration_policy[
            "minimum_recovered_name_length_without_known_endpoint"
        ]
    )
    if existing_ids:
        minimum_length = int(
            corroboration_policy[
                "minimum_recovered_name_length_with_known_endpoint"
            ]
        )
    mentions = collect_recovered_mentions(
        str(candidate["evidence_text"]),
        name_entries,
        minimum_length,
        [
            str(value)
            for value in corroboration_policy["following_particles"]
        ],
    )
    mentioned_ids = {
        str(mention["canonical_id"]) for mention in mentions
    }
    candidate_ids = existing_ids.union(mentioned_ids)

    predicate_families = parse_json_list(
        candidate.get("predicate_families_json", "")
    )
    allowed_relation_types: set[str] = set()
    relation_type_policy = corroboration_policy[
        "predicate_family_relation_types"
    ]
    for family in predicate_families:
        allowed_relation_types.update(
            str(value)
            for value in relation_type_policy.get(family, [])
        )
    following_particles = [
        str(value)
        for value in corroboration_policy["following_particles"]
    ]
    recovered_neighbor_fact: dict = {}
    if len(candidate_ids) < 2 and allowed_relation_types:
        recovered_neighbor_fact = recover_fact_neighbor(
            str(candidate["evidence_text"]),
            candidate_ids,
            allowed_relation_types,
            facts_by_endpoint,
            trusted_facts,
            registry_by_id,
            minimum_length,
            following_particles,
        )
        if recovered_neighbor_fact:
            candidate_ids.update(
                {
                    str(
                        recovered_neighbor_fact[
                            "start_canonical_id"
                        ]
                    ),
                    str(
                        recovered_neighbor_fact[
                            "end_canonical_id"
                        ]
                    ),
                }
            )

    pair_keys: list[tuple[str, str]] = []
    if len(existing_ids) >= 2:
        pair_keys = [tuple(sorted(existing_ids))]
    elif len(existing_ids) == 1:
        existing_id = next(iter(existing_ids))
        pair_keys = [
            tuple(sorted([existing_id, other_id]))
            for other_id in candidate_ids
            if other_id != existing_id
        ]
    elif len(existing_ids) == 0:
        pair_keys = [
            tuple(sorted(pair))
            for pair in combinations(sorted(candidate_ids), 2)
        ]

    matched_facts_by_id: dict[str, dict] = {}
    for pair_key in sorted(set(pair_keys)):
        for fact in fact_pair_index.get(pair_key, []):
            fact_id = str(fact["canonical_relationship_id"])
            matched_facts_by_id[fact_id] = fact
    matched_facts = list(matched_facts_by_id.values())

    compatible_facts = [
        fact
        for fact in matched_facts
        if str(fact["relation_type"]) in allowed_relation_types
    ]
    if recovered_neighbor_fact:
        matched_facts = [recovered_neighbor_fact]
        compatible_facts = [recovered_neighbor_fact]

    verification_status = str(
        statuses["official_fact_not_found"]
    )
    if len(candidate_ids) < 2:
        verification_status = str(statuses["endpoints_unresolved"])
    elif matched_facts and not allowed_relation_types:
        verification_status = str(statuses["predicate_unresolved"])
    elif len(compatible_facts) == 1:
        verification_status = str(statuses["verified"])
    elif len(compatible_facts) > 1:
        verification_status = str(
            statuses["ambiguous_official_fact"]
        )
    elif matched_facts and allowed_relation_types:
        verification_status = str(
            statuses["relation_type_mismatch"]
        )

    resolved_fact: dict = {}
    endpoint_resolution_method = ""
    if verification_status == str(statuses["verified"]):
        resolved_fact = compatible_facts[0]
        endpoint_resolution_method = "RECOVERED_BOTH_ENDPOINTS"
        if len(existing_ids) >= 2:
            endpoint_resolution_method = "EXISTING_PAIR"
        elif len(existing_ids) == 1:
            endpoint_resolution_method = "RECOVERED_ONE_ENDPOINT"
        if recovered_neighbor_fact:
            endpoint_resolution_method = str(
                corroboration_policy[
                    "official_fact_neighbor_resolution_method"
                ]
            )

    evidence_facts = compatible_facts
    if not evidence_facts:
        evidence_facts = matched_facts
    recovered_mentions = [
        {
            "canonical_id": str(mention["canonical_id"]),
            "display_name": str(mention["display_name"]),
            "entity_type": str(mention["entity_type"]),
        }
        for mention in mentions
        if str(mention["canonical_id"]) not in existing_ids
    ]
    recovered_mention_ids = {
        str(mention["canonical_id"])
        for mention in recovered_mentions
    }
    if recovered_neighbor_fact:
        fact_endpoint_ids = {
            str(recovered_neighbor_fact["start_canonical_id"]),
            str(recovered_neighbor_fact["end_canonical_id"]),
        }
        for endpoint_id in sorted(
            fact_endpoint_ids.difference(existing_ids)
        ):
            if endpoint_id in recovered_mention_ids:
                continue
            registry_row = registry_by_id[endpoint_id]
            recovered_mentions.append(
                {
                    "canonical_id": endpoint_id,
                    "display_name": str(
                        registry_row["display_name"]
                    ),
                    "entity_type": str(
                        registry_row["entity_type"]
                    ),
                }
            )
    verification_id = create_corroboration_id(
        str(identifier["verification_prefix"]),
        [str(candidate["exam_relation_candidate_id"])],
        policy,
    )
    verified_status = str(statuses["verified"])
    return {
        "exam_relation_verification_id": verification_id,
        "exam_relation_candidate_id": str(
            candidate["exam_relation_candidate_id"]
        ),
        "claim_segment_id": str(candidate["claim_segment_id"]),
        "problem_id": str(candidate["problem_id"]),
        "original_candidate_status": str(
            candidate["candidate_status"]
        ),
        "verification_status": verification_status,
        "endpoint_resolution_method": endpoint_resolution_method,
        "resolved_start_canonical_id": str(
            resolved_fact.get("start_canonical_id") or ""
        ),
        "resolved_end_canonical_id": str(
            resolved_fact.get("end_canonical_id") or ""
        ),
        "resolved_relation_type": str(
            resolved_fact.get("relation_type") or ""
        ),
        "existing_canonical_ids_json": dumps(
            sorted(existing_ids),
            ensure_ascii=False,
        ),
        "recovered_mentions_json": dumps(
            recovered_mentions,
            ensure_ascii=False,
        ),
        "predicate_families_json": dumps(
            sorted(predicate_families),
            ensure_ascii=False,
        ),
        "matched_official_fact_ids_json": dumps(
            sorted(
                str(fact["canonical_relationship_id"])
                for fact in evidence_facts
            ),
            ensure_ascii=False,
        ),
        "official_relation_types_json": dumps(
            sorted(
                {
                    str(fact["relation_type"])
                    for fact in evidence_facts
                }
            ),
            ensure_ascii=False,
        ),
        "official_source_datasets_json": merge_fact_json_values(
            evidence_facts,
            "source_datasets_json",
        ),
        "official_evidence_urls_json": merge_fact_json_values(
            evidence_facts,
            "evidence_urls_json",
        ),
        "official_evidence_sentences_json": merge_fact_json_values(
            evidence_facts,
            "evidence_sentences_json",
        ),
        "exam_evidence_text": str(candidate["evidence_text"]),
        "can_link_to_existing_fact": (
            verification_status == verified_status
        ),
        "may_create_new_fact": False,
        "llm_used": False,
        "policy_version": str(corroboration_policy["policy_version"]),
    }


def validate_official_corroboration_tables(
    tables: dict[str, pd.DataFrame],
    canonical_facts: pd.DataFrame,
    policy: dict,
) -> list[str]:
    """공식 검증 결과가 새로운 사실을 만들지 않는지 검사한다."""
    corroboration_policy = policy[
        "exam_relation_official_corroboration"
    ]
    verified_status = str(
        corroboration_policy["verification_statuses"]["verified"]
    )
    checks = tables["official_checks"]
    links = tables["verified_links"]
    errors: list[str] = []
    if checks["exam_relation_verification_id"].duplicated().any():
        errors.append("공식 검증 ID가 중복되었습니다.")
    if checks["exam_relation_candidate_id"].duplicated().any():
        errors.append("한 후보에 공식 검증 결과가 여러 개입니다.")
    if checks["may_create_new_fact"].eq(True).any():
        errors.append("기출 검증 결과가 새 사실 생성을 허용했습니다.")
    invalid_link_flags = checks[
        checks["can_link_to_existing_fact"].eq(True)
        & checks["verification_status"].ne(verified_status)
    ]
    if not invalid_link_flags.empty:
        errors.append("검증되지 않은 후보에 기존 사실 링크가 허용됐습니다.")

    official_fact_ids = {
        str(value)
        for value in canonical_facts["canonical_relationship_id"]
    }
    unknown_fact_links = links[
        ~links["canonical_relationship_id"].isin(official_fact_ids)
    ]
    if not unknown_fact_links.empty:
        errors.append("검증 링크가 존재하지 않는 공식 사실을 참조합니다.")
    self_links = links[
        links["start_canonical_id"].eq(links["end_canonical_id"])
    ]
    if not self_links.empty:
        errors.append("검증 링크에 자기 관계가 있습니다.")
    return errors


def build_exam_relation_official_corroboration_tables(
    relation_candidates: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    canonical_facts: pd.DataFrame,
    policy: dict,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """기출 관계 후보를 기존 공식 사실에 연결할 감사표를 만든다."""
    corroboration_policy = policy[
        "exam_relation_official_corroboration"
    ]
    required_candidate_columns = {
        "exam_relation_candidate_id",
        "claim_segment_id",
        "problem_id",
        "candidate_status",
        "start_canonical_id",
        "end_canonical_id",
        "predicate_families_json",
        "evidence_text",
    }
    missing_candidate_columns = required_candidate_columns.difference(
        relation_candidates.columns
    )
    if missing_candidate_columns:
        missing_text = ", ".join(sorted(missing_candidate_columns))
        raise ValueError(
            f"기출 관계 후보 필수 컬럼이 없습니다: {missing_text}"
        )
    input_statuses = {
        str(value)
        for value in corroboration_policy["input_candidate_statuses"]
    }
    eligible_candidates = relation_candidates[
        relation_candidates["candidate_status"].isin(input_statuses)
    ].copy()
    name_entries = build_unique_name_entries(
        canonical_registry,
        policy,
    )
    fact_pair_index = build_fact_pair_index(canonical_facts, policy)
    facts_by_endpoint, trusted_facts = build_fact_endpoint_index(
        fact_pair_index
    )
    accepted_status = str(
        corroboration_policy["accepted_registry_status"]
    )
    registry_by_id = {
        str(row["canonical_id"]): row
        for row in canonical_registry.to_dict("records")
        if str(row["lifecycle_status"]) == accepted_status
    }
    check_rows = [
        verify_exam_relation_candidate(
            candidate,
            name_entries,
            fact_pair_index,
            facts_by_endpoint,
            trusted_facts,
            registry_by_id,
            policy,
        )
        for candidate in eligible_candidates.to_dict("records")
    ]
    check_columns = [
        "exam_relation_verification_id",
        "exam_relation_candidate_id",
        "claim_segment_id",
        "problem_id",
        "original_candidate_status",
        "verification_status",
        "endpoint_resolution_method",
        "resolved_start_canonical_id",
        "resolved_end_canonical_id",
        "resolved_relation_type",
        "existing_canonical_ids_json",
        "recovered_mentions_json",
        "predicate_families_json",
        "matched_official_fact_ids_json",
        "official_relation_types_json",
        "official_source_datasets_json",
        "official_evidence_urls_json",
        "official_evidence_sentences_json",
        "exam_evidence_text",
        "can_link_to_existing_fact",
        "may_create_new_fact",
        "llm_used",
        "policy_version",
    ]
    official_checks = pd.DataFrame(check_rows, columns=check_columns)
    verified_status = str(
        corroboration_policy["verification_statuses"]["verified"]
    )
    verified_checks = official_checks[
        official_checks["verification_status"].eq(verified_status)
    ]
    check_by_candidate_id = {
        str(row["exam_relation_candidate_id"]): row
        for row in verified_checks.to_dict("records")
    }
    fact_by_id = {
        str(row["canonical_relationship_id"]): row
        for row in canonical_facts.to_dict("records")
    }
    link_rows: list[dict] = []
    identifier = corroboration_policy["identifier"]
    for candidate_id, check in check_by_candidate_id.items():
        fact_ids = parse_json_list(
            check["matched_official_fact_ids_json"]
        )
        if len(fact_ids) != 1:
            continue
        fact_id = fact_ids[0]
        fact = fact_by_id[fact_id]
        link_id = create_corroboration_id(
            str(identifier["link_prefix"]),
            [candidate_id, fact_id],
            policy,
        )
        link_rows.append(
            {
                "exam_official_fact_link_id": link_id,
                "exam_relation_candidate_id": candidate_id,
                "claim_segment_id": str(check["claim_segment_id"]),
                "problem_id": str(check["problem_id"]),
                "canonical_relationship_id": fact_id,
                "start_canonical_id": str(
                    fact["start_canonical_id"]
                ),
                "end_canonical_id": str(fact["end_canonical_id"]),
                "relation_type": str(fact["relation_type"]),
                "endpoint_resolution_method": str(
                    check["endpoint_resolution_method"]
                ),
                "exam_evidence_text": str(
                    check["exam_evidence_text"]
                ),
                "official_evidence_urls_json": str(
                    check["official_evidence_urls_json"]
                ),
                "official_evidence_sentences_json": str(
                    check["official_evidence_sentences_json"]
                ),
                "creates_new_fact": False,
                "policy_version": str(
                    corroboration_policy["policy_version"]
                ),
            }
        )
    link_columns = [
        "exam_official_fact_link_id",
        "exam_relation_candidate_id",
        "claim_segment_id",
        "problem_id",
        "canonical_relationship_id",
        "start_canonical_id",
        "end_canonical_id",
        "relation_type",
        "endpoint_resolution_method",
        "exam_evidence_text",
        "official_evidence_urls_json",
        "official_evidence_sentences_json",
        "creates_new_fact",
        "policy_version",
    ]
    verified_links = pd.DataFrame(link_rows, columns=link_columns)
    tables = {
        "official_checks": official_checks,
        "verified_links": verified_links,
    }
    validation_errors = validate_official_corroboration_tables(
        tables,
        canonical_facts,
        policy,
    )
    if validation_errors:
        raise ValueError("; ".join(validation_errors))

    statistics: dict[str, object] = {
        "input_candidate_count": len(relation_candidates),
        "eligible_candidate_count": len(eligible_candidates),
        "skipped_candidate_count": (
            len(relation_candidates) - len(eligible_candidates)
        ),
        "unique_registry_name_count": len(name_entries),
        "trusted_official_fact_count": sum(
            len(rows) for rows in fact_pair_index.values()
        ),
        "verification_status_counts": dict(
            Counter(
                str(value)
                for value in official_checks["verification_status"]
            )
        ),
        "verified_link_count": len(verified_links),
        "verified_problem_count": int(
            verified_links["problem_id"].nunique()
        ),
        "endpoint_resolution_method_counts": dict(
            Counter(
                str(value)
                for value in verified_links[
                    "endpoint_resolution_method"
                ]
            )
        ),
        "new_fact_creation_count": int(
            verified_links["creates_new_fact"].eq(True).sum()
        ),
        "llm_used": False,
        "neo4j_load": False,
    }
    return tables, statistics

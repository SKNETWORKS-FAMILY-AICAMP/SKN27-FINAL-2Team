from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import new as new_hash
from itertools import combinations
from json import JSONDecodeError, dumps, loads
from pathlib import Path
import re

import pandas as pd

from common import load_policy_file
from choice_relation.deterministic_candidates import (
    parse_json_list,
)
from choice_relation.safe_disambiguation import (
    build_safe_aks_disambiguation,
)
from choice_relation.relation_frames import (
    find_atomic_clause_spans,
    find_clause_mentions,
    find_pattern_occurrences,
    locate_clause_span,
    pair_is_ready,
)


def load_exam_relation_official_text_policy(
    policy_path: str,
) -> dict:
    """AKS 원문 기반 기출 관계 검증 정책을 읽는다."""
    policy = load_policy_file(Path(policy_path))
    policy_key = "exam_relation_official_text_corroboration"
    if policy_key not in policy:
        raise ValueError(f"{policy_key} 정책이 없습니다.")
    text_policy = policy[policy_key]
    required_fields = {
        "policy_version",
        "source",
        "source_record_prefix",
        "text_fields",
        "supported_check_statuses",
        "uncertainty_patterns",
        "negation_patterns",
        "maximum_mentions_per_sentence",
        "maximum_evidence_per_candidate",
        "maximum_discovered_pairs_per_candidate",
        "maximum_sentence_length",
        "minimum_two_explicit_source_count",
        "safe_disambiguation",
        "search_statuses",
        "identifier",
        "outputs",
    }
    missing_fields = required_fields.difference(text_policy)
    if missing_fields:
        missing_text = ", ".join(sorted(missing_fields))
        raise ValueError(
            f"AKS 원문 검증 정책 필드가 없습니다: {missing_text}"
        )
    return policy


def create_text_evidence_id(
    candidate_id: str,
    article_id: str,
    sentence: str,
    endpoint_pair: tuple[str, str],
    policy: dict,
) -> str:
    """후보·문서·근거 문장에 고정되는 원문 근거 ID를 만든다."""
    text_policy = policy[
        "exam_relation_official_text_corroboration"
    ]
    identifier = text_policy["identifier"]
    hasher = new_hash(str(identifier["hash_algorithm"]))
    source = "|".join(
        [
            candidate_id,
            article_id,
            sentence,
            *endpoint_pair,
            str(text_policy["policy_version"]),
        ]
    )
    hasher.update(source.encode("utf-8"))
    digest_length = int(identifier["digest_length"])
    return (
        f"{identifier['evidence_prefix']}"
        f"{hasher.hexdigest()[:digest_length]}"
    )


def build_official_text_search_contracts(
    official_checks: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    policy: dict,
    trusted_aks_eid_map: dict[str, str] | None = None,
) -> tuple[
    dict[tuple[str, str], list[dict]],
    dict[str, list[dict]],
    dict[str, str],
    dict[str, str],
    dict[str, str],
    dict[str, dict],
    list[dict],
]:
    """검증할 endpoint 쌍과 안전한 이름·AKS 문서 주체 색인을 만든다."""
    text_policy = policy[
        "exam_relation_official_text_corroboration"
    ]
    corroboration_policy = policy[
        "exam_relation_official_corroboration"
    ]
    statuses = text_policy["search_statuses"]
    supported_check_statuses = {
        str(value)
        for value in text_policy["supported_check_statuses"]
    }
    accepted_registry_status = str(
        corroboration_policy["accepted_registry_status"]
    )
    registry_rows = [
        row
        for row in canonical_registry.to_dict("records")
        if str(row["lifecycle_status"]) == accepted_registry_status
    ]
    registry_by_id = {
        str(row["canonical_id"]): row for row in registry_rows
    }
    canonical_ids_by_name: dict[str, set[str]] = defaultdict(set)
    canonical_ids_by_eid: dict[str, set[str]] = defaultdict(set)
    source_prefix = str(text_policy["source_record_prefix"])
    for row in registry_rows:
        canonical_id = str(row["canonical_id"])
        display_name = str(row["display_name"]).strip()
        if display_name:
            canonical_ids_by_name[display_name].add(canonical_id)
        for source_record_id in parse_json_list(
            row.get("identity_member_source_ids_json", "")
        ):
            if not source_record_id.startswith(source_prefix):
                continue
            source_parts = source_record_id.split(":")
            if len(source_parts) < 3:
                continue
            canonical_ids_by_eid[source_parts[2]].add(canonical_id)

    canonical_id_by_unique_name = {
        name: next(iter(canonical_ids))
        for name, canonical_ids in canonical_ids_by_name.items()
        if len(canonical_ids) == 1 and len(name) >= 2
    }
    canonical_id_by_unique_eid = {
        eid: next(iter(canonical_ids))
        for eid, canonical_ids in canonical_ids_by_eid.items()
        if len(canonical_ids) == 1
    }
    if trusted_aks_eid_map:
        canonical_id_by_unique_eid.update(trusted_aks_eid_map)

    tasks_by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    fragment_tasks_by_seed: dict[str, list[dict]] = defaultdict(list)
    check_seeds: list[dict] = []
    task_endpoint_ids: set[str] = set()
    for check in official_checks.to_dict("records"):
        candidate_id = str(check["exam_relation_candidate_id"])
        check_status = str(check["verification_status"])
        predicate_families = parse_json_list(
            check["predicate_families_json"]
        )
        existing_ids = set(
            parse_json_list(check["existing_canonical_ids_json"])
        )
        recovered_values = loads(
            str(check["recovered_mentions_json"] or "[]")
        )
        recovered_ids = {
            str(value.get("canonical_id") or "")
            for value in recovered_values
            if isinstance(value, dict)
        }
        recovered_ids.discard("")
        candidate_ids = existing_ids.union(recovered_ids)
        pair_keys: list[tuple[str, str]] = []
        if len(existing_ids) >= 2:
            pair_keys = [tuple(sorted(existing_ids))]
        elif len(existing_ids) == 1:
            existing_id = next(iter(existing_ids))
            pair_keys = [
                tuple(sorted([existing_id, recovered_id]))
                for recovered_id in recovered_ids
                if recovered_id != existing_id
            ]
        elif len(existing_ids) == 0:
            pair_keys = [
                tuple(sorted(pair))
                for pair in combinations(sorted(candidate_ids), 2)
            ]
        pair_keys = [
            pair_key
            for pair_key in sorted(set(pair_keys))
            if len(pair_key) == 2
            and pair_key[0] in registry_by_id
            and pair_key[1] in registry_by_id
        ]

        search_status = str(statuses["no_support"])
        endpoint_recovery_mode = "FIXED_PAIR"
        if check_status not in supported_check_statuses:
            search_status = str(statuses["already_verified"])
        elif not predicate_families:
            search_status = str(statuses["predicate_missing"])
        elif not pair_keys and len(candidate_ids) != 1:
            search_status = str(statuses["endpoints_missing"])
        elif not pair_keys and len(candidate_ids) == 1:
            endpoint_recovery_mode = "DISCOVER_OFFICIAL_ENDPOINT"
        check_seeds.append(
            {
                "exam_relation_candidate_id": candidate_id,
                "claim_segment_id": str(check["claim_segment_id"]),
                "problem_id": str(check["problem_id"]),
                "official_fact_check_status": check_status,
                "search_status": search_status,
                "predicate_families_json": dumps(
                    sorted(predicate_families),
                    ensure_ascii=False,
                ),
                "searched_endpoint_pair_count": len(pair_keys),
                "endpoint_recovery_mode": endpoint_recovery_mode,
                "exam_evidence_text": str(
                    check["exam_evidence_text"]
                ),
            }
        )
        if search_status != str(statuses["no_support"]):
            continue
        if endpoint_recovery_mode == "DISCOVER_OFFICIAL_ENDPOINT":
            seed_id = next(iter(candidate_ids))
            if seed_id not in registry_by_id:
                continue
            fragment_tasks_by_seed[seed_id].append(
                {
                    "exam_relation_candidate_id": candidate_id,
                    "claim_segment_id": str(check["claim_segment_id"]),
                    "problem_id": str(check["problem_id"]),
                    "seed_canonical_id": seed_id,
                    "predicate_families": set(
                        predicate_families
                    ),
                    "predicate_patterns_by_family": (
                        collect_predicate_patterns(
                            str(check["exam_evidence_text"]),
                            policy,
                        )
                    ),
                    "exam_evidence_text": str(
                        check["exam_evidence_text"]
                    ),
                }
            )
            task_endpoint_ids.add(seed_id)
            continue
        for pair_key in pair_keys:
            start_row = registry_by_id[pair_key[0]]
            end_row = registry_by_id[pair_key[1]]
            task = {
                "exam_relation_candidate_id": candidate_id,
                "claim_segment_id": str(check["claim_segment_id"]),
                "problem_id": str(check["problem_id"]),
                "endpoint_pair": pair_key,
                "start_name": str(start_row["display_name"]).strip(),
                "end_name": str(end_row["display_name"]).strip(),
                "predicate_families": set(predicate_families),
                "predicate_patterns_by_family": (
                    collect_predicate_patterns(
                        str(check["exam_evidence_text"]),
                        policy,
                    )
                ),
                "exam_evidence_text": str(
                    check["exam_evidence_text"]
                ),
            }
            tasks_by_pair[pair_key].append(task)
            task_endpoint_ids.update(pair_key)

    explicit_name_index = {
        name: canonical_id
        for name, canonical_id in canonical_id_by_unique_name.items()
        if canonical_id in task_endpoint_ids
    }
    return (
        dict(tasks_by_pair),
        dict(fragment_tasks_by_seed),
        explicit_name_index,
        canonical_id_by_unique_eid,
        {
            str(row["canonical_id"]): str(
                row["display_name"]
            ).strip()
            for row in registry_rows
        },
        registry_by_id,
        check_seeds,
    )


def extract_article_sentences(
    article: dict,
    policy: dict,
) -> list[tuple[str, str]]:
    """AKS 레코드의 검증 대상 필드를 짧은 문장 단위로 나눈다."""
    text_policy = policy[
        "exam_relation_official_text_corroboration"
    ]
    maximum_length = int(text_policy["maximum_sentence_length"])
    sentences: list[tuple[str, str]] = []
    observed_sentences: set[str] = set()
    for field in text_policy["text_fields"]:
        field_name = str(field)
        text = str(article.get(field_name) or "").strip()
        if not text:
            continue
        for sentence_value in re.split(
            r"(?<=[.!?。])\s+|[\r\n]+",
            text,
        ):
            sentence = str(sentence_value).strip(" #\t")
            if not sentence or len(sentence) > maximum_length:
                continue
            if sentence in observed_sentences:
                continue
            observed_sentences.add(sentence)
            sentences.append((field_name, sentence))
    return sentences


def collect_predicate_patterns(
    text: str,
    policy: dict,
) -> dict[str, set[str]]:
    """문장에 실제로 나타난 관계 계열별 서술어 표지를 모은다."""
    patterns_by_family: dict[str, set[str]] = defaultdict(set)
    for rule in policy["exam_relation_candidates"][
        "relationship_trigger_rules"
    ]:
        family = str(rule["predicate_family"])
        for pattern_value in rule["patterns"]:
            pattern = str(pattern_value)
            match_start = text.find(pattern)
            while match_start >= 0:
                if (
                    match_start == 0
                    or not text[match_start - 1].isalnum()
                ):
                    patterns_by_family[family].add(pattern)
                    break
                match_start = text.find(pattern, match_start + 1)
    return dict(patterns_by_family)


def official_sentence_has_role_pair(
    sentence: str,
    endpoint_pair: tuple[str, str],
    shared_patterns_by_family: dict[str, set[str]],
    registry_by_id: dict[str, dict],
    policy: dict,
) -> bool:
    """공식 문장에서 두 endpoint가 실제 허용 역할쌍인지 검사한다."""
    text_policy = policy[
        "exam_relation_official_text_corroboration"
    ]
    linked_eid_pattern = re.compile(
        str(
            text_policy["safe_disambiguation"][
                "linked_eid_pattern"
            ]
        )
    )
    clean_sentence = linked_eid_pattern.sub(
        lambda match: match.group(0).split("](", 1)[0].lstrip("["),
        sentence,
    )
    endpoint_rows = [
        registry_by_id[endpoint_id]
        for endpoint_id in endpoint_pair
        if endpoint_id in registry_by_id
    ]
    if len(endpoint_rows) != 2:
        return False
    frame_policy = policy["exam_relation_frames"]
    non_assertive_pattern = re.compile(
        str(frame_policy["non_assertive_suffix_pattern"])
    )
    assertive_light_verb_pattern = re.compile(
        str(frame_policy["assertive_light_verb_suffix_pattern"])
    )
    passive_pattern = re.compile(
        str(frame_policy["passive_suffix_pattern"])
    )
    clause_spans = find_atomic_clause_spans(
        clean_sentence,
        policy,
    )
    for predicate_family, predicate_patterns in (
        shared_patterns_by_family.items()
    ):
        for predicate_pattern in predicate_patterns:
            for action_start, action_end in find_pattern_occurrences(
                clean_sentence,
                predicate_pattern,
            ):
                clause_span = locate_clause_span(
                    action_start,
                    action_end,
                    clause_spans,
                )
                action_suffix = clean_sentence[
                    action_end:clause_span[1]
                ]
                action_asserted = bool(
                    assertive_light_verb_pattern.search(action_suffix)
                ) or not bool(
                    non_assertive_pattern.search(action_suffix)
                )
                if not action_asserted:
                    continue
                voice = "ACTIVE"
                if passive_pattern.search(action_suffix):
                    voice = "PASSIVE"
                mentions = find_clause_mentions(
                    clean_sentence,
                    clause_span,
                    endpoint_rows,
                    voice,
                    policy,
                )
                mentions_before_action = [
                    mention
                    for mention in mentions
                    if int(mention["mention_end"]) <= action_start
                ]
                ready, ready_ids = pair_is_ready(
                    predicate_family,
                    mentions_before_action,
                    policy,
                )
                if ready and set(ready_ids) == set(endpoint_pair):
                    return True
    return False


def scan_aks_official_text(
    aks_details_path: str,
    tasks_by_pair: dict[tuple[str, str], list[dict]],
    fragment_tasks_by_seed: dict[str, list[dict]],
    explicit_name_index: dict[str, str],
    canonical_id_by_eid: dict[str, str],
    canonical_name_by_id: dict[str, str],
    registry_by_id: dict[str, dict],
    policy: dict,
) -> tuple[list[dict], dict[str, int]]:
    """AKS JSONL을 한 번 순회하며 endpoint·서술어 동시 근거를 찾는다."""
    text_policy = policy[
        "exam_relation_official_text_corroboration"
    ]
    linked_eid_pattern = re.compile(
        str(
            text_policy["safe_disambiguation"][
                "linked_eid_pattern"
            ]
        )
    )
    corroboration_policy = policy[
        "exam_relation_official_corroboration"
    ]
    ordered_names = sorted(
        explicit_name_index,
        key=lambda value: (-len(value), value),
    )
    name_pattern: re.Pattern | None = None
    if ordered_names:
        name_pattern = re.compile(
            "|".join(re.escape(name) for name in ordered_names)
        )
    following_particles = [
        str(value)
        for value in corroboration_policy["following_particles"]
    ]
    word_character = re.compile(r"[\uac00-\ud7a3A-Za-z0-9]")
    uncertainty_patterns = [
        str(value) for value in text_policy["uncertainty_patterns"]
    ]
    negation_patterns = [
        str(value) for value in text_policy["negation_patterns"]
    ]
    maximum_mentions = int(
        text_policy["maximum_mentions_per_sentence"]
    )
    maximum_evidence = int(
        text_policy["maximum_evidence_per_candidate"]
    )
    maximum_discovered_pairs = int(
        text_policy["maximum_discovered_pairs_per_candidate"]
    )
    evidence_count_by_candidate_pair: Counter = Counter()
    discovered_pairs_by_candidate: dict[
        str,
        set[tuple[str, str]],
    ] = defaultdict(set)
    evidence_keys: set[
        tuple[str, str, str, tuple[str, str]]
    ] = set()
    evidence_rows: list[dict] = []
    scan_statistics = {
        "article_count": 0,
        "aks_list_invalid_json_count": 0,
        "sentence_count": 0,
    }
    with Path(aks_details_path).open(
        "r",
        encoding="utf-8",
    ) as source_file:
        for line in source_file:
            try:
                article = loads(line)
            except (JSONDecodeError, TypeError):
                scan_statistics["invalid_json_count"] += 1
                continue
            if not isinstance(article, dict):
                scan_statistics["invalid_json_count"] += 1
                continue
            scan_statistics["article_count"] += 1
            article_id = str(article.get("eid") or "")
            subject_id = canonical_id_by_eid.get(article_id, "")
            for evidence_field, sentence in extract_article_sentences(
                article,
                policy,
            ):
                scan_statistics["sentence_count"] += 1
                if any(
                    pattern in sentence
                    for pattern in uncertainty_patterns
                ):
                    continue
                if any(
                    pattern in sentence for pattern in negation_patterns
                ):
                    continue
                sentence_patterns_by_family = (
                    collect_predicate_patterns(sentence, policy)
                )
                sentence_families = set(
                    sentence_patterns_by_family
                )
                if not sentence_families:
                    continue
                explicit_ids: set[str] = set()
                linked_ids: set[str] = set()
                for eid_match in linked_eid_pattern.finditer(sentence):
                    linked_canonical_id = canonical_id_by_eid.get(
                        str(eid_match.group("eid")),
                        "",
                    )
                    if linked_canonical_id:
                        explicit_ids.add(linked_canonical_id)
                        linked_ids.add(linked_canonical_id)
                name_matches = []
                if name_pattern is not None:
                    name_matches = name_pattern.finditer(sentence)
                for name_match in name_matches:
                    if (
                        name_match.start() > 0
                        and word_character.fullmatch(
                            sentence[name_match.start() - 1]
                        )
                    ):
                        continue
                    tail = sentence[name_match.end():]
                    safe_tail = (
                        not tail or not word_character.match(tail[0])
                    )
                    if not safe_tail:
                        safe_tail = any(
                            tail.startswith(particle)
                            for particle in following_particles
                        )
                    if not safe_tail:
                        continue
                    explicit_ids.add(
                        explicit_name_index[name_match.group(0)]
                    )
                if len(explicit_ids) > maximum_mentions:
                    continue
                contextual_ids = set(explicit_ids)
                if subject_id:
                    contextual_ids.add(subject_id)
                if len(contextual_ids) < 2:
                    continue
                for endpoint_pair in combinations(
                    sorted(contextual_ids),
                    2,
                ):
                    pair_key = tuple(sorted(endpoint_pair))
                    tasks = list(tasks_by_pair.get(pair_key, []))
                    for seed_id in pair_key:
                        for fragment_task in (
                            fragment_tasks_by_seed.get(seed_id, [])
                        ):
                            tasks.append(
                                {
                                    **fragment_task,
                                    "endpoint_pair": pair_key,
                                    "start_name": (
                                        canonical_name_by_id.get(
                                            pair_key[0],
                                            "",
                                        )
                                    ),
                                    "end_name": (
                                        canonical_name_by_id.get(
                                            pair_key[1],
                                            "",
                                        )
                                    ),
                                }
                            )
                    if not tasks:
                        continue
                    both_explicit = set(pair_key).issubset(explicit_ids)
                    subject_context = (
                        subject_id in pair_key
                        and any(
                            endpoint_id in explicit_ids
                            for endpoint_id in pair_key
                            if endpoint_id != subject_id
                        )
                    )
                    if not both_explicit and not subject_context:
                        continue
                    evidence_mode = "TWO_EXPLICIT_ENTITIES"
                    if subject_context:
                        evidence_mode = (
                            "SUBJECT_CONTEXT_AND_EXPLICIT_OBJECT"
                        )
                    for task in tasks:
                        candidate_id = str(
                            task["exam_relation_candidate_id"]
                        )
                        discovered_endpoint_linked = False
                        if "seed_canonical_id" in task:
                            discovered_ids = set(pair_key).difference(
                                {str(task["seed_canonical_id"])}
                            )
                            if len(discovered_ids) != 1:
                                continue
                            discovered_endpoint_id = next(
                                iter(discovered_ids)
                            )
                            discovered_endpoint_linked = (
                                discovered_endpoint_id in linked_ids
                            )
                            if not discovered_endpoint_linked:
                                continue
                            candidate_pairs = (
                                discovered_pairs_by_candidate[
                                    candidate_id
                                ]
                            )
                            if (
                                pair_key not in candidate_pairs
                                and len(candidate_pairs)
                                >= maximum_discovered_pairs
                            ):
                                continue
                            candidate_pairs.add(pair_key)
                        if (
                            evidence_count_by_candidate_pair[
                                (candidate_id, pair_key)
                            ]
                            >= maximum_evidence
                        ):
                            continue
                        candidate_patterns_by_family = task[
                            "predicate_patterns_by_family"
                        ]
                        shared_patterns_by_family: dict[
                            str,
                            set[str],
                        ] = {}
                        for family in sentence_families.intersection(
                            task["predicate_families"]
                        ):
                            shared_patterns = (
                                sentence_patterns_by_family.get(
                                    family,
                                    set(),
                                ).intersection(
                                    candidate_patterns_by_family.get(
                                        family,
                                        set(),
                                    )
                                )
                            )
                            if shared_patterns:
                                shared_patterns_by_family[family] = (
                                    shared_patterns
                                )
                        if not shared_patterns_by_family:
                            continue
                        role_pair_verified = False
                        if "seed_canonical_id" in task:
                            role_pair_verified = (
                                official_sentence_has_role_pair(
                                    sentence,
                                    pair_key,
                                    shared_patterns_by_family,
                                    registry_by_id,
                                    policy,
                                )
                            )
                            if not role_pair_verified:
                                continue
                        shared_families = set(
                            shared_patterns_by_family
                        )
                        shared_patterns = {
                            pattern
                            for patterns in (
                                shared_patterns_by_family.values()
                            )
                            for pattern in patterns
                        }
                        evidence_key = (
                            candidate_id,
                            article_id,
                            sentence,
                            pair_key,
                        )
                        if evidence_key in evidence_keys:
                            continue
                        evidence_keys.add(evidence_key)
                        evidence_count_by_candidate_pair[
                            (candidate_id, pair_key)
                        ] += 1
                        evidence_id = create_text_evidence_id(
                            candidate_id,
                            article_id,
                            sentence,
                            pair_key,
                            policy,
                        )
                        resolved_evidence_mode = evidence_mode
                        if role_pair_verified:
                            resolved_evidence_mode = (
                                "EXPLICIT_ROLE_PAIR"
                            )
                        evidence_rows.append(
                            {
                                "exam_official_text_evidence_id": (
                                    evidence_id
                                ),
                                "exam_relation_candidate_id": (
                                    candidate_id
                                ),
                                "claim_segment_id": str(
                                    task["claim_segment_id"]
                                ),
                                "problem_id": str(task["problem_id"]),
                                "start_canonical_id": pair_key[0],
                                "end_canonical_id": pair_key[1],
                                "start_name": str(task["start_name"]),
                                "end_name": str(task["end_name"]),
                                "shared_predicate_families_json": dumps(
                                    sorted(shared_families),
                                    ensure_ascii=False,
                                ),
                                "shared_predicate_patterns_json": dumps(
                                    sorted(shared_patterns),
                                    ensure_ascii=False,
                                ),
                                "evidence_mode": (
                                    resolved_evidence_mode
                                ),
                                "both_endpoints_explicit": (
                                    both_explicit
                                ),
                                "role_pair_verified": (
                                    role_pair_verified
                                ),
                                "discovered_endpoint_linked": (
                                    discovered_endpoint_linked
                                ),
                                "source": str(text_policy["source"]),
                                "source_record_id": (
                                    f"{text_policy['source_record_prefix']}"
                                    f"{article_id}"
                                ),
                                "source_url": str(
                                    article.get("url") or ""
                                ),
                                "source_headword": str(
                                    article.get("headword") or ""
                                ),
                                "evidence_field": evidence_field,
                                "official_evidence_sentence": sentence,
                                "exam_evidence_text": str(
                                    task["exam_evidence_text"]
                                ),
                                "supports_relation_family_only": True,
                                "may_create_new_fact": False,
                                "llm_used": False,
                                "policy_version": str(
                                    text_policy["policy_version"]
                                ),
                            }
                        )
    scan_statistics["discovered_candidate_pair_count"] = sum(
        len(pair_keys)
        for pair_keys in discovered_pairs_by_candidate.values()
    )
    return evidence_rows, scan_statistics


def validate_official_text_tables(
    tables: dict[str, pd.DataFrame],
) -> list[str]:
    """AKS 원문 근거가 새 사실 적재 권한을 갖지 않는지 검사한다."""
    checks = tables["text_checks"]
    evidence = tables["text_evidence"]
    disambiguation = tables["aks_disambiguation"]
    errors: list[str] = []
    if checks["exam_relation_candidate_id"].duplicated().any():
        errors.append("원문 검증 결과가 후보별로 중복되었습니다.")
    if evidence["exam_official_text_evidence_id"].duplicated().any():
        errors.append("AKS 원문 근거 ID가 중복되었습니다.")
    if checks["may_create_new_fact"].eq(True).any():
        errors.append("AKS 원문 검증 결과가 새 사실 생성을 허용했습니다.")
    if evidence["may_create_new_fact"].eq(True).any():
        errors.append("AKS 원문 근거가 새 사실 생성을 허용했습니다.")
    safe_rows = disambiguation[
        disambiguation["disambiguation_status"].eq("SAFE_MATCH")
    ]
    if safe_rows["aks_eid"].duplicated().any():
        errors.append("같은 AKS EID가 여러 canonical로 안전 해소됐습니다.")
    return errors


def build_exam_relation_official_text_tables(
    official_checks: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    aks_details_path: str,
    policy: dict,
    source_records: pd.DataFrame | None = None,
    aks_list_path: str | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """공식 사실표에서 놓친 후보를 AKS 원문으로 교차 확인한다."""
    text_policy = policy[
        "exam_relation_official_text_corroboration"
    ]
    disambiguation_columns = [
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
    disambiguation = pd.DataFrame(
        columns=disambiguation_columns
    )
    trusted_aks_eid_map: dict[str, str] = {}
    disambiguation_statistics = {
        "ambiguous_relevant_name_count": 0,
        "aks_same_name_article_count": 0,
        "safe_disambiguation_count": 0,
        "ambiguous_disambiguation_count": 0,
        "no_safe_match_count": 0,
        "aks_list_invalid_json_count": 0,
    }
    if source_records is not None and aks_list_path is not None:
        (
            disambiguation,
            trusted_aks_eid_map,
            disambiguation_statistics,
        ) = build_safe_aks_disambiguation(
            official_checks,
            canonical_registry,
            source_records,
            aks_list_path,
            policy,
        )
    (
        tasks_by_pair,
        fragment_tasks_by_seed,
        explicit_name_index,
        canonical_id_by_eid,
        canonical_name_by_id,
        registry_by_id,
        check_seeds,
    ) = build_official_text_search_contracts(
        official_checks,
        canonical_registry,
        policy,
        trusted_aks_eid_map,
    )
    evidence_rows, scan_statistics = scan_aks_official_text(
        aks_details_path,
        tasks_by_pair,
        fragment_tasks_by_seed,
        explicit_name_index,
        canonical_id_by_eid,
        canonical_name_by_id,
        registry_by_id,
        policy,
    )
    evidence_by_candidate: dict[str, list[dict]] = defaultdict(list)
    for evidence in evidence_rows:
        evidence_by_candidate[
            str(evidence["exam_relation_candidate_id"])
        ].append(evidence)

    statuses = text_policy["search_statuses"]
    minimum_two_explicit_sources = int(
        text_policy["minimum_two_explicit_source_count"]
    )
    check_rows: list[dict] = []
    for seed in check_seeds:
        candidate_id = str(seed["exam_relation_candidate_id"])
        candidate_evidence = evidence_by_candidate.get(
            candidate_id,
            [],
        )
        search_status = str(seed["search_status"])
        evidence_by_pair: dict[
            tuple[str, str],
            list[dict],
        ] = defaultdict(list)
        for evidence in candidate_evidence:
            pair_key = tuple(
                sorted(
                    [
                        str(evidence["start_canonical_id"]),
                        str(evidence["end_canonical_id"]),
                    ]
                )
            )
            evidence_by_pair[pair_key].append(evidence)
        strict_pair_keys: list[tuple[str, str]] = []
        for pair_key, pair_evidence in evidence_by_pair.items():
            subject_context_evidence = [
                row
                for row in pair_evidence
                if row["evidence_mode"]
                == "SUBJECT_CONTEXT_AND_EXPLICIT_OBJECT"
            ]
            two_explicit_source_ids = {
                str(row["source_record_id"])
                for row in pair_evidence
                if row["evidence_mode"] == "TWO_EXPLICIT_ENTITIES"
            }
            role_pair_evidence = [
                row
                for row in pair_evidence
                if row["evidence_mode"] == "EXPLICIT_ROLE_PAIR"
                and bool(row["role_pair_verified"])
            ]
            pair_is_strict = (
                bool(subject_context_evidence)
                or bool(role_pair_evidence)
                or (
                len(two_explicit_source_ids)
                >= minimum_two_explicit_sources
                )
            )
            if pair_is_strict:
                strict_pair_keys.append(pair_key)
        strict_pair_keys.sort()
        strict_support = len(strict_pair_keys) == 1
        resolved_pair: tuple[str, str] | tuple[()] = ()
        if strict_support:
            resolved_pair = strict_pair_keys[0]
            search_status = str(statuses["supported"])
        elif len(strict_pair_keys) > 1:
            search_status = str(statuses["ambiguous_endpoints"])
        elif candidate_evidence:
            search_status = str(statuses["insufficient_evidence"])
        check_rows.append(
            {
                **seed,
                "search_status": search_status,
                "official_text_evidence_count": len(
                    candidate_evidence
                ),
                "official_text_evidence_ids_json": dumps(
                    sorted(
                        str(row["exam_official_text_evidence_id"])
                        for row in candidate_evidence
                    ),
                    ensure_ascii=False,
                ),
                "official_text_urls_json": dumps(
                    sorted(
                        {
                            str(row["source_url"])
                            for row in candidate_evidence
                            if str(row["source_url"])
                        }
                    ),
                    ensure_ascii=False,
                ),
                "supports_relation_family_only": bool(
                    candidate_evidence
                ),
                "strict_supported_pair_count": len(
                    strict_pair_keys
                ),
                "resolved_endpoint_ids_json": dumps(
                    list(resolved_pair),
                    ensure_ascii=False,
                ),
                "strict_support": strict_support,
                "requires_relation_mapping": bool(
                    strict_support
                ),
                "may_create_new_fact": False,
                "llm_used": False,
                "policy_version": str(text_policy["policy_version"]),
            }
        )
    check_columns = [
        "exam_relation_candidate_id",
        "claim_segment_id",
        "problem_id",
        "official_fact_check_status",
        "search_status",
        "predicate_families_json",
        "searched_endpoint_pair_count",
        "endpoint_recovery_mode",
        "official_text_evidence_count",
        "official_text_evidence_ids_json",
        "official_text_urls_json",
        "supports_relation_family_only",
        "strict_supported_pair_count",
        "resolved_endpoint_ids_json",
        "strict_support",
        "requires_relation_mapping",
        "may_create_new_fact",
        "llm_used",
        "exam_evidence_text",
        "policy_version",
    ]
    evidence_columns = [
        "exam_official_text_evidence_id",
        "exam_relation_candidate_id",
        "claim_segment_id",
        "problem_id",
        "start_canonical_id",
        "end_canonical_id",
        "start_name",
        "end_name",
        "shared_predicate_families_json",
        "shared_predicate_patterns_json",
        "evidence_mode",
        "both_endpoints_explicit",
        "role_pair_verified",
        "discovered_endpoint_linked",
        "source",
        "source_record_id",
        "source_url",
        "source_headword",
        "evidence_field",
        "official_evidence_sentence",
        "exam_evidence_text",
        "supports_relation_family_only",
        "may_create_new_fact",
        "llm_used",
        "policy_version",
    ]
    text_checks = pd.DataFrame(check_rows, columns=check_columns)
    text_evidence = pd.DataFrame(
        evidence_rows,
        columns=evidence_columns,
    )
    tables = {
        "text_checks": text_checks,
        "text_evidence": text_evidence,
        "aks_disambiguation": disambiguation,
    }
    validation_errors = validate_official_text_tables(tables)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))

    statistics: dict[str, object] = {
        **scan_statistics,
        "input_check_count": len(official_checks),
        "search_pair_count": len(tasks_by_pair),
        "endpoint_fragment_task_count": sum(
            len(tasks)
            for tasks in fragment_tasks_by_seed.values()
        ),
        "search_candidate_count": len(
            {
                str(task["exam_relation_candidate_id"])
                for tasks in tasks_by_pair.values()
                for task in tasks
            }.union(
                {
                    str(task["exam_relation_candidate_id"])
                    for tasks in fragment_tasks_by_seed.values()
                    for task in tasks
                }
            )
        ),
        "explicit_name_index_count": len(explicit_name_index),
        "aks_subject_index_count": len(canonical_id_by_eid),
        **disambiguation_statistics,
        "search_status_counts": dict(
            Counter(
                str(value) for value in text_checks["search_status"]
            )
        ),
        "supported_candidate_count": int(
            text_checks["search_status"]
            .eq(str(statuses["supported"]))
            .sum()
        ),
        "evidence_count": len(text_evidence),
        "new_fact_creation_count": int(
            text_evidence["may_create_new_fact"].eq(True).sum()
        ),
        "llm_used": False,
        "neo4j_load": False,
    }
    return tables, statistics

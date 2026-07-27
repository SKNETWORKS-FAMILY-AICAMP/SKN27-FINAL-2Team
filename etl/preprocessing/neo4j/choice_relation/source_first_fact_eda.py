from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from itertools import product
from json import load, loads, dumps
from pathlib import Path
import re

import pandas as pd

from choice_relation.official_text_corroboration import (
    collect_predicate_patterns,
    extract_article_sentences,
)
from choice_relation.relation_frames import (
    classify_mention_role,
    find_atomic_clause_spans,
    find_clause_mentions,
    find_pattern_occurrences,
    locate_clause_span,
)


def load_source_first_fact_policy(
    eda_policy_path: str,
    relation_policy_path: str,
) -> dict:
    """원천 중심 EDA와 관계 프레임 정책을 함께 읽는다."""
    with open(eda_policy_path, "r", encoding="utf-8") as input_file:
        eda_policy = load(input_file)
    with open(
        relation_policy_path,
        "r",
        encoding="utf-8",
    ) as input_file:
        relation_policy = load(input_file)
    return {
        "source_first_fact_eda": eda_policy,
        "exam_relation_candidates": relation_policy[
            "exam_relation_candidates"
        ],
        "exam_relation_frames": relation_policy[
            "exam_relation_frames"
        ],
        "exam_relation_official_corroboration": relation_policy[
            "exam_relation_official_corroboration"
        ],
        "exam_relation_official_text_corroboration": relation_policy[
            "exam_relation_official_text_corroboration"
        ],
    }


def parse_json_list(value: object) -> list[str]:
    """JSON 배열 문자열을 문자열 목록으로 정규화한다."""
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    text = str(value or "").strip()
    if not text:
        return []
    parsed = loads(text)
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item)]


def create_identifier(
    prefix: str,
    values: list[str],
    policy: dict,
) -> str:
    """정책의 해시 규칙으로 재현 가능한 식별자를 만든다."""
    identifier_policy = policy["source_first_fact_eda"][
        "identifier"
    ]
    digest = sha256("\u241f".join(values).encode("utf-8")).hexdigest()
    return (
        f"{prefix}"
        f"{digest[:int(identifier_policy['digest_length'])]}"
    )


def build_source_indexes(
    canonical_registry: pd.DataFrame,
    policy: dict,
) -> tuple[
    dict[str, dict],
    dict[str, str],
    dict[str, str],
    dict[str, str],
]:
    """ACTIVE canonical과 AKS·용어집 원천 ID 색인을 만든다."""
    eda_policy = policy["source_first_fact_eda"]
    accepted_status = str(
        eda_policy["accepted_registry_status"]
    )
    source_prefixes = eda_policy["source_prefixes"]
    registry_by_id: dict[str, dict] = {}
    canonical_ids_by_name: dict[str, set[str]] = defaultdict(set)
    canonical_ids_by_eid: dict[str, set[str]] = defaultdict(set)
    canonical_ids_by_term_id: dict[str, set[str]] = defaultdict(set)
    for row in canonical_registry.to_dict("records"):
        if str(row["lifecycle_status"]) != accepted_status:
            continue
        canonical_id = str(row["canonical_id"])
        registry_by_id[canonical_id] = row
        display_name = str(row["display_name"]).strip()
        if display_name:
            canonical_ids_by_name[display_name].add(canonical_id)
        for source_id in parse_json_list(
            row.get("identity_member_source_ids_json", "")
        ):
            if source_id.startswith(
                str(source_prefixes["aks_article"])
            ):
                source_parts = source_id.split(":")
                if len(source_parts) >= 3:
                    canonical_ids_by_eid[source_parts[2]].add(
                        canonical_id
                    )
                continue
            if source_id.startswith(
                str(source_prefixes["thesaurus_term"])
            ):
                source_parts = source_id.split(":")
                if len(source_parts) >= 3:
                    canonical_ids_by_term_id[
                        source_parts[2]
                    ].add(canonical_id)
    unique_name_index = {
        name: next(iter(canonical_ids))
        for name, canonical_ids in canonical_ids_by_name.items()
        if len(canonical_ids) == 1
    }
    unique_eid_index = {
        eid: next(iter(canonical_ids))
        for eid, canonical_ids in canonical_ids_by_eid.items()
        if len(canonical_ids) == 1
    }
    unique_term_index = {
        term_id: next(iter(canonical_ids))
        for term_id, canonical_ids in (
            canonical_ids_by_term_id.items()
        )
        if len(canonical_ids) == 1
    }
    return (
        registry_by_id,
        unique_name_index,
        unique_eid_index,
        unique_term_index,
    )


def build_exam_anchor_ids(
    exam_term_matches: pd.DataFrame,
    policy: dict,
) -> set[str]:
    """단일 ACCEPTED 기출 용어가 가리키는 canonical ID를 모은다."""
    accepted_status = str(
        policy["source_first_fact_eda"]["exam_anchor_status"]
    )
    anchor_ids: set[str] = set()
    for row in exam_term_matches.to_dict("records"):
        if (
            str(row["projected_source_link_status"])
            != accepted_status
        ):
            continue
        projected_ids = parse_json_list(
            row["projected_canonical_ids_json"]
        )
        if len(projected_ids) == 1:
            anchor_ids.add(projected_ids[0])
    return anchor_ids


def normalize_linked_sentence(
    sentence: str,
    registry_by_id: dict[str, dict],
    canonical_id_by_eid: dict[str, str],
    policy: dict,
) -> tuple[str, list[dict]]:
    """AKS EID 링크를 canonical 이름으로 바꾸고 위치를 보존한다."""
    link_pattern = re.compile(
        str(
            policy["source_first_fact_eda"]["aks"][
                "linked_entity_pattern"
            ]
        )
    )
    clean_parts: list[str] = []
    linked_mentions: list[dict] = []
    source_cursor = 0
    clean_length = 0
    for match in link_pattern.finditer(sentence):
        prefix_text = sentence[source_cursor:match.start()]
        clean_parts.append(prefix_text)
        clean_length += len(prefix_text)
        eid = str(match.group("eid"))
        label = str(match.group("label")).strip()
        canonical_id = canonical_id_by_eid.get(eid, "")
        replacement = label
        if canonical_id in registry_by_id:
            replacement = str(
                registry_by_id[canonical_id]["display_name"]
            ).strip()
        mention_start = clean_length
        clean_parts.append(replacement)
        clean_length += len(replacement)
        if canonical_id in registry_by_id:
            endpoint = registry_by_id[canonical_id]
            linked_mentions.append(
                {
                    "canonical_id": canonical_id,
                    "display_name": replacement,
                    "entity_type": str(endpoint["entity_type"]),
                    "mention_text": replacement,
                    "mention_start": mention_start,
                    "mention_end": clean_length,
                }
            )
        source_cursor = match.end()
    remaining_text = sentence[source_cursor:]
    clean_parts.append(remaining_text)
    return "".join(clean_parts), linked_mentions


def collect_asserted_actions(
    text: str,
    policy: dict,
) -> list[dict]:
    """문장에서 확정적으로 서술된 관계 행동을 수집한다."""
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
    clause_spans = find_atomic_clause_spans(text, policy)
    patterns_by_family = collect_predicate_patterns(text, policy)
    actions: list[dict] = []
    for predicate_family, predicate_patterns in (
        patterns_by_family.items()
    ):
        for predicate_pattern in predicate_patterns:
            for action_start, action_end in find_pattern_occurrences(
                text,
                predicate_pattern,
            ):
                clause_span = locate_clause_span(
                    action_start,
                    action_end,
                    clause_spans,
                )
                action_suffix = text[
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
                actions.append(
                    {
                        "predicate_family": predicate_family,
                        "predicate_pattern": predicate_pattern,
                        "action_start": action_start,
                        "action_end": action_end,
                        "clause_span": clause_span,
                        "voice": voice,
                        "action_suffix": action_suffix,
                    }
                )
    return actions


def classify_linked_mentions(
    text: str,
    linked_mentions: list[dict],
    action: dict,
    policy: dict,
) -> list[dict]:
    """같은 절에서 술어 앞에 나온 EID 링크의 역할을 판정한다."""
    clause_start, clause_end = action["clause_span"]
    classified: list[dict] = []
    for linked_mention in linked_mentions:
        mention_start = int(linked_mention["mention_start"])
        mention_end = int(linked_mention["mention_end"])
        if mention_start < clause_start or mention_end > clause_end:
            continue
        if mention_end > int(action["action_start"]):
            continue
        suffix = text[mention_end:clause_end]
        role, role_basis = classify_mention_role(
            suffix,
            str(linked_mention["entity_type"]),
            str(action["voice"]),
            policy,
        )
        classified.append(
            {
                **linked_mention,
                "participant_role": role,
                "role_basis": role_basis,
                "role_suffix": suffix,
            }
        )
    return classified


def resolve_unique_role_pair(
    predicate_family: str,
    mentions: list[dict],
    policy: dict,
) -> dict | None:
    """허용 역할 조합이 정확히 하나일 때 방향이 있는 쌍을 반환한다."""
    ids_by_role: dict[str, set[str]] = defaultdict(set)
    for mention in mentions:
        role = str(mention["participant_role"])
        if role in {"UNKNOWN", "COORDINATED"}:
            continue
        ids_by_role[role].add(str(mention["canonical_id"]))
    resolved_pairs: dict[
        tuple[str, str, str, str],
        dict,
    ] = {}
    role_sets = policy["exam_relation_frames"][
        "pair_role_sets_by_family"
    ].get(predicate_family, [])
    for role_set in role_sets:
        required_roles = [str(role) for role in role_set]
        if any(not ids_by_role.get(role) for role in required_roles):
            continue
        role_values = [
            sorted(ids_by_role[role]) for role in required_roles
        ]
        for candidate_ids in product(*role_values):
            if len(set(candidate_ids)) != len(candidate_ids):
                continue
            pair_key = (
                candidate_ids[0],
                required_roles[0],
                candidate_ids[1],
                required_roles[1],
            )
            resolved_pairs[pair_key] = {
                "start_canonical_id": candidate_ids[0],
                "start_role": required_roles[0],
                "end_canonical_id": candidate_ids[1],
                "end_role": required_roles[1],
            }
    if len(resolved_pairs) != 1:
        return None
    return next(iter(resolved_pairs.values()))


def infer_subject_role_pair(
    subject_id: str,
    explicit_mentions: list[dict],
    predicate_family: str,
    registry_by_id: dict[str, dict],
    policy: dict,
) -> dict | None:
    """표제어가 생략됐을 때 상대 역할로 표제어 역할을 유일 추론한다."""
    explicit_ids = {
        str(mention["canonical_id"])
        for mention in explicit_mentions
        if str(mention["participant_role"])
        not in {"UNKNOWN", "COORDINATED"}
    }
    explicit_roles = {
        str(mention["participant_role"])
        for mention in explicit_mentions
        if str(mention["participant_role"])
        not in {"UNKNOWN", "COORDINATED"}
    }
    if len(explicit_ids) != 1 or len(explicit_roles) != 1:
        return None
    if subject_id in explicit_ids or subject_id not in registry_by_id:
        return None
    inferred_roles: set[str] = set()
    explicit_role = next(iter(explicit_roles))
    role_sets = policy["exam_relation_frames"][
        "pair_role_sets_by_family"
    ].get(predicate_family, [])
    for role_set in role_sets:
        required_roles = [str(role) for role in role_set]
        if explicit_role not in required_roles:
            continue
        missing_roles = [
            role for role in required_roles if role != explicit_role
        ]
        if len(missing_roles) == 1:
            inferred_roles.add(missing_roles[0])
    if len(inferred_roles) != 1:
        return None
    inferred_role = next(iter(inferred_roles))
    subject_type = str(registry_by_id[subject_id]["entity_type"])
    place_types = {
        str(value)
        for value in policy["exam_relation_frames"][
            "place_entity_types"
        ]
    }
    if inferred_role == "LOCATION" and subject_type not in place_types:
        return None
    synthetic_subject = {
        "canonical_id": subject_id,
        "participant_role": inferred_role,
    }
    return resolve_unique_role_pair(
        predicate_family,
        [*explicit_mentions, synthetic_subject],
        policy,
    )


def direct_pair_is_auto_acceptable(
    resolved_pair: dict,
    classified_mentions: list[dict],
    action: dict,
    all_actions: list[dict],
    registry_by_id: dict[str, dict],
    policy: dict,
) -> bool:
    """직접 EID 역할쌍이 자동 후보 계약까지 만족하는지 검사한다."""
    eda_policy = policy["source_first_fact_eda"]
    aks_policy = eda_policy["aks"]
    if str(action["predicate_pattern"]) in {
        str(value)
        for value in aks_policy[
            "auto_accept_excluded_predicate_patterns"
        ]
    }:
        return False
    unsafe_action_patterns = [
        re.compile(str(value))
        for value in aks_policy["unsafe_action_suffix_patterns"]
    ]
    if any(
        pattern.search(str(action["action_suffix"]))
        for pattern in unsafe_action_patterns
    ):
        return False
    start_id = str(resolved_pair["start_canonical_id"])
    end_id = str(resolved_pair["end_canonical_id"])
    start_type = str(registry_by_id[start_id]["entity_type"])
    end_type = str(registry_by_id[end_id]["entity_type"])
    contracts = eda_policy[
        "auto_accept_role_type_contracts"
    ].get(str(action["predicate_family"]), [])
    contract_match = any(
        str(contract["start_role"])
        == str(resolved_pair["start_role"])
        and start_type
        in {str(value) for value in contract["start_types"]}
        and str(contract["end_role"])
        == str(resolved_pair["end_role"])
        and end_type
        in {str(value) for value in contract["end_types"]}
        for contract in contracts
    )
    if not contract_match:
        return False
    resolved_mentions = [
        mention
        for mention in classified_mentions
        if str(mention["canonical_id"]) in {start_id, end_id}
    ]
    if not resolved_mentions:
        return False
    earliest_mention_start = min(
        int(mention["mention_start"])
        for mention in resolved_mentions
    )
    argument_span = (
        int(action["action_start"]) - earliest_mention_start
    )
    if argument_span > int(
        aks_policy["maximum_argument_span_characters"]
    ):
        return False
    if any(
        int(other_action["action_start"])
        > earliest_mention_start
        and int(other_action["action_start"])
        < int(action["action_start"])
        for other_action in all_actions
    ):
        return False
    unsafe_target_patterns = [
        re.compile(str(value))
        for value in aks_policy["unsafe_target_suffix_patterns"]
    ]
    for mention in resolved_mentions:
        if str(mention["participant_role"]) != "TARGET":
            continue
        role_suffix = str(mention.get("role_suffix") or "")
        if any(
            pattern.search(role_suffix)
            for pattern in unsafe_target_patterns
        ):
            return False
    return True


def build_evidence_row(
    resolved_pair: dict,
    predicate_family: str,
    predicate_pattern: str,
    source: str,
    discovery_rule: str,
    trust_tier: str,
    source_record_id: str,
    source_url: str,
    source_headword: str,
    source_field: str,
    evidence_sentence: str,
    registry_by_id: dict[str, dict],
    exam_anchor_ids: set[str],
    policy: dict,
) -> dict:
    """역할쌍 하나를 공통 EDA 근거 행으로 만든다."""
    start_id = str(resolved_pair["start_canonical_id"])
    end_id = str(resolved_pair["end_canonical_id"])
    identifier_policy = policy["source_first_fact_eda"][
        "identifier"
    ]
    evidence_id = create_identifier(
        str(identifier_policy["evidence_prefix"]),
        [
            source,
            discovery_rule,
            start_id,
            predicate_family,
            predicate_pattern,
            end_id,
            source_record_id,
            evidence_sentence,
        ],
        policy,
    )
    return {
        "source_first_evidence_id": evidence_id,
        "source": source,
        "discovery_rule": discovery_rule,
        "trust_tier": trust_tier,
        "auto_accept_eligible": (
            trust_tier
            == str(
                policy["source_first_fact_eda"]["trust_tiers"][
                    "auto_accept"
                ]
            )
        ),
        "start_canonical_id": start_id,
        "start_name": str(
            registry_by_id[start_id]["display_name"]
        ),
        "start_entity_type": str(
            registry_by_id[start_id]["entity_type"]
        ),
        "start_role": str(resolved_pair["start_role"]),
        "end_canonical_id": end_id,
        "end_name": str(registry_by_id[end_id]["display_name"]),
        "end_entity_type": str(
            registry_by_id[end_id]["entity_type"]
        ),
        "end_role": str(resolved_pair["end_role"]),
        "predicate_family": predicate_family,
        "predicate_pattern": predicate_pattern,
        "source_record_id": source_record_id,
        "source_url": source_url,
        "source_headword": source_headword,
        "source_field": source_field,
        "evidence_sentence": evidence_sentence,
        "touches_exam_anchor": (
            start_id in exam_anchor_ids or end_id in exam_anchor_ids
        ),
        "both_exam_anchors": (
            start_id in exam_anchor_ids and end_id in exam_anchor_ids
        ),
        "llm_used": False,
        "neo4j_load": False,
        "policy_version": str(
            policy["source_first_fact_eda"]["policy_version"]
        ),
    }


def scan_aks_source_first_facts(
    aks_details_path: str,
    registry_by_id: dict[str, dict],
    canonical_id_by_eid: dict[str, str],
    exam_anchor_ids: set[str],
    policy: dict,
) -> tuple[list[dict], dict[str, int]]:
    """AKS 전체 문서에서 EID 기반 역할 관계를 찾는다."""
    eda_policy = policy["source_first_fact_eda"]
    aks_policy = eda_policy["aks"]
    auto_accept_tier = str(
        eda_policy["trust_tiers"]["auto_accept"]
    )
    review_tier = str(eda_policy["trust_tiers"]["review"])
    uncertainty_patterns = [
        str(value) for value in aks_policy["uncertainty_patterns"]
    ]
    negation_patterns = [
        str(value) for value in aks_policy["negation_patterns"]
    ]
    maximum_linked_entities = int(
        aks_policy["maximum_linked_entities_per_sentence"]
    )
    evidence_rows: list[dict] = []
    evidence_ids: set[str] = set()
    statistics: Counter[str] = Counter()
    with open(aks_details_path, "r", encoding="utf-8") as input_file:
        for line in input_file:
            line_value = line.strip()
            if not line_value:
                continue
            article = loads(line_value)
            if not isinstance(article, dict):
                statistics["invalid_article_count"] += 1
                continue
            statistics["article_count"] += 1
            article_id = str(article.get("eid") or "")
            subject_id = canonical_id_by_eid.get(article_id, "")
            for evidence_field, sentence in extract_article_sentences(
                article,
                policy,
            ):
                statistics["sentence_count"] += 1
                if any(
                    value in sentence
                    for value in uncertainty_patterns
                ):
                    continue
                if any(
                    value in sentence for value in negation_patterns
                ):
                    continue
                clean_sentence, linked_mentions = (
                    normalize_linked_sentence(
                        sentence,
                        registry_by_id,
                        canonical_id_by_eid,
                        policy,
                    )
                )
                linked_ids = {
                    str(mention["canonical_id"])
                    for mention in linked_mentions
                }
                if not linked_ids:
                    continue
                if len(linked_ids) > maximum_linked_entities:
                    statistics["too_many_linked_entities"] += 1
                    continue
                actions = collect_asserted_actions(
                    clean_sentence,
                    policy,
                )
                if not actions:
                    continue
                statistics["action_sentence_count"] += 1
                for action in actions:
                    classified_mentions = classify_linked_mentions(
                        clean_sentence,
                        linked_mentions,
                        action,
                        policy,
                    )
                    stable_ids = {
                        str(mention["canonical_id"])
                        for mention in classified_mentions
                        if str(mention["participant_role"])
                        not in {"UNKNOWN", "COORDINATED"}
                    }
                    clause_start, clause_end = action["clause_span"]
                    clause_linked_ids = {
                        str(mention["canonical_id"])
                        for mention in linked_mentions
                        if int(mention["mention_start"])
                        >= int(clause_start)
                        and int(mention["mention_end"])
                        <= int(clause_end)
                        and int(mention["mention_end"])
                        <= int(action["action_start"])
                    }
                    resolved_pair: dict | None = None
                    discovery_rule = ""
                    trust_tier = review_tier
                    if (
                        len(stable_ids) == 2
                        and len(clause_linked_ids) == 2
                        and stable_ids == clause_linked_ids
                    ):
                        resolved_pair = resolve_unique_role_pair(
                            str(action["predicate_family"]),
                            classified_mentions,
                            policy,
                        )
                        discovery_rule = str(
                            aks_policy["direct_link_rule"]
                        )
                        if (
                            resolved_pair is not None
                            and direct_pair_is_auto_acceptable(
                                resolved_pair,
                                classified_mentions,
                                action,
                                actions,
                                registry_by_id,
                                policy,
                            )
                        ):
                            trust_tier = auto_accept_tier
                    if (
                        resolved_pair is None
                        and subject_id
                        and evidence_field
                        in {
                            str(value)
                            for value in aks_policy[
                                "subject_inference_fields"
                            ]
                        }
                        and len(clause_linked_ids) == 1
                    ):
                        resolved_pair = infer_subject_role_pair(
                            subject_id,
                            classified_mentions,
                            str(action["predicate_family"]),
                            registry_by_id,
                            policy,
                        )
                        discovery_rule = str(
                            aks_policy["subject_inference_rule"]
                        )
                    if resolved_pair is None:
                        continue
                    evidence_row = build_evidence_row(
                        resolved_pair,
                        str(action["predicate_family"]),
                        str(action["predicate_pattern"]),
                        str(aks_policy["source"]),
                        discovery_rule,
                        trust_tier,
                        f"AKS:ARTICLE:{article_id}",
                        str(article.get("url") or ""),
                        str(article.get("headword") or ""),
                        evidence_field,
                        sentence,
                        registry_by_id,
                        exam_anchor_ids,
                        policy,
                    )
                    evidence_id = str(
                        evidence_row["source_first_evidence_id"]
                    )
                    if evidence_id in evidence_ids:
                        continue
                    evidence_ids.add(evidence_id)
                    evidence_rows.append(evidence_row)
                    statistics[discovery_rule] += 1
    return evidence_rows, dict(statistics)


def build_unique_name_pattern(
    unique_name_index: dict[str, str],
    policy: dict,
) -> re.Pattern | None:
    """용어집 문맥에서 찾을 canonical 고유 이름 패턴을 만든다."""
    minimum_length = int(
        policy["source_first_fact_eda"]["thesaurus"][
            "minimum_unique_name_length"
        ]
    )
    names = [
        name
        for name in unique_name_index
        if len(re.sub(r"\s+", "", name)) >= minimum_length
    ]
    if not names:
        return None
    alternatives = "|".join(
        re.escape(name)
        for name in sorted(names, key=lambda value: (-len(value), value))
    )
    return re.compile(alternatives)


def find_context_canonical_ids(
    context: str,
    name_pattern: re.Pattern | None,
    unique_name_index: dict[str, str],
    policy: dict,
) -> set[str]:
    """문맥에서 문자 경계가 안전한 고유 canonical 이름을 찾는다."""
    if name_pattern is None:
        return set()
    following_particles = [
        str(value)
        for value in policy["source_first_fact_eda"]["thesaurus"][
            "following_particles"
        ]
    ]
    word_character = re.compile(r"[0-9A-Za-z가-힣一-龥]")
    canonical_ids: set[str] = set()
    for match in name_pattern.finditer(context):
        if (
            match.start() > 0
            and word_character.fullmatch(context[match.start() - 1])
        ):
            continue
        tail = context[match.end():]
        safe_tail = not tail or not word_character.match(tail[0])
        if not safe_tail:
            safe_tail = any(
                tail.startswith(particle)
                for particle in following_particles
            )
        if not safe_tail:
            continue
        canonical_ids.add(unique_name_index[match.group(0)])
    return canonical_ids


def scan_thesaurus_source_first_facts(
    thesaurus_path: str,
    registry_by_id: dict[str, dict],
    unique_name_index: dict[str, str],
    canonical_id_by_term_id: dict[str, str],
    exam_anchor_ids: set[str],
    policy: dict,
) -> tuple[list[dict], dict[str, int]]:
    """용어집 표제어와 고유 이름의 역할 관계를 검토 후보로 찾는다."""
    eda_policy = policy["source_first_fact_eda"]
    thesaurus_policy = eda_policy["thesaurus"]
    trust_tier = str(eda_policy["trust_tiers"]["review"])
    maximum_names = int(
        thesaurus_policy["maximum_named_entities_per_context"]
    )
    hanja_parenthetical_pattern = re.compile(
        str(thesaurus_policy["hanja_parenthetical_pattern"])
    )
    name_pattern = build_unique_name_pattern(
        unique_name_index,
        policy,
    )
    with open(thesaurus_path, "r", encoding="utf-8") as input_file:
        documents = load(input_file)
    evidence_rows: list[dict] = []
    evidence_ids: set[str] = set()
    statistics: Counter[str] = Counter()
    for document in documents:
        statistics["document_count"] += 1
        term_id = str(
            document.get("problem_id") or ""
        ).removeprefix("thesaurus_")
        subject_id = canonical_id_by_term_id.get(term_id, "")
        if subject_id not in registry_by_id:
            continue
        statistics["mapped_subject_document_count"] += 1
        for term in document.get("terms", []):
            context = str(term.get("context") or "").strip()
            if not context:
                continue
            context_ids = find_context_canonical_ids(
                context,
                name_pattern,
                unique_name_index,
                policy,
            )
            context_ids.discard(subject_id)
            if not context_ids:
                continue
            if len(context_ids) > maximum_names:
                statistics["too_many_named_entities"] += 1
                continue
            clean_context = hanja_parenthetical_pattern.sub(
                "",
                context,
            )
            actions = collect_asserted_actions(
                clean_context,
                policy,
            )
            if not actions:
                continue
            endpoint_rows = [
                registry_by_id[canonical_id]
                for canonical_id in sorted(context_ids)
                if canonical_id in registry_by_id
            ]
            for action in actions:
                mentions = find_clause_mentions(
                    clean_context,
                    action["clause_span"],
                    endpoint_rows,
                    str(action["voice"]),
                    policy,
                )
                mentions_before_action = [
                    mention
                    for mention in mentions
                    if int(mention["mention_end"])
                    <= int(action["action_start"])
                ]
                resolved_pair = infer_subject_role_pair(
                    subject_id,
                    mentions_before_action,
                    str(action["predicate_family"]),
                    registry_by_id,
                    policy,
                )
                if resolved_pair is None:
                    continue
                discovery_rule = str(
                    thesaurus_policy["subject_inference_rule"]
                )
                evidence_row = build_evidence_row(
                    resolved_pair,
                    str(action["predicate_family"]),
                    str(action["predicate_pattern"]),
                    str(thesaurus_policy["source"]),
                    discovery_rule,
                    trust_tier,
                    f"THESAURUS:TERM:{term_id}",
                    "",
                    str(term.get("raw_term") or ""),
                    "context",
                    context,
                    registry_by_id,
                    exam_anchor_ids,
                    policy,
                )
                evidence_id = str(
                    evidence_row["source_first_evidence_id"]
                )
                if evidence_id in evidence_ids:
                    continue
                evidence_ids.add(evidence_id)
                evidence_rows.append(evidence_row)
                statistics[discovery_rule] += 1
    return evidence_rows, dict(statistics)


def build_existing_fact_indexes(
    canonical_facts: pd.DataFrame,
) -> tuple[set[tuple[str, str]], set[tuple[str, str, str]]]:
    """기존 canonical 사실의 쌍과 관계유형 키를 만든다."""
    pair_keys: set[tuple[str, str]] = set()
    typed_keys: set[tuple[str, str, str]] = set()
    for row in canonical_facts.to_dict("records"):
        start_id = str(row["start_canonical_id"])
        end_id = str(row["end_canonical_id"])
        relation_type = str(row["relation_type"])
        pair_keys.add(tuple(sorted([start_id, end_id])))
        typed_keys.add((start_id, relation_type, end_id))
        typed_keys.add((end_id, relation_type, start_id))
    return pair_keys, typed_keys


def aggregate_source_first_facts(
    evidence: pd.DataFrame,
    canonical_facts: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    """근거 행을 방향 있는 관계 계열 단위 후보로 집계한다."""
    fact_columns = [
        "source_first_fact_id",
        "start_canonical_id",
        "start_name",
        "start_entity_type",
        "start_role",
        "relation_family",
        "end_canonical_id",
        "end_name",
        "end_entity_type",
        "end_role",
        "predicate_patterns_json",
        "sources_json",
        "discovery_rules_json",
        "trust_tiers_json",
        "evidence_ids_json",
        "evidence_count",
        "auto_accept_eligible",
        "existing_pair_match",
        "existing_family_fact_match",
        "novel_family_fact",
        "touches_exam_anchor",
        "both_exam_anchors",
        "llm_used",
        "neo4j_load",
        "policy_version",
    ]
    if evidence.empty:
        return pd.DataFrame(columns=fact_columns)
    pair_keys, typed_keys = build_existing_fact_indexes(
        canonical_facts
    )
    relation_types_by_family = policy[
        "exam_relation_official_corroboration"
    ]["predicate_family_relation_types"]
    fact_rows: list[dict] = []
    identifier_policy = policy["source_first_fact_eda"][
        "identifier"
    ]
    grouped = evidence.groupby(
        [
            "start_canonical_id",
            "start_name",
            "start_entity_type",
            "start_role",
            "predicate_family",
            "end_canonical_id",
            "end_name",
            "end_entity_type",
            "end_role",
        ],
        dropna=False,
        sort=True,
    )
    for keys, group in grouped:
        (
            start_id,
            start_name,
            start_type,
            start_role,
            predicate_family,
            end_id,
            end_name,
            end_type,
            end_role,
        ) = [str(value) for value in keys]
        pair_key = tuple(sorted([start_id, end_id]))
        expected_relation_types = [
            str(value)
            for value in relation_types_by_family.get(
                predicate_family,
                [],
            )
        ]
        family_match = any(
            (start_id, relation_type, end_id) in typed_keys
            for relation_type in expected_relation_types
        )
        fact_id = create_identifier(
            str(identifier_policy["fact_prefix"]),
            [
                start_id,
                start_role,
                predicate_family,
                end_role,
                end_id,
            ],
            policy,
        )
        auto_accept_eligible = bool(
            group["auto_accept_eligible"].eq(True).any()
        )
        fact_rows.append(
            {
                "source_first_fact_id": fact_id,
                "start_canonical_id": start_id,
                "start_name": start_name,
                "start_entity_type": start_type,
                "start_role": start_role,
                "relation_family": predicate_family,
                "end_canonical_id": end_id,
                "end_name": end_name,
                "end_entity_type": end_type,
                "end_role": end_role,
                "predicate_patterns_json": dumps(
                    sorted(set(group["predicate_pattern"])),
                    ensure_ascii=False,
                ),
                "sources_json": dumps(
                    sorted(set(group["source"])),
                    ensure_ascii=False,
                ),
                "discovery_rules_json": dumps(
                    sorted(set(group["discovery_rule"])),
                    ensure_ascii=False,
                ),
                "trust_tiers_json": dumps(
                    sorted(set(group["trust_tier"])),
                    ensure_ascii=False,
                ),
                "evidence_ids_json": dumps(
                    sorted(set(group["source_first_evidence_id"])),
                    ensure_ascii=False,
                ),
                "evidence_count": len(group),
                "auto_accept_eligible": auto_accept_eligible,
                "existing_pair_match": pair_key in pair_keys,
                "existing_family_fact_match": family_match,
                "novel_family_fact": not family_match,
                "touches_exam_anchor": bool(
                    group["touches_exam_anchor"].eq(True).any()
                ),
                "both_exam_anchors": bool(
                    group["both_exam_anchors"].eq(True).any()
                ),
                "llm_used": False,
                "neo4j_load": False,
                "policy_version": str(
                    policy["source_first_fact_eda"][
                        "policy_version"
                    ]
                ),
            }
        )
    return pd.DataFrame(fact_rows, columns=fact_columns)


def build_audit_sample(
    evidence: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    """원천 규칙·관계 계열별 결정론적 검토 표본을 만든다."""
    maximum_rows = int(
        policy["source_first_fact_eda"]["audit"][
            "maximum_rows"
        ]
    )
    if evidence.empty:
        return evidence.copy()
    group_columns = ["discovery_rule", "predicate_family"]
    group_count = evidence.groupby(group_columns).ngroups
    rows_per_group = max(1, maximum_rows // max(1, group_count))
    ordered = evidence.sort_values(
        [
            "discovery_rule",
            "predicate_family",
            "source_first_evidence_id",
        ]
    )
    sampled = (
        ordered.groupby(group_columns, group_keys=False)
        .head(rows_per_group)
        .head(maximum_rows)
        .reset_index(drop=True)
    )
    return sampled


def validate_source_first_fact_tables(
    tables: dict[str, pd.DataFrame],
) -> list[str]:
    """EDA 출력의 안전 계약 위반을 찾는다."""
    errors: list[str] = []
    evidence = tables["evidence"]
    facts = tables["facts"]
    if evidence["source_first_evidence_id"].duplicated().any():
        errors.append("source_first_evidence_id가 중복되었습니다.")
    if facts["source_first_fact_id"].duplicated().any():
        errors.append("source_first_fact_id가 중복되었습니다.")
    if evidence["llm_used"].eq(True).any():
        errors.append("EDA 근거에서 LLM 사용이 감지되었습니다.")
    if evidence["neo4j_load"].eq(True).any():
        errors.append("EDA 근거에서 Neo4j 적재가 감지되었습니다.")
    if (
        evidence["start_canonical_id"]
        .eq(evidence["end_canonical_id"])
        .any()
    ):
        errors.append("자기 자신을 잇는 관계가 생성되었습니다.")
    return errors


def build_source_first_fact_eda_tables(
    canonical_registry: pd.DataFrame,
    canonical_facts: pd.DataFrame,
    exam_term_matches: pd.DataFrame,
    aks_details_path: str,
    thesaurus_path: str,
    policy: dict,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """공식 원천 중심 관계 EDA 표와 통계를 만든다."""
    (
        registry_by_id,
        unique_name_index,
        canonical_id_by_eid,
        canonical_id_by_term_id,
    ) = build_source_indexes(canonical_registry, policy)
    exam_anchor_ids = build_exam_anchor_ids(
        exam_term_matches,
        policy,
    )
    aks_rows, aks_statistics = scan_aks_source_first_facts(
        aks_details_path,
        registry_by_id,
        canonical_id_by_eid,
        exam_anchor_ids,
        policy,
    )
    thesaurus_rows, thesaurus_statistics = (
        scan_thesaurus_source_first_facts(
            thesaurus_path,
            registry_by_id,
            unique_name_index,
            canonical_id_by_term_id,
            exam_anchor_ids,
            policy,
        )
    )
    evidence_columns = [
        "source_first_evidence_id",
        "source",
        "discovery_rule",
        "trust_tier",
        "auto_accept_eligible",
        "start_canonical_id",
        "start_name",
        "start_entity_type",
        "start_role",
        "end_canonical_id",
        "end_name",
        "end_entity_type",
        "end_role",
        "predicate_family",
        "predicate_pattern",
        "source_record_id",
        "source_url",
        "source_headword",
        "source_field",
        "evidence_sentence",
        "touches_exam_anchor",
        "both_exam_anchors",
        "llm_used",
        "neo4j_load",
        "policy_version",
    ]
    evidence = pd.DataFrame(
        [*aks_rows, *thesaurus_rows],
        columns=evidence_columns,
    )
    facts = aggregate_source_first_facts(
        evidence,
        canonical_facts,
        policy,
    )
    audit_sample = build_audit_sample(evidence, policy)
    tables = {
        "evidence": evidence,
        "facts": facts,
        "audit_sample": audit_sample,
    }
    validation_errors = validate_source_first_fact_tables(tables)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))
    source_dataset_counts = Counter()
    for value in canonical_facts["source_datasets_json"]:
        for source_dataset in parse_json_list(value):
            source_dataset_counts[source_dataset] += 1
    existing_touch_exam_count = int(
        (
            canonical_facts["start_canonical_id"].isin(
                exam_anchor_ids
            )
            | canonical_facts["end_canonical_id"].isin(
                exam_anchor_ids
            )
        ).sum()
    )
    existing_both_exam_count = int(
        (
            canonical_facts["start_canonical_id"].isin(
                exam_anchor_ids
            )
            & canonical_facts["end_canonical_id"].isin(
                exam_anchor_ids
            )
        ).sum()
    )
    novel_auto_mask = (
        facts["novel_family_fact"].eq(True)
        & facts["auto_accept_eligible"].eq(True)
    )
    novel_auto_touch_exam_count = int(
        (
            novel_auto_mask
            & facts["touches_exam_anchor"].eq(True)
        ).sum()
    )
    novel_auto_both_exam_count = int(
        (
            novel_auto_mask
            & facts["both_exam_anchors"].eq(True)
        ).sum()
    )
    excluded_swap_context_types = {
        str(value)
        for value in policy["source_first_fact_eda"][
            "excluded_swap_context_relation_types"
        ]
    }
    swap_eligible_existing = canonical_facts.loc[
        ~canonical_facts["relation_type"].isin(
            excluded_swap_context_types
        )
    ]
    swap_eligible_touch_exam = swap_eligible_existing.loc[
        swap_eligible_existing["start_canonical_id"].isin(
            exam_anchor_ids
        )
        | swap_eligible_existing["end_canonical_id"].isin(
            exam_anchor_ids
        )
    ]
    swap_eligible_both_exam = swap_eligible_existing.loc[
        swap_eligible_existing["start_canonical_id"].isin(
            exam_anchor_ids
        )
        & swap_eligible_existing["end_canonical_id"].isin(
            exam_anchor_ids
        )
    ]
    outgoing_group_sizes = swap_eligible_both_exam.groupby(
        ["start_canonical_id", "relation_type"]
    )["end_canonical_id"].nunique()
    outgoing_branch_keys = {
        (str(start_id), str(relation_type))
        for start_id, relation_type in outgoing_group_sizes[
            outgoing_group_sizes >= 2
        ].index
    }
    incoming_group_sizes = swap_eligible_both_exam.groupby(
        ["end_canonical_id", "relation_type"]
    )["start_canonical_id"].nunique()
    incoming_branch_keys = {
        (str(end_id), str(relation_type))
        for end_id, relation_type in incoming_group_sizes[
            incoming_group_sizes >= 2
        ].index
    }
    outgoing_branch_fact_count = sum(
        (
            str(row["start_canonical_id"]),
            str(row["relation_type"]),
        )
        in outgoing_branch_keys
        for row in swap_eligible_both_exam.to_dict("records")
    )
    incoming_branch_fact_count = sum(
        (
            str(row["end_canonical_id"]),
            str(row["relation_type"]),
        )
        in incoming_branch_keys
        for row in swap_eligible_both_exam.to_dict("records")
    )
    statistics: dict[str, object] = {
        "canonical_registry_count": len(registry_by_id),
        "exam_anchor_canonical_count": len(exam_anchor_ids),
        "existing_canonical_fact_count": len(canonical_facts),
        "existing_fact_touching_exam_anchor_count": (
            existing_touch_exam_count
        ),
        "existing_fact_between_exam_anchors_count": (
            existing_both_exam_count
        ),
        "existing_swap_eligible_fact_count": len(
            swap_eligible_existing
        ),
        "existing_swap_eligible_fact_touching_exam_anchor_count": (
            len(swap_eligible_touch_exam)
        ),
        "existing_swap_eligible_fact_between_exam_anchors_count": (
            len(swap_eligible_both_exam)
        ),
        "existing_outgoing_swap_branch_group_count": len(
            outgoing_branch_keys
        ),
        "existing_outgoing_swap_branch_fact_count": (
            outgoing_branch_fact_count
        ),
        "existing_incoming_swap_branch_group_count": len(
            incoming_branch_keys
        ),
        "existing_incoming_swap_branch_fact_count": (
            incoming_branch_fact_count
        ),
        "existing_fact_source_dataset_counts": dict(
            sorted(source_dataset_counts.items())
        ),
        "aks": aks_statistics,
        "thesaurus": thesaurus_statistics,
        "evidence_count": len(evidence),
        "evidence_source_counts": dict(
            Counter(str(value) for value in evidence["source"])
        ),
        "evidence_rule_counts": dict(
            Counter(
                str(value) for value in evidence["discovery_rule"]
            )
        ),
        "candidate_fact_count": len(facts),
        "auto_accept_candidate_fact_count": int(
            facts["auto_accept_eligible"].eq(True).sum()
        ),
        "review_candidate_fact_count": int(
            facts["auto_accept_eligible"].eq(False).sum()
        ),
        "novel_candidate_fact_count": int(
            facts["novel_family_fact"].eq(True).sum()
        ),
        "novel_auto_accept_candidate_fact_count": int(
            novel_auto_mask.sum()
        ),
        "novel_auto_fact_touching_exam_anchor_count": (
            novel_auto_touch_exam_count
        ),
        "novel_auto_fact_between_exam_anchors_count": (
            novel_auto_both_exam_count
        ),
        "exam_anchor_candidate_fact_count": int(
            facts["touches_exam_anchor"].eq(True).sum()
        ),
        "both_exam_anchor_candidate_fact_count": int(
            facts["both_exam_anchors"].eq(True).sum()
        ),
        "combined_existing_and_novel_auto_fact_count": (
            len(canonical_facts)
            + int(novel_auto_mask.sum())
        ),
        "projected_fact_touching_exam_anchor_count": (
            existing_touch_exam_count
            + novel_auto_touch_exam_count
        ),
        "projected_fact_between_exam_anchors_count": (
            existing_both_exam_count
            + novel_auto_both_exam_count
        ),
        "projected_swap_eligible_fact_touching_exam_anchor_count": (
            len(swap_eligible_touch_exam)
            + novel_auto_touch_exam_count
        ),
        "projected_swap_eligible_fact_between_exam_anchors_count": (
            len(swap_eligible_both_exam)
            + novel_auto_both_exam_count
        ),
        "candidate_relation_family_counts": dict(
            Counter(
                str(value) for value in facts["relation_family"]
            )
        ),
        "audit_sample_count": len(audit_sample),
        "llm_used": False,
        "neo4j_load": False,
    }
    return tables, statistics

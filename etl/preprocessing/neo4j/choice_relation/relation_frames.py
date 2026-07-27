from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import new as new_hash
from itertools import product
from json import dumps
from pathlib import Path
import re

import pandas as pd

from common import load_policy_file
from choice_relation.deterministic_candidates import parse_json_list


def load_exam_relation_frame_policy(policy_path: str) -> dict:
    """기출 원자 관계 프레임 정책을 읽고 필수 구성을 검사한다."""
    policy = load_policy_file(Path(policy_path))
    policy_key = "exam_relation_frames"
    if policy_key not in policy:
        raise ValueError(f"{policy_key} 정책이 없습니다.")
    frame_policy = policy[policy_key]
    required_fields = {
        "policy_version",
        "supported_search_statuses",
        "atomic_clause_separator_pattern",
        "non_assertive_suffix_pattern",
        "assertive_light_verb_suffix_pattern",
        "passive_suffix_pattern",
        "role_suffix_rules",
        "place_entity_types",
        "pair_role_sets_by_family",
        "frame_statuses",
        "pair_statuses",
        "identifier",
        "outputs",
    }
    missing_fields = required_fields.difference(frame_policy)
    if missing_fields:
        missing_text = ", ".join(sorted(missing_fields))
        raise ValueError(
            f"기출 관계 프레임 정책 필드가 없습니다: {missing_text}"
        )
    return policy


def create_relation_frame_id(
    candidate_id: str,
    predicate_family: str,
    predicate_pattern: str,
    action_start: int,
    policy: dict,
) -> str:
    """후보·서술어·위치와 정책 버전에 고정되는 프레임 ID를 만든다."""
    frame_policy = policy["exam_relation_frames"]
    identifier = frame_policy["identifier"]
    hasher = new_hash(str(identifier["hash_algorithm"]))
    source = "|".join(
        [
            candidate_id,
            predicate_family,
            predicate_pattern,
            str(action_start),
            str(frame_policy["policy_version"]),
        ]
    )
    hasher.update(source.encode("utf-8"))
    digest_length = int(identifier["digest_length"])
    return (
        f"{identifier['frame_prefix']}"
        f"{hasher.hexdigest()[:digest_length]}"
    )


def is_true(value: object) -> bool:
    """CSV 문자열과 불리언을 같은 방식으로 판정한다."""
    return str(value).strip().lower() == "true"


def build_pattern_family_index(policy: dict) -> dict[str, str]:
    """관계 후보 정책의 서술어 표지를 관계 계열에 연결한다."""
    pattern_family: dict[str, str] = {}
    for rule in policy["exam_relation_candidates"][
        "relationship_trigger_rules"
    ]:
        family = str(rule["predicate_family"])
        for pattern_value in rule["patterns"]:
            pattern = str(pattern_value)
            existing_family = pattern_family.get(pattern)
            if existing_family and existing_family != family:
                raise ValueError(
                    f"서술어 표지가 여러 계열에 속합니다: {pattern}"
                )
            pattern_family[pattern] = family
    return pattern_family


def find_atomic_clause_spans(
    text: str,
    policy: dict,
) -> list[tuple[int, int]]:
    """접속 어미와 문장부호를 기준으로 원자 주장 구간을 나눈다."""
    separator_pattern = re.compile(
        str(
            policy["exam_relation_frames"][
                "atomic_clause_separator_pattern"
            ]
        )
    )
    spans: list[tuple[int, int]] = []
    span_start = 0
    for separator in separator_pattern.finditer(text):
        span_end = separator.end()
        if span_end > span_start:
            spans.append((span_start, span_end))
        span_start = span_end
    if span_start < len(text):
        spans.append((span_start, len(text)))
    if not spans:
        spans.append((0, len(text)))
    return spans


def find_pattern_occurrences(
    text: str,
    pattern: str,
) -> list[tuple[int, int]]:
    """단어 내부 오탐을 제외하고 서술어 표지 위치를 찾는다."""
    occurrences: list[tuple[int, int]] = []
    match_start = text.find(pattern)
    while match_start >= 0:
        if match_start == 0 or not text[match_start - 1].isalnum():
            occurrences.append(
                (match_start, match_start + len(pattern))
            )
        match_start = text.find(pattern, match_start + 1)
    return occurrences


def locate_clause_span(
    action_start: int,
    action_end: int,
    clause_spans: list[tuple[int, int]],
) -> tuple[int, int]:
    """서술어 위치를 포함하는 원자 주장 구간을 반환한다."""
    for clause_start, clause_end in clause_spans:
        if action_start >= clause_start and action_end <= clause_end:
            return clause_start, clause_end
    return 0, max(action_end, 0)


def build_name_regex(name: str) -> re.Pattern:
    """띄어쓰기 차이를 허용하는 canonical 이름 정규식을 만든다."""
    compact_name = re.sub(r"\s+", "", name)
    pattern = r"\s*".join(
        re.escape(character) for character in compact_name
    )
    return re.compile(pattern)


def classify_mention_role(
    suffix: str,
    entity_type: str,
    voice: str,
    policy: dict,
) -> tuple[str, str]:
    """조사와 태를 이용해 canonical 언급의 문장 역할을 정한다."""
    frame_policy = policy["exam_relation_frames"]
    for rule in frame_policy["role_suffix_rules"]:
        if not re.search(str(rule["pattern"]), suffix):
            continue
        role = str(rule["role"])
        basis = str(rule["basis"])
        if role == "GRAMMATICAL_SUBJECT" and voice == "PASSIVE":
            return "TARGET", f"{basis}_PASSIVE"
        if role == "GRAMMATICAL_SUBJECT":
            return "ACTOR", f"{basis}_ACTIVE"
        if role == "LOCATION_OR_ROLE":
            place_types = {
                str(value)
                for value in frame_policy["place_entity_types"]
            }
            if entity_type in place_types:
                return "LOCATION", basis
            return "ROLE", basis
        return role, basis
    return "UNKNOWN", "NO_SAFE_ROLE_MARKER"


def find_clause_mentions(
    text: str,
    clause_span: tuple[int, int],
    endpoint_rows: list[dict],
    voice: str,
    policy: dict,
) -> list[dict]:
    """원자 구간에서 겹치지 않는 canonical 언급과 역할을 찾는다."""
    clause_start, clause_end = clause_span
    candidates: list[dict] = []
    role_rules = policy["exam_relation_frames"][
        "role_suffix_rules"
    ]
    for endpoint in endpoint_rows:
        name = str(endpoint["display_name"]).strip()
        if not name:
            continue
        name_regex = build_name_regex(name)
        for name_match in name_regex.finditer(
            text,
            clause_start,
            clause_end,
        ):
            mention_start = name_match.start()
            mention_end = name_match.end()
            if (
                mention_start > 0
                and text[mention_start - 1].isalnum()
            ):
                continue
            suffix = text[mention_end:clause_end]
            next_character_is_word = (
                mention_end < len(text)
                and text[mention_end].isalnum()
            )
            safe_suffix = any(
                re.search(str(rule["pattern"]), suffix)
                for rule in role_rules
            )
            if next_character_is_word and not safe_suffix:
                continue
            role, role_basis = classify_mention_role(
                suffix,
                str(endpoint["entity_type"]),
                voice,
                policy,
            )
            candidates.append(
                {
                    "canonical_id": str(
                        endpoint["canonical_id"]
                    ),
                    "display_name": name,
                    "entity_type": str(endpoint["entity_type"]),
                    "mention_text": text[
                        mention_start:mention_end
                    ],
                    "mention_start": mention_start,
                    "mention_end": mention_end,
                    "participant_role": role,
                    "role_basis": role_basis,
                }
            )
    candidates.sort(
        key=lambda row: (
            int(row["mention_start"]),
            -(
                int(row["mention_end"])
                - int(row["mention_start"])
            ),
            str(row["canonical_id"]),
        )
    )
    mentions: list[dict] = []
    for candidate in candidates:
        overlaps = any(
            int(candidate["mention_start"])
            < int(observed["mention_end"])
            and int(candidate["mention_end"])
            > int(observed["mention_start"])
            for observed in mentions
        )
        if overlaps:
            continue
        mentions.append(candidate)
    for mention_index, mention in enumerate(mentions):
        if mention["participant_role"] != "COORDINATED":
            continue
        adjacent_roles: list[tuple[str, str]] = []
        if mention_index + 1 < len(mentions):
            next_mention = mentions[mention_index + 1]
            adjacent_roles.append(
                (
                    str(next_mention["participant_role"]),
                    "FORWARD_COORDINATION",
                )
            )
        if mention_index > 0:
            previous_mention = mentions[mention_index - 1]
            adjacent_roles.append(
                (
                    str(previous_mention["participant_role"]),
                    "BACKWARD_COORDINATION",
                )
            )
        for adjacent_role, basis in adjacent_roles:
            if adjacent_role in {"UNKNOWN", "COORDINATED"}:
                continue
            mention["participant_role"] = adjacent_role
            mention["role_basis"] = basis
            break
    return mentions


def pair_is_ready(
    predicate_family: str,
    mentions: list[dict],
    policy: dict,
) -> tuple[bool, list[str]]:
    """관계 계열별 허용 역할 조합에 서로 다른 두 endpoint가 있는지 본다."""
    canonical_ids_by_role: dict[str, set[str]] = defaultdict(set)
    for mention in mentions:
        canonical_ids_by_role[
            str(mention["participant_role"])
        ].add(str(mention["canonical_id"]))
    role_sets = policy["exam_relation_frames"][
        "pair_role_sets_by_family"
    ].get(predicate_family, [])
    for role_set in role_sets:
        required_roles = [str(role) for role in role_set]
        if any(
            not canonical_ids_by_role.get(role)
            for role in required_roles
        ):
            continue
        role_candidates = [
            sorted(canonical_ids_by_role[role])
            for role in required_roles
        ]
        for candidate_values in product(*role_candidates):
            if len(set(candidate_values)) != len(candidate_values):
                continue
            return True, sorted(set(candidate_values))
    return False, []


def build_exam_relation_frame_tables(
    relation_candidates: pd.DataFrame,
    text_checks: pd.DataFrame,
    text_evidence: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    policy: dict,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """공식 지지 문장을 원자 행동과 canonical 역할 프레임으로 바꾼다."""
    frame_policy = policy["exam_relation_frames"]
    supported_statuses = {
        str(value)
        for value in frame_policy["supported_search_statuses"]
    }
    candidate_by_id = {
        str(row["exam_relation_candidate_id"]): row
        for row in relation_candidates.to_dict("records")
    }
    eligible_checks = [
        row
        for row in text_checks.to_dict("records")
        if str(row["search_status"]) in supported_statuses
        and is_true(row["strict_support"])
    ]
    invalid_truth_ids = [
        str(row["exam_relation_candidate_id"])
        for row in eligible_checks
        if str(
            candidate_by_id.get(
                str(row["exam_relation_candidate_id"]),
                {},
            ).get("contextual_truth_status", "")
        )
        != "CONTEXTUALLY_TRUE"
    ]
    if invalid_truth_ids:
        raise ValueError(
            "문맥상 참이 아닌 후보가 관계 프레임 입력에 포함됐습니다: "
            + ", ".join(sorted(invalid_truth_ids))
        )
    eligible_ids = {
        str(row["exam_relation_candidate_id"])
        for row in eligible_checks
    }
    resolved_pair_by_candidate: dict[str, set[str]] = {}
    for row in eligible_checks:
        resolved_ids = set(
            parse_json_list(
                row.get("resolved_endpoint_ids_json", "")
            )
        )
        if resolved_ids:
            resolved_pair_by_candidate[
                str(row["exam_relation_candidate_id"])
            ] = resolved_ids
    evidence_rows = [
        row
        for row in text_evidence.to_dict("records")
        if str(row["exam_relation_candidate_id"]) in eligible_ids
        and (
            str(row["exam_relation_candidate_id"])
            not in resolved_pair_by_candidate
            or {
                str(row["start_canonical_id"]),
                str(row["end_canonical_id"]),
            }
            == resolved_pair_by_candidate[
                str(row["exam_relation_candidate_id"])
            ]
        )
    ]
    registry_by_id = {
        str(row["canonical_id"]): row
        for row in canonical_registry.to_dict("records")
    }
    endpoint_ids_by_candidate: dict[str, set[str]] = defaultdict(set)
    evidence_by_candidate: dict[str, list[dict]] = defaultdict(list)
    for evidence in evidence_rows:
        candidate_id = str(
            evidence["exam_relation_candidate_id"]
        )
        evidence_by_candidate[candidate_id].append(evidence)
        endpoint_ids_by_candidate[candidate_id].update(
            {
                str(evidence["start_canonical_id"]),
                str(evidence["end_canonical_id"]),
            }
        )
    pattern_family_index = build_pattern_family_index(policy)
    statuses = frame_policy["frame_statuses"]
    pair_statuses = frame_policy["pair_statuses"]
    non_assertive_pattern = re.compile(
        str(frame_policy["non_assertive_suffix_pattern"])
    )
    assertive_light_verb_pattern = re.compile(
        str(frame_policy["assertive_light_verb_suffix_pattern"])
    )
    passive_pattern = re.compile(
        str(frame_policy["passive_suffix_pattern"])
    )
    frame_rows: list[dict] = []
    participant_rows: list[dict] = []
    for check in eligible_checks:
        candidate_id = str(check["exam_relation_candidate_id"])
        exam_text = str(check["exam_evidence_text"])
        candidate_evidence = evidence_by_candidate.get(
            candidate_id,
            [],
        )
        supported_patterns = {
            pattern
            for evidence in candidate_evidence
            for pattern in parse_json_list(
                evidence["shared_predicate_patterns_json"]
            )
            if pattern in pattern_family_index
        }
        endpoint_rows = [
            registry_by_id[canonical_id]
            for canonical_id in sorted(
                endpoint_ids_by_candidate[candidate_id]
            )
            if canonical_id in registry_by_id
        ]
        clause_spans = find_atomic_clause_spans(
            exam_text,
            policy,
        )
        for predicate_pattern in sorted(supported_patterns):
            predicate_family = pattern_family_index[
                predicate_pattern
            ]
            occurrences = find_pattern_occurrences(
                exam_text,
                predicate_pattern,
            )
            for action_start, action_end in occurrences:
                clause_span = locate_clause_span(
                    action_start,
                    action_end,
                    clause_spans,
                )
                action_suffix = exam_text[action_end:clause_span[1]]
                action_asserted = bool(
                    assertive_light_verb_pattern.search(action_suffix)
                ) or not bool(
                    non_assertive_pattern.search(action_suffix)
                )
                voice = "ACTIVE"
                if passive_pattern.search(action_suffix):
                    voice = "PASSIVE"
                mentions = find_clause_mentions(
                    exam_text,
                    clause_span,
                    endpoint_rows,
                    voice,
                    policy,
                )
                pair_ready, pair_ids = pair_is_ready(
                    predicate_family,
                    mentions,
                    policy,
                )
                frame_status = str(statuses["partial"])
                pair_status = str(pair_statuses["not_available"])
                if not action_asserted:
                    frame_status = str(
                        statuses["action_not_asserted"]
                    )
                    pair_status = str(
                        pair_statuses["action_not_asserted"]
                    )
                    pair_ready = False
                    pair_ids = []
                elif any(
                    str(mention["participant_role"])
                    in {"UNKNOWN", "COORDINATED"}
                    for mention in mentions
                ):
                    frame_status = str(statuses["ambiguous"])
                elif pair_ready:
                    frame_status = str(statuses["resolved"])
                    pair_status = str(pair_statuses["ready"])
                frame_id = create_relation_frame_id(
                    candidate_id,
                    predicate_family,
                    predicate_pattern,
                    action_start,
                    policy,
                )
                matching_evidence = [
                    evidence
                    for evidence in candidate_evidence
                    if predicate_pattern
                    in parse_json_list(
                        evidence[
                            "shared_predicate_patterns_json"
                        ]
                    )
                ]
                role_values: dict[str, set[str]] = defaultdict(set)
                role_names: dict[str, set[str]] = defaultdict(set)
                for mention in mentions:
                    role = str(mention["participant_role"])
                    role_values[role].add(
                        str(mention["canonical_id"])
                    )
                    role_names[role].add(
                        str(mention["display_name"])
                    )
                    participant_rows.append(
                        {
                            "relation_frame_id": frame_id,
                            "exam_relation_candidate_id": (
                                candidate_id
                            ),
                            "canonical_id": str(
                                mention["canonical_id"]
                            ),
                            "display_name": str(
                                mention["display_name"]
                            ),
                            "entity_type": str(
                                mention["entity_type"]
                            ),
                            "participant_role": role,
                            "role_basis": str(
                                mention["role_basis"]
                            ),
                            "mention_text": str(
                                mention["mention_text"]
                            ),
                            "mention_start": int(
                                mention["mention_start"]
                            ),
                            "mention_end": int(
                                mention["mention_end"]
                            ),
                            "action_asserted": action_asserted,
                            "policy_version": str(
                                frame_policy["policy_version"]
                            ),
                        }
                    )
                frame_rows.append(
                    {
                        "relation_frame_id": frame_id,
                        "exam_relation_candidate_id": candidate_id,
                        "claim_segment_id": str(
                            check["claim_segment_id"]
                        ),
                        "problem_id": str(check["problem_id"]),
                        "contextual_truth_status": str(
                            candidate_by_id[candidate_id][
                                "contextual_truth_status"
                            ]
                        ),
                        "predicate_family": predicate_family,
                        "predicate_pattern": predicate_pattern,
                        "action_start": action_start,
                        "action_end": action_end,
                        "action_asserted": action_asserted,
                        "voice": voice,
                        "atomic_clause_text": exam_text[
                            clause_span[0]:clause_span[1]
                        ].strip(),
                        "frame_status": frame_status,
                        "pair_status": pair_status,
                        "actor_canonical_ids_json": dumps(
                            sorted(role_values["ACTOR"]),
                            ensure_ascii=False,
                        ),
                        "actor_names_json": dumps(
                            sorted(role_names["ACTOR"]),
                            ensure_ascii=False,
                        ),
                        "target_canonical_ids_json": dumps(
                            sorted(role_values["TARGET"]),
                            ensure_ascii=False,
                        ),
                        "target_names_json": dumps(
                            sorted(role_names["TARGET"]),
                            ensure_ascii=False,
                        ),
                        "location_canonical_ids_json": dumps(
                            sorted(role_values["LOCATION"]),
                            ensure_ascii=False,
                        ),
                        "location_names_json": dumps(
                            sorted(role_names["LOCATION"]),
                            ensure_ascii=False,
                        ),
                        "role_canonical_ids_json": dumps(
                            sorted(role_values["ROLE"]),
                            ensure_ascii=False,
                        ),
                        "role_names_json": dumps(
                            sorted(role_names["ROLE"]),
                            ensure_ascii=False,
                        ),
                        "context_canonical_ids_json": dumps(
                            sorted(role_values["CONTEXT"]),
                            ensure_ascii=False,
                        ),
                        "context_names_json": dumps(
                            sorted(role_names["CONTEXT"]),
                            ensure_ascii=False,
                        ),
                        "unresolved_canonical_ids_json": dumps(
                            sorted(
                                role_values["UNKNOWN"].union(
                                    role_values["COORDINATED"]
                                )
                            ),
                            ensure_ascii=False,
                        ),
                        "canonical_pair_candidate_ids_json": dumps(
                            pair_ids,
                            ensure_ascii=False,
                        ),
                        "official_text_evidence_ids_json": dumps(
                            sorted(
                                {
                                    str(
                                        evidence[
                                            "exam_official_text_evidence_id"
                                        ]
                                    )
                                    for evidence in matching_evidence
                                }
                            ),
                            ensure_ascii=False,
                        ),
                        "official_text_urls_json": dumps(
                            sorted(
                                {
                                    str(evidence["source_url"])
                                    for evidence in matching_evidence
                                    if str(evidence["source_url"])
                                }
                            ),
                            ensure_ascii=False,
                        ),
                        "official_evidence_sentences_json": dumps(
                            sorted(
                                {
                                    str(
                                        evidence[
                                            "official_evidence_sentence"
                                        ]
                                    )
                                    for evidence in matching_evidence
                                }
                            ),
                            ensure_ascii=False,
                        ),
                        "exam_evidence_text": exam_text,
                        "requires_official_fact_validation": (
                            action_asserted
                        ),
                        "direct_fact_projection_allowed": False,
                        "may_create_new_fact": False,
                        "llm_used": False,
                        "policy_version": str(
                            frame_policy["policy_version"]
                        ),
                    }
                )
    frame_columns = [
        "relation_frame_id",
        "exam_relation_candidate_id",
        "claim_segment_id",
        "problem_id",
        "contextual_truth_status",
        "predicate_family",
        "predicate_pattern",
        "action_start",
        "action_end",
        "action_asserted",
        "voice",
        "atomic_clause_text",
        "frame_status",
        "pair_status",
        "actor_canonical_ids_json",
        "actor_names_json",
        "target_canonical_ids_json",
        "target_names_json",
        "location_canonical_ids_json",
        "location_names_json",
        "role_canonical_ids_json",
        "role_names_json",
        "context_canonical_ids_json",
        "context_names_json",
        "unresolved_canonical_ids_json",
        "canonical_pair_candidate_ids_json",
        "official_text_evidence_ids_json",
        "official_text_urls_json",
        "official_evidence_sentences_json",
        "exam_evidence_text",
        "requires_official_fact_validation",
        "direct_fact_projection_allowed",
        "may_create_new_fact",
        "llm_used",
        "policy_version",
    ]
    participant_columns = [
        "relation_frame_id",
        "exam_relation_candidate_id",
        "canonical_id",
        "display_name",
        "entity_type",
        "participant_role",
        "role_basis",
        "mention_text",
        "mention_start",
        "mention_end",
        "action_asserted",
        "policy_version",
    ]
    frames = pd.DataFrame(frame_rows, columns=frame_columns)
    participants = pd.DataFrame(
        participant_rows,
        columns=participant_columns,
    )
    tables = {
        "frames": frames,
        "participants": participants,
    }
    validation_errors = validate_exam_relation_frame_tables(tables)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))
    statistics: dict[str, object] = {
        "eligible_candidate_count": len(eligible_checks),
        "frame_count": len(frames),
        "candidate_with_frame_count": int(
            frames["exam_relation_candidate_id"].nunique()
        ),
        "participant_count": len(participants),
        "frame_status_counts": dict(
            Counter(str(value) for value in frames["frame_status"])
        ),
        "pair_status_counts": dict(
            Counter(str(value) for value in frames["pair_status"])
        ),
        "asserted_action_count": int(
            frames["action_asserted"].eq(True).sum()
        ),
        "blocked_nominal_action_count": int(
            frames["action_asserted"].eq(False).sum()
        ),
        "pair_ready_count": int(
            frames["pair_status"]
            .eq(str(pair_statuses["ready"]))
            .sum()
        ),
        "direct_fact_projection_count": 0,
        "new_fact_creation_count": 0,
        "llm_used": False,
        "neo4j_load": False,
    }
    return tables, statistics


def validate_exam_relation_frame_tables(
    tables: dict[str, pd.DataFrame],
) -> list[str]:
    """프레임이 사실 관계를 직접 생성하지 않는지 검사한다."""
    frames = tables["frames"]
    participants = tables["participants"]
    errors: list[str] = []
    if frames["relation_frame_id"].duplicated().any():
        errors.append("기출 관계 프레임 ID가 중복되었습니다.")
    participant_key = [
        "relation_frame_id",
        "canonical_id",
        "mention_start",
        "mention_end",
    ]
    if participants.duplicated(participant_key).any():
        errors.append("같은 프레임 참여자가 중복되었습니다.")
    if frames["direct_fact_projection_allowed"].eq(True).any():
        errors.append("검증 전 프레임이 직접 사실 투영을 허용했습니다.")
    if frames["may_create_new_fact"].eq(True).any():
        errors.append("검증 전 프레임이 새 사실 생성을 허용했습니다.")
    if frames["contextual_truth_status"].ne(
        "CONTEXTUALLY_TRUE"
    ).any():
        errors.append("문맥상 참이 아닌 프레임이 생성되었습니다.")
    return errors

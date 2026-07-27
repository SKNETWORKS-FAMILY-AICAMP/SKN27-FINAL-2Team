from __future__ import annotations

from collections import Counter, defaultdict
from json import dumps, loads
import re
from typing import Iterable

from kiwipiepy import Kiwi
import pandas as pd

from choice_relation.exam_term_noun_phrases import (
    extract_noun_phrase_mentions,
)
from choice_relation.exam_term_raw_relations import (
    IndexedSurfaceMatcher,
    compact_surface,
    create_identifier,
    endpoint_from_group,
    extract_document_sentences,
)
from choice_relation.relation_frames import classify_mention_role
from choice_relation.source_first_fact_eda import (
    collect_asserted_actions,
)


def score_relation_orientation(
    anchor: dict,
    counterpart: dict,
    anchor_role: str,
    counterpart_role: str,
    anchor_side: str,
    sentence: str,
    action: dict,
    counterpart_registered: bool,
    counterpart_entity_type: str,
    nlp_policy: dict,
    policy: dict,
) -> tuple[int, list[str]]:
    """앵커와 상대 명사구의 역할 방향 하나를 근거별로 점수화한다."""
    weights = nlp_policy["score_weights"]
    score = 0
    bases: list[str] = []
    clause_end = int(action["clause_span"][1])
    anchor_suffix = sentence[
        int(anchor["mention_end"]):clause_end
    ]
    anchor_marker_pattern = nlp_policy[
        "case_marker_patterns_by_role"
    ].get(anchor_role)
    anchor_marker_match = bool(
        anchor_marker_pattern
        and re.search(str(anchor_marker_pattern), anchor_suffix)
    )
    if anchor_marker_match:
        score += int(weights["explicit_anchor_role"])
        bases.append("EXPLICIT_ANCHOR_ROLE")
    if not anchor_marker_match:
        classified_role, _ = classify_mention_role(
            anchor_suffix,
            str(anchor["entity_type"]),
            str(action["voice"]),
            policy,
        )
        if classified_role == anchor_role:
            score += int(weights["explicit_anchor_role"])
            bases.append("CLASSIFIED_ANCHOR_ROLE")
        elif classified_role != "UNKNOWN":
            score += int(weights["role_mismatch_penalty"])
            bases.append("ANCHOR_ROLE_MISMATCH")

    counterpart_suffix = sentence[
        int(counterpart["mention_end"]):clause_end
    ]
    counterpart_marker_pattern = nlp_policy[
        "case_marker_patterns_by_role"
    ].get(counterpart_role)
    if (
        counterpart_marker_pattern
        and re.search(
            str(counterpart_marker_pattern),
            counterpart_suffix,
        )
    ):
        score += int(weights["explicit_counterpart_role"])
        bases.append("EXPLICIT_COUNTERPART_ROLE")

    action_start = int(action["action_start"])
    action_end = int(action["action_end"])
    counterpart_start = int(counterpart["mention_start"])
    counterpart_end = int(counterpart["mention_end"])
    distance = 0
    if counterpart_end <= action_start:
        distance = action_start - counterpart_end
    elif counterpart_start >= action_end:
        distance = counterpart_start - action_end
    for band in nlp_policy["distance_score_bands"]:
        if distance <= int(band["maximum_distance"]):
            score += int(band["score"])
            bases.append(
                f"DISTANCE_LE_{band['maximum_distance']}"
            )
            break

    start_mention = anchor
    end_mention = counterpart
    if anchor_side == "END":
        start_mention = counterpart
        end_mention = anchor
    if (
        int(start_mention["mention_start"])
        < int(end_mention["mention_start"])
    ):
        score += int(weights["argument_order"])
        bases.append("EXPECTED_ARGUMENT_ORDER")

    if counterpart_registered:
        score += int(weights["registered_counterpart"])
        bases.append("REGISTERED_COUNTERPART")

    start_role = anchor_role
    end_role = counterpart_role
    start_entity_type = str(anchor["entity_type"])
    end_entity_type = counterpart_entity_type
    if anchor_side == "END":
        start_role = counterpart_role
        end_role = anchor_role
        start_entity_type = counterpart_entity_type
        end_entity_type = str(anchor["entity_type"])
    matching_contracts = [
        contract
        for contract in policy[
            "auto_accept_role_type_contracts"
        ].get(str(action["predicate_family"]), [])
        if str(contract["start_role"]) == start_role
        and str(contract["end_role"]) == end_role
    ]
    type_contract_compatible = False
    type_contract_conflict = False
    for contract in matching_contracts:
        endpoint_checks = [
            (
                start_entity_type,
                {
                    str(value)
                    for value in contract["start_types"]
                },
                anchor_side == "START"
                or counterpart_registered,
            ),
            (
                end_entity_type,
                {
                    str(value)
                    for value in contract["end_types"]
                },
                anchor_side == "END"
                or counterpart_registered,
            ),
        ]
        checked_types = [
            (entity_type, allowed_types)
            for entity_type, allowed_types, is_registered
            in endpoint_checks
            if is_registered and entity_type != "Unknown"
        ]
        if not checked_types:
            continue
        if all(
            entity_type in allowed_types
            for entity_type, allowed_types in checked_types
        ):
            type_contract_compatible = True
            continue
        type_contract_conflict = True
    if type_contract_compatible:
        score += int(weights["compatible_entity_type"])
        bases.append("TYPE_CONTRACT_COMPATIBLE")
    elif type_contract_conflict:
        score += int(weights["incompatible_entity_type"])
        bases.append("TYPE_CONTRACT_CONFLICT")
    counterpart_tags = {
        str(value)
        for value in loads(str(counterpart["pos_tags_json"]))
    }
    proper_noun_tags = {
        str(value)
        for value in nlp_policy["proper_noun_tags"]
    }
    if counterpart_tags.intersection(proper_noun_tags):
        score += int(weights["proper_noun"])
        bases.append("PROPER_NOUN")
    if int(counterpart["word_count"]) > 1:
        score += int(weights["multiword_phrase"])
        bases.append("MULTIWORD_PHRASE")

    for mention, role in [
        (anchor, anchor_role),
        (counterpart, counterpart_role),
    ]:
        if int(mention["mention_start"]) < action_end:
            continue
        if role not in {"TARGET", "LOCATION", "ROLE", "CONTEXT"}:
            continue
        predicate_gap = sentence[
            action_end:int(mention["mention_start"])
        ]
        if re.fullmatch(
            str(
                nlp_policy[
                    "post_predicate_attributive_pattern"
                ]
            ),
            predicate_gap,
        ):
            score += int(weights["post_predicate_attributive"])
            bases.append("POST_PREDICATE_ATTRIBUTIVE")

    if any(
        re.fullmatch(str(pattern), str(counterpart["surface"]))
        for pattern in nlp_policy["generic_surface_patterns"]
    ):
        score += int(weights["generic_surface_penalty"])
        bases.append("GENERIC_SURFACE_PENALTY")
    return score, bases


def build_exam_term_nlp_relation_tables(
    documents: Iterable[dict],
    exam_groups: dict[str, dict],
    target_groups: dict[str, dict],
    policy: dict,
    noun_policy: dict,
    nlp_policy: dict,
    kiwi: Kiwi,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """등록 기출 앵커와 같은 절의 NLP 명사구로 관계 후보를 만든다."""
    eda_policy = policy["exam_term_raw_relation_eda"]
    exam_matcher = IndexedSurfaceMatcher(
        exam_groups.keys(),
        eda_policy["following_particles"],
    )
    evidence_rows: list[dict] = []
    dataset_statistics: dict[str, Counter] = defaultdict(Counter)
    term_action_surfaces: set[str] = set()
    term_candidate_surfaces: set[str] = set()
    term_high_confidence_surfaces: set[str] = set()
    statuses = nlp_policy["statuses"]
    blocked_predicates_by_family = eda_policy[
        "blocked_predicate_patterns_by_family"
    ]
    open_entity_kind = str(
        eda_policy["target_node_kinds"]["open_entity"]
    )

    for document in documents:
        dataset = str(document["source_dataset"])
        dataset_statistics[dataset]["document_count"] += 1
        for sentence_row in extract_document_sentences(
            document,
            policy,
        ):
            dataset_statistics[dataset]["sentence_count"] += 1
            sentence = str(sentence_row["sentence"])
            exam_matches = exam_matcher.find(sentence)
            if not exam_matches:
                continue
            actions = collect_asserted_actions(sentence, policy)
            if not actions:
                continue
            noun_mentions = extract_noun_phrase_mentions(
                sentence,
                kiwi,
                noun_policy,
            )
            if not noun_mentions:
                continue
            dataset_statistics[dataset][
                "relation_trigger_sentence_count"
            ] += 1
            for asserted_action in actions:
                action = dict(asserted_action)
                if re.search(
                    str(eda_policy["passive_action_suffix_pattern"]),
                    str(action["action_suffix"]),
                ):
                    action["voice"] = "PASSIVE"
                family = str(action["predicate_family"])
                predicate = str(action["predicate_pattern"])
                blocked_predicates = {
                    str(value)
                    for value in blocked_predicates_by_family.get(
                        family,
                        [],
                    )
                }
                if predicate in blocked_predicates:
                    continue
                if re.search(
                    str(eda_policy["blocked_action_suffix_pattern"]),
                    str(action["action_suffix"]),
                ):
                    continue
                clause_start, clause_end = action["clause_span"]
                clause_text = sentence[clause_start:clause_end]
                if any(
                    re.search(str(pattern), clause_text)
                    for pattern in eda_policy[
                        "uncertainty_patterns"
                    ]
                ):
                    continue
                if any(
                    re.search(str(pattern), clause_text)
                    for pattern in eda_policy["negation_patterns"]
                ):
                    continue
                clause_anchors = [
                    match
                    for match in exam_matches
                    if int(match["mention_start"]) >= clause_start
                    and int(match["mention_end"]) <= clause_end
                ]
                clause_nouns = [
                    mention
                    for mention in noun_mentions
                    if int(mention["mention_start"]) >= clause_start
                    and int(mention["mention_end"]) <= clause_end
                    and not (
                        int(mention["mention_start"])
                        < int(action["action_end"])
                        and int(mention["mention_end"])
                        > int(action["action_start"])
                    )
                ]
                for anchor_match in clause_anchors:
                    anchor_group = exam_groups[
                        str(anchor_match["surface_key"])
                    ]
                    anchor_endpoint = endpoint_from_group(
                        anchor_group
                    )
                    if anchor_endpoint is None:
                        continue
                    anchor = {
                        **anchor_match,
                        **anchor_endpoint,
                    }
                    term_action_surfaces.add(
                        str(anchor_match["surface_key"])
                    )
                    scored_candidates: list[dict] = []
                    for counterpart in clause_nouns:
                        if (
                            int(counterpart["mention_start"])
                            < int(anchor["mention_end"])
                            and int(counterpart["mention_end"])
                            > int(anchor["mention_start"])
                        ):
                            continue
                        counterpart_start = int(
                            counterpart["mention_start"]
                        )
                        counterpart_end = int(
                            counterpart["mention_end"]
                        )
                        action_start = int(action["action_start"])
                        action_end = int(action["action_end"])
                        distance = int(
                            nlp_policy[
                                "maximum_counterpart_distance"
                            ]
                        ) + 1
                        if counterpart_end <= action_start:
                            distance = action_start - counterpart_end
                        elif counterpart_start >= action_end:
                            distance = counterpart_start - action_end
                            predicate_gap = sentence[
                                action_end:counterpart_start
                            ]
                            if not re.fullmatch(
                                str(
                                    nlp_policy[
                                        "post_predicate_attributive_pattern"
                                    ]
                                ),
                                predicate_gap,
                            ):
                                continue
                        if distance > int(
                            nlp_policy[
                                "maximum_counterpart_distance"
                            ]
                        ):
                            continue
                        if int(counterpart["word_count"]) > int(
                            nlp_policy[
                                "maximum_counterpart_word_count"
                            ]
                        ):
                            continue

                        surface_key = str(
                            counterpart["normalized_surface"]
                        )
                        endpoint_candidates: dict[str, dict] = {}
                        for group in [
                            exam_groups.get(surface_key),
                            target_groups.get(surface_key),
                        ]:
                            if group is None:
                                continue
                            for endpoint in group["endpoints"]:
                                endpoint_candidates[
                                    str(endpoint["endpoint_id"])
                                ] = dict(endpoint)
                        counterpart_registered = (
                            len(endpoint_candidates) == 1
                        )
                        counterpart_endpoint: dict | None = None
                        if counterpart_registered:
                            counterpart_endpoint = next(
                                iter(endpoint_candidates.values())
                            )
                            if (
                                str(
                                    counterpart_endpoint[
                                        "endpoint_id"
                                    ]
                                )
                                == str(anchor["endpoint_id"])
                            ):
                                continue
                        counterpart_entity_type = "Unknown"
                        if counterpart_endpoint is not None:
                            counterpart_entity_type = str(
                                counterpart_endpoint[
                                    "entity_type"
                                ]
                            )

                        best_orientation: dict | None = None
                        role_sets = policy["exam_relation_frames"][
                            "pair_role_sets_by_family"
                        ].get(family, [])
                        for role_set in role_sets:
                            if len(role_set) != 2:
                                continue
                            start_role = str(role_set[0])
                            end_role = str(role_set[1])
                            orientations = [
                                {
                                    "anchor_role": start_role,
                                    "counterpart_role": end_role,
                                    "anchor_side": "START",
                                },
                                {
                                    "anchor_role": end_role,
                                    "counterpart_role": start_role,
                                    "anchor_side": "END",
                                },
                            ]
                            for orientation in orientations:
                                score, score_bases = (
                                    score_relation_orientation(
                                        anchor,
                                        counterpart,
                                        str(
                                            orientation[
                                                "anchor_role"
                                            ]
                                        ),
                                        str(
                                            orientation[
                                                "counterpart_role"
                                            ]
                                        ),
                                        str(
                                            orientation[
                                                "anchor_side"
                                            ]
                                        ),
                                        sentence,
                                        action,
                                        counterpart_registered,
                                        counterpart_entity_type,
                                        nlp_policy,
                                        policy,
                                    )
                                )
                                candidate_orientation = {
                                    **orientation,
                                    "score": score,
                                    "score_bases": score_bases,
                                }
                                if best_orientation is None:
                                    best_orientation = (
                                        candidate_orientation
                                    )
                                    continue
                                if score > int(
                                    best_orientation["score"]
                                ):
                                    best_orientation = (
                                        candidate_orientation
                                    )
                        if best_orientation is None:
                            continue
                        if int(best_orientation["score"]) < int(
                            nlp_policy["minimum_candidate_score"]
                        ):
                            continue
                        ordered_mentions = sorted(
                            [anchor, counterpart],
                            key=lambda mention: int(
                                mention["mention_start"]
                            ),
                        )
                        between_arguments = sentence[
                            int(ordered_mentions[0]["mention_end"]):
                            int(
                                ordered_mentions[1][
                                    "mention_start"
                                ]
                            )
                        ]
                        relation_tail = ""
                        if (
                            int(
                                ordered_mentions[1][
                                    "mention_end"
                                ]
                            )
                            <= int(action["action_start"])
                        ):
                            relation_tail = sentence[
                                int(
                                    ordered_mentions[1][
                                        "mention_end"
                                    ]
                                ):
                                int(action["action_start"])
                            ]
                        structure_text = (
                            between_arguments + relation_tail
                        )
                        structural_conflict = bool(
                            re.search(
                                str(
                                    eda_policy[
                                        "intervening_predicate_pattern"
                                    ]
                                ),
                                structure_text,
                            )
                            or re.search(
                                str(
                                    eda_policy[
                                        "intervening_argument_boundary_pattern"
                                    ]
                                ),
                                structure_text,
                            )
                            or re.search(
                                str(
                                    eda_policy[
                                        "intervening_subject_pattern"
                                    ]
                                ),
                                structure_text,
                            )
                            or re.search(
                                str(
                                    nlp_policy[
                                        "nlp_structural_conflict_pattern"
                                    ]
                                ),
                                structure_text,
                            )
                        )
                        counterpart_role = str(
                            best_orientation["counterpart_role"]
                        )
                        if counterpart_endpoint is None:
                            type_key = f"{family}:{counterpart_role}"
                            open_policy = eda_policy[
                                "open_endpoint_extraction"
                            ]
                            entity_type = str(
                                open_policy[
                                    "entity_type_by_family_role"
                                ].get(
                                    type_key,
                                    open_policy[
                                        "default_entity_type_by_role"
                                    ][counterpart_role],
                                )
                            )
                            counterpart_endpoint = {
                                "endpoint_id": create_identifier(
                                    str(
                                        eda_policy["identifier"][
                                            "open_entity_prefix"
                                        ]
                                    ),
                                    [
                                        str(
                                            document[
                                                "source_document_id"
                                            ]
                                        ),
                                        str(
                                            sentence_row[
                                                "source_field"
                                            ]
                                        ),
                                        surface_key,
                                        entity_type,
                                    ],
                                    policy,
                                ),
                                "node_kind": open_entity_kind,
                                "canonical_id": "",
                                "source_record_id": "",
                                "display_name": str(
                                    counterpart["surface"]
                                ),
                                "entity_type": entity_type,
                                "source": dataset,
                                "source_url": str(
                                    document["source_url"]
                                ),
                                "is_exam_term": False,
                                "exam_term_id": "",
                            }
                        scored_candidates.append(
                            {
                                "counterpart": counterpart,
                                "counterpart_endpoint": (
                                    counterpart_endpoint
                                ),
                                "counterpart_registered": (
                                    counterpart_registered
                                ),
                                "structural_conflict": (
                                    structural_conflict
                                ),
                                **best_orientation,
                            }
                        )
                    scored_candidates.sort(
                        key=lambda row: (
                            -int(row["score"]),
                            -int(
                                row["counterpart_registered"]
                            ),
                            -len(
                                compact_surface(
                                    row["counterpart"]["surface"]
                                )
                            ),
                            int(
                                row["counterpart"][
                                    "mention_start"
                                ]
                            ),
                        )
                    )
                    maximum_candidates = int(
                        nlp_policy[
                            "maximum_candidates_per_anchor_action"
                        ]
                    )
                    selected_candidates = scored_candidates[
                        :maximum_candidates
                    ]
                    for rank, selected in enumerate(
                        selected_candidates,
                        start=1,
                    ):
                        next_score = -999
                        if rank < len(selected_candidates):
                            next_score = int(
                                selected_candidates[rank]["score"]
                            )
                        score_margin = int(selected["score"]) - next_score
                        explicit_role_evidence_count = sum(
                            basis
                            in {
                                "EXPLICIT_ANCHOR_ROLE",
                                "CLASSIFIED_ANCHOR_ROLE",
                                "EXPLICIT_COUNTERPART_ROLE",
                            }
                            for basis in selected["score_bases"]
                        )
                        type_contract_ready = (
                            "TYPE_CONTRACT_COMPATIBLE"
                            in selected["score_bases"]
                        )
                        if not bool(
                            nlp_policy[
                                "require_type_contract_for_high_confidence"
                            ]
                        ):
                            type_contract_ready = True
                        high_confidence = (
                            rank == 1
                            and int(selected["score"])
                            >= int(
                                nlp_policy[
                                    "high_confidence_score"
                                ]
                            )
                            and score_margin
                            >= int(
                                nlp_policy[
                                    "high_confidence_margin"
                                ]
                            )
                            and explicit_role_evidence_count
                            >= int(
                                nlp_policy[
                                    "minimum_explicit_role_evidence_count_for_high_confidence"
                                ]
                            )
                            and type_contract_ready
                            and not bool(
                                selected["structural_conflict"]
                            )
                        )
                        candidate_status = str(
                            statuses["ambiguous"]
                        )
                        if high_confidence and bool(
                            selected["counterpart_registered"]
                        ):
                            candidate_status = str(
                                statuses[
                                    "high_confidence_registered"
                                ]
                            )
                        elif high_confidence:
                            candidate_status = str(
                                statuses["high_confidence_open"]
                            )
                        elif rank == 1 and bool(
                            selected["counterpart_registered"]
                        ):
                            candidate_status = str(
                                statuses["review_registered"]
                            )
                        elif rank == 1:
                            candidate_status = str(
                                statuses["review_open"]
                            )

                        counterpart = selected["counterpart"]
                        counterpart_endpoint = selected[
                            "counterpart_endpoint"
                        ]
                        anchor_side = str(selected["anchor_side"])
                        start_endpoint = anchor
                        start_mention = anchor
                        start_role = str(
                            selected["anchor_role"]
                        )
                        end_endpoint = counterpart_endpoint
                        end_mention = counterpart
                        end_role = str(
                            selected["counterpart_role"]
                        )
                        if anchor_side == "END":
                            start_endpoint = counterpart_endpoint
                            start_mention = counterpart
                            start_role = str(
                                selected["counterpart_role"]
                            )
                            end_endpoint = anchor
                            end_mention = anchor
                            end_role = str(
                                selected["anchor_role"]
                            )
                        relation_type = str(
                            eda_policy[
                                "relation_type_by_predicate"
                            ].get(
                                predicate,
                                eda_policy[
                                    "relation_type_by_family"
                                ][family],
                            )
                        )
                        evidence_id = create_identifier(
                            str(
                                nlp_policy["identifier"][
                                    "evidence_prefix"
                                ]
                            ),
                            [
                                str(start_endpoint["endpoint_id"]),
                                relation_type,
                                str(end_endpoint["endpoint_id"]),
                                str(
                                    document["source_document_id"]
                                ),
                                str(
                                    sentence_row["source_field"]
                                ),
                                clause_text,
                                str(anchor["exam_term_id"]),
                                str(nlp_policy["policy_version"]),
                            ],
                            policy,
                        )
                        evidence_rows.append(
                            {
                                "nlp_relation_evidence_id": (
                                    evidence_id
                                ),
                                "anchor_exam_term_id": str(
                                    anchor["exam_term_id"]
                                ),
                                "anchor_surface": str(
                                    anchor["mention_text"]
                                ),
                                "anchor_side": anchor_side,
                                "start_node_id": str(
                                    start_endpoint["endpoint_id"]
                                ),
                                "start_node_kind": str(
                                    start_endpoint["node_kind"]
                                ),
                                "start_display_name": str(
                                    start_endpoint["display_name"]
                                ),
                                "start_mention_text": str(
                                    start_mention.get(
                                        "mention_text",
                                        start_mention.get(
                                            "surface",
                                            "",
                                        ),
                                    )
                                ),
                                "start_entity_type": str(
                                    start_endpoint["entity_type"]
                                ),
                                "start_role": start_role,
                                "end_node_id": str(
                                    end_endpoint["endpoint_id"]
                                ),
                                "end_node_kind": str(
                                    end_endpoint["node_kind"]
                                ),
                                "end_display_name": str(
                                    end_endpoint["display_name"]
                                ),
                                "end_mention_text": str(
                                    end_mention.get(
                                        "mention_text",
                                        end_mention.get(
                                            "surface",
                                            "",
                                        ),
                                    )
                                ),
                                "end_entity_type": str(
                                    end_endpoint["entity_type"]
                                ),
                                "end_role": end_role,
                                "relation_family": family,
                                "relation_type": relation_type,
                                "predicate_pattern": predicate,
                                "candidate_rank": rank,
                                "candidate_score": int(
                                    selected["score"]
                                ),
                                "score_margin": score_margin,
                                "score_bases_json": dumps(
                                    selected["score_bases"],
                                    ensure_ascii=False,
                                ),
                                "explicit_role_evidence_count": (
                                    explicit_role_evidence_count
                                ),
                                "type_contract_compatible": (
                                    "TYPE_CONTRACT_COMPATIBLE"
                                    in selected["score_bases"]
                                ),
                                "structural_conflict": bool(
                                    selected["structural_conflict"]
                                ),
                                "candidate_status": (
                                    candidate_status
                                ),
                                "counterpart_registered": bool(
                                    selected[
                                        "counterpart_registered"
                                    ]
                                ),
                                "source_dataset": dataset,
                                "source_document_id": str(
                                    document["source_document_id"]
                                ),
                                "source_title": str(
                                    document["source_title"]
                                ),
                                "source_field": str(
                                    sentence_row["source_field"]
                                ),
                                "source_url": str(
                                    document["source_url"]
                                ),
                                "evidence_sentence": sentence,
                                "atomic_clause_text": clause_text,
                                "minimum_registered_endpoint_count": int(
                                    nlp_policy[
                                        "minimum_registered_endpoint_count_per_relation"
                                    ]
                                ),
                                "auto_load_eligible": False,
                                "llm_used": False,
                                "neo4j_load": False,
                                "policy_version": str(
                                    nlp_policy["policy_version"]
                                ),
                            }
                        )
                        term_candidate_surfaces.add(
                            str(anchor_match["surface_key"])
                        )
                        if high_confidence:
                            term_high_confidence_surfaces.add(
                                str(anchor_match["surface_key"])
                            )
                        dataset_statistics[dataset][
                            "nlp_relation_evidence_count"
                        ] += 1

    evidence_columns = [
        "nlp_relation_evidence_id",
        "anchor_exam_term_id",
        "anchor_surface",
        "anchor_side",
        "start_node_id",
        "start_node_kind",
        "start_display_name",
        "start_mention_text",
        "start_entity_type",
        "start_role",
        "end_node_id",
        "end_node_kind",
        "end_display_name",
        "end_mention_text",
        "end_entity_type",
        "end_role",
        "relation_family",
        "relation_type",
        "predicate_pattern",
        "candidate_rank",
        "candidate_score",
        "score_margin",
        "score_bases_json",
        "explicit_role_evidence_count",
        "type_contract_compatible",
        "structural_conflict",
        "candidate_status",
        "counterpart_registered",
        "source_dataset",
        "source_document_id",
        "source_title",
        "source_field",
        "source_url",
        "evidence_sentence",
        "atomic_clause_text",
        "minimum_registered_endpoint_count",
        "auto_load_eligible",
        "llm_used",
        "neo4j_load",
        "policy_version",
    ]
    evidence = pd.DataFrame(
        evidence_rows,
        columns=evidence_columns,
    ).drop_duplicates(subset=["nlp_relation_evidence_id"])
    relation_columns = [
        "nlp_relation_candidate_id",
        "start_node_id",
        "start_node_kind",
        "start_display_name",
        "start_entity_type",
        "end_node_id",
        "end_node_kind",
        "end_display_name",
        "end_entity_type",
        "relation_family",
        "relation_type",
        "evidence_count",
        "anchor_exam_term_count",
        "anchor_exam_term_ids_json",
        "candidate_statuses_json",
        "maximum_candidate_score",
        "source_datasets_json",
        "evidence_ids_json",
        "touches_open_entity",
        "minimum_registered_endpoint_count",
        "auto_load_eligible",
        "llm_used",
        "neo4j_load",
        "policy_version",
    ]
    relation_rows: list[dict] = []
    if not evidence.empty:
        group_columns = [
            "start_node_id",
            "end_node_id",
            "relation_family",
            "relation_type",
        ]
        for group_key, group in evidence.groupby(
            group_columns,
            sort=True,
        ):
            start_node_id, end_node_id, family, relation_type = (
                group_key
            )
            relation_rows.append(
                {
                    "nlp_relation_candidate_id": create_identifier(
                        str(
                            nlp_policy["identifier"][
                                "relation_prefix"
                            ]
                        ),
                        [
                            str(start_node_id),
                            str(relation_type),
                            str(end_node_id),
                            str(nlp_policy["policy_version"]),
                        ],
                        policy,
                    ),
                    "start_node_id": str(start_node_id),
                    "start_node_kind": str(
                        group.iloc[0]["start_node_kind"]
                    ),
                    "start_display_name": str(
                        group.iloc[0]["start_display_name"]
                    ),
                    "start_entity_type": str(
                        group.iloc[0]["start_entity_type"]
                    ),
                    "end_node_id": str(end_node_id),
                    "end_node_kind": str(
                        group.iloc[0]["end_node_kind"]
                    ),
                    "end_display_name": str(
                        group.iloc[0]["end_display_name"]
                    ),
                    "end_entity_type": str(
                        group.iloc[0]["end_entity_type"]
                    ),
                    "relation_family": str(family),
                    "relation_type": str(relation_type),
                    "evidence_count": len(group),
                    "anchor_exam_term_count": int(
                        group["anchor_exam_term_id"].nunique()
                    ),
                    "anchor_exam_term_ids_json": dumps(
                        sorted(
                            {
                                str(value)
                                for value in group[
                                    "anchor_exam_term_id"
                                ]
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "candidate_statuses_json": dumps(
                        sorted(
                            {
                                str(value)
                                for value in group[
                                    "candidate_status"
                                ]
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "maximum_candidate_score": int(
                        group["candidate_score"].max()
                    ),
                    "source_datasets_json": dumps(
                        sorted(
                            {
                                str(value)
                                for value in group[
                                    "source_dataset"
                                ]
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "evidence_ids_json": dumps(
                        sorted(
                            {
                                str(value)
                                for value in group[
                                    "nlp_relation_evidence_id"
                                ]
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "touches_open_entity": bool(
                        group["start_node_kind"].eq(
                            open_entity_kind
                        ).any()
                        or group["end_node_kind"].eq(
                            open_entity_kind
                        ).any()
                    ),
                    "minimum_registered_endpoint_count": int(
                        nlp_policy[
                            "minimum_registered_endpoint_count_per_relation"
                        ]
                    ),
                    "auto_load_eligible": False,
                    "llm_used": False,
                    "neo4j_load": False,
                    "policy_version": str(
                        nlp_policy["policy_version"]
                    ),
                }
            )
    relations = pd.DataFrame(
        relation_rows,
        columns=relation_columns,
    )
    coverage_rows: list[dict] = []
    for surface_key, group in sorted(exam_groups.items()):
        endpoint = endpoint_from_group(group)
        exam_term_id = ""
        if endpoint is not None:
            exam_term_id = str(endpoint["exam_term_id"])
        coverage_rows.append(
            {
                "exam_term_id": exam_term_id,
                "term": str(group["display_surface"]),
                "appeared_in_relation_trigger_clause": (
                    surface_key in term_action_surfaces
                ),
                "has_nlp_relation_candidate": (
                    surface_key in term_candidate_surfaces
                ),
                "has_high_confidence_nlp_candidate": (
                    surface_key in term_high_confidence_surfaces
                ),
                "policy_version": str(
                    nlp_policy["policy_version"]
                ),
            }
        )
    coverage = pd.DataFrame(coverage_rows)
    total_term_count = len(coverage)
    candidate_term_count = len(term_candidate_surfaces)
    high_confidence_term_count = len(
        term_high_confidence_surfaces
    )
    candidate_term_coverage = 0.0
    high_confidence_term_coverage = 0.0
    if total_term_count:
        candidate_term_coverage = (
            candidate_term_count / total_term_count
        )
        high_confidence_term_coverage = (
            high_confidence_term_count / total_term_count
        )
    statistics = {
        "datasets": {
            dataset: dict(counts)
            for dataset, counts in sorted(
                dataset_statistics.items()
            )
        },
        "exam_term_count": total_term_count,
        "relation_trigger_term_count": len(term_action_surfaces),
        "nlp_relation_candidate_term_count": candidate_term_count,
        "nlp_relation_candidate_term_coverage": (
            candidate_term_coverage
        ),
        "high_confidence_nlp_term_count": (
            high_confidence_term_count
        ),
        "high_confidence_nlp_term_coverage": (
            high_confidence_term_coverage
        ),
        "nlp_relation_evidence_count": len(evidence),
        "nlp_relation_candidate_count": len(relations),
        "candidate_status_counts": {
            str(key): int(value)
            for key, value in evidence[
                "candidate_status"
            ].value_counts().to_dict().items()
        },
        "both_endpoints_unregistered_count": int(
            (
                evidence["start_node_kind"].eq(open_entity_kind)
                & evidence["end_node_kind"].eq(open_entity_kind)
            ).sum()
        ),
        "auto_load_eligible_count": 0,
        "llm_used": False,
        "neo4j_load": False,
    }
    return {
        "evidence": evidence,
        "relations": relations,
        "coverage": coverage,
    }, statistics

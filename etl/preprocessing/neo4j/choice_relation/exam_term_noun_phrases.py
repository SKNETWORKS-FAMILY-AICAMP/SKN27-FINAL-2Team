from __future__ import annotations

from collections import Counter, defaultdict
from json import dumps, loads
import re
from typing import Iterable

from kiwipiepy import Kiwi
import pandas as pd

from choice_relation.exam_term_raw_relations import (
    IndexedSurfaceMatcher,
    compact_surface,
    create_identifier,
    extract_document_sentences,
)
from choice_relation.source_first_fact_eda import (
    collect_asserted_actions,
)


def extract_noun_phrase_mentions(
    sentence: str,
    kiwi: Kiwi,
    noun_policy: dict,
) -> list[dict]:
    """Kiwi 형태소를 명사 어절로 묶고 연속 명사구 n-gram을 만든다."""
    noun_tags = {
        str(value) for value in noun_policy["noun_tags"]
    }
    noun_suffix_tags = {
        str(value) for value in noun_policy["noun_suffix_tags"]
    }
    sequences: list[list[dict]] = []
    current_sequence: list[dict] = []
    current_word_tokens: list[object] = []

    for token in kiwi.tokenize(sentence):
        token_tag = str(token.tag)
        token_start = int(token.start)
        token_end = token_start + int(token.len)
        if token_tag in noun_tags:
            if current_word_tokens:
                previous = current_word_tokens[-1]
                previous_end = int(previous.start) + int(
                    previous.len
                )
                if token_start == previous_end:
                    current_word_tokens.append(token)
                    continue
                current_sequence.append(
                    {
                        "start": int(
                            current_word_tokens[0].start
                        ),
                        "end": previous_end,
                        "tokens": list(current_word_tokens),
                    }
                )
                current_word_tokens = []
            current_word_tokens.append(token)
            continue
        if token_tag in noun_suffix_tags and current_word_tokens:
            previous = current_word_tokens[-1]
            previous_end = int(previous.start) + int(previous.len)
            if token_start == previous_end:
                current_word_tokens.append(token)
                continue
        if current_word_tokens:
            previous = current_word_tokens[-1]
            current_sequence.append(
                {
                    "start": int(current_word_tokens[0].start),
                    "end": int(previous.start) + int(previous.len),
                    "tokens": list(current_word_tokens),
                }
            )
            current_word_tokens = []
        if current_sequence:
            sequences.append(current_sequence)
            current_sequence = []

    if current_word_tokens:
        previous = current_word_tokens[-1]
        current_sequence.append(
            {
                "start": int(current_word_tokens[0].start),
                "end": int(previous.start) + int(previous.len),
                "tokens": list(current_word_tokens),
            }
        )
    if current_sequence:
        sequences.append(current_sequence)

    maximum_word_count = int(
        noun_policy["maximum_phrase_word_count"]
    )
    minimum_length = int(noun_policy["minimum_compact_length"])
    maximum_length = int(noun_policy["maximum_phrase_length"])
    blocked_patterns = [
        str(value)
        for value in noun_policy["blocked_phrase_patterns"]
    ]
    candidates: dict[tuple[int, int, str], dict] = {}
    for sequence in sequences:
        for start_index in range(len(sequence)):
            maximum_end = min(
                len(sequence),
                start_index + maximum_word_count,
            )
            for end_index in range(start_index + 1, maximum_end + 1):
                selected_words = sequence[start_index:end_index]
                mention_start = int(selected_words[0]["start"])
                mention_end = int(selected_words[-1]["end"])
                surface = re.sub(
                    r"\s+",
                    " ",
                    sentence[mention_start:mention_end],
                ).strip()
                normalized_surface = compact_surface(surface)
                if len(normalized_surface) < minimum_length:
                    continue
                if len(surface) > maximum_length:
                    continue
                if any(
                    re.fullmatch(pattern, surface)
                    for pattern in blocked_patterns
                ):
                    continue
                selected_tokens = [
                    token
                    for word in selected_words
                    for token in word["tokens"]
                ]
                candidate_key = (
                    mention_start,
                    mention_end,
                    normalized_surface,
                )
                candidates[candidate_key] = {
                    "mention_start": mention_start,
                    "mention_end": mention_end,
                    "surface": surface,
                    "normalized_surface": normalized_surface,
                    "word_count": len(selected_words),
                    "morphemes_json": dumps(
                        [
                            str(token.form)
                            for token in selected_tokens
                        ],
                        ensure_ascii=False,
                    ),
                    "pos_tags_json": dumps(
                        [
                            str(token.tag)
                            for token in selected_tokens
                        ],
                        ensure_ascii=False,
                    ),
                }
    maximum_candidates = int(
        noun_policy["maximum_candidates_per_sentence"]
    )
    return sorted(
        candidates.values(),
        key=lambda row: (
            int(row["mention_start"]),
            int(row["word_count"]),
            str(row["normalized_surface"]),
        ),
    )[:maximum_candidates]


def build_noun_phrase_eda_tables(
    documents: Iterable[dict],
    exam_groups: dict[str, dict],
    target_groups: dict[str, dict],
    policy: dict,
    noun_policy: dict,
    kiwi: Kiwi,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """기출 용어 포함 공식 문장에서 명사구를 수집하고 등록 상태를 붙인다."""
    exam_matcher = IndexedSurfaceMatcher(
        exam_groups.keys(),
        policy["exam_term_raw_relation_eda"][
            "following_particles"
        ],
    )
    rows: list[dict] = []
    dataset_statistics: dict[str, Counter] = defaultdict(Counter)
    statuses = noun_policy["statuses"]
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
            dataset_statistics[dataset][
                "exam_mention_sentence_count"
            ] += 1
            actions = collect_asserted_actions(sentence, policy)
            if (
                bool(noun_policy["require_relation_trigger"])
                and not actions
            ):
                continue
            dataset_statistics[dataset][
                "relation_trigger_sentence_count"
            ] += 1
            predicate_families = sorted(
                {
                    str(action["predicate_family"])
                    for action in actions
                }
            )
            noun_mentions = extract_noun_phrase_mentions(
                sentence,
                kiwi,
                noun_policy,
            )
            for mention in noun_mentions:
                surface_key = str(
                    mention["normalized_surface"]
                )
                exam_group = exam_groups.get(surface_key)
                target_group = target_groups.get(surface_key)
                match_status = str(statuses["unregistered"])
                endpoint_ids: set[str] = set()
                endpoint_kinds: set[str] = set()
                if exam_group is not None:
                    if int(exam_group["endpoint_count"]) == 1:
                        match_status = str(statuses["exam_term"])
                    elif int(exam_group["endpoint_count"]) > 1:
                        match_status = str(statuses["ambiguous"])
                    for endpoint in exam_group["endpoints"]:
                        endpoint_ids.add(
                            str(endpoint["endpoint_id"])
                        )
                        endpoint_kinds.add(
                            str(endpoint["node_kind"])
                        )
                if target_group is not None:
                    if (
                        match_status
                        == str(statuses["unregistered"])
                        and int(target_group["endpoint_count"]) == 1
                    ):
                        match_status = str(statuses["registered"])
                    elif int(target_group["endpoint_count"]) > 1:
                        match_status = str(statuses["ambiguous"])
                    for endpoint in target_group["endpoints"]:
                        endpoint_ids.add(
                            str(endpoint["endpoint_id"])
                        )
                        endpoint_kinds.add(
                            str(endpoint["node_kind"])
                        )
                relation_anchor_eligible = match_status in {
                    str(statuses["exam_term"]),
                    str(statuses["registered"]),
                }
                candidate_id = create_identifier(
                    str(
                        noun_policy["identifier"][
                            "candidate_prefix"
                        ]
                    ),
                    [
                        str(document["source_document_id"]),
                        str(sentence_row["source_field"]),
                        str(mention["mention_start"]),
                        str(mention["mention_end"]),
                        surface_key,
                        str(noun_policy["policy_version"]),
                    ],
                    policy,
                )
                rows.append(
                    {
                        "noun_phrase_candidate_id": candidate_id,
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
                        "source_url": str(document["source_url"]),
                        "sentence": sentence,
                        "predicate_families_json": dumps(
                            predicate_families,
                            ensure_ascii=False,
                        ),
                        "mention_start": int(
                            mention["mention_start"]
                        ),
                        "mention_end": int(
                            mention["mention_end"]
                        ),
                        "noun_phrase": str(mention["surface"]),
                        "normalized_surface": surface_key,
                        "word_count": int(
                            mention["word_count"]
                        ),
                        "morphemes_json": str(
                            mention["morphemes_json"]
                        ),
                        "pos_tags_json": str(
                            mention["pos_tags_json"]
                        ),
                        "registration_status": match_status,
                        "registered_endpoint_ids_json": dumps(
                            sorted(endpoint_ids),
                            ensure_ascii=False,
                        ),
                        "registered_endpoint_kinds_json": dumps(
                            sorted(endpoint_kinds),
                            ensure_ascii=False,
                        ),
                        "relation_anchor_eligible": (
                            relation_anchor_eligible
                        ),
                        "node_creation_eligible": False,
                        "llm_used": False,
                        "neo4j_load": False,
                        "policy_version": str(
                            noun_policy["policy_version"]
                        ),
                    }
                )
                dataset_statistics[dataset][
                    "noun_phrase_mention_count"
                ] += 1

    mention_columns = [
        "noun_phrase_candidate_id",
        "source_dataset",
        "source_document_id",
        "source_title",
        "source_field",
        "source_url",
        "sentence",
        "predicate_families_json",
        "mention_start",
        "mention_end",
        "noun_phrase",
        "normalized_surface",
        "word_count",
        "morphemes_json",
        "pos_tags_json",
        "registration_status",
        "registered_endpoint_ids_json",
        "registered_endpoint_kinds_json",
        "relation_anchor_eligible",
        "node_creation_eligible",
        "llm_used",
        "neo4j_load",
        "policy_version",
    ]
    mentions = pd.DataFrame(rows, columns=mention_columns)
    if not mentions.empty:
        mentions = mentions.drop_duplicates(
            subset=["noun_phrase_candidate_id"]
        )
    surface_columns = [
        "noun_phrase_surface_id",
        "noun_phrase",
        "normalized_surface",
        "mention_count",
        "source_document_count",
        "source_datasets_json",
        "registration_statuses_json",
        "registered_endpoint_ids_json",
        "relation_anchor_eligible",
        "node_creation_eligible",
        "llm_used",
        "neo4j_load",
        "policy_version",
    ]
    surface_rows: list[dict] = []
    if not mentions.empty:
        for surface_key, group in mentions.groupby(
            "normalized_surface",
            sort=True,
        ):
            endpoint_ids: set[str] = set()
            for value in group[
                "registered_endpoint_ids_json"
            ]:
                endpoint_ids.update(
                    str(item)
                    for item in loads(str(value))
                )
            surface_rows.append(
                {
                    "noun_phrase_surface_id": create_identifier(
                        str(
                            noun_policy["identifier"][
                                "surface_prefix"
                            ]
                        ),
                        [
                            str(surface_key),
                            str(noun_policy["policy_version"]),
                        ],
                        policy,
                    ),
                    "noun_phrase": str(
                        group.iloc[0]["noun_phrase"]
                    ),
                    "normalized_surface": str(surface_key),
                    "mention_count": len(group),
                    "source_document_count": int(
                        group["source_document_id"].nunique()
                    ),
                    "source_datasets_json": dumps(
                        sorted(
                            {
                                str(value)
                                for value in group["source_dataset"]
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "registration_statuses_json": dumps(
                        sorted(
                            {
                                str(value)
                                for value in group[
                                    "registration_status"
                                ]
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "registered_endpoint_ids_json": dumps(
                        sorted(endpoint_ids),
                        ensure_ascii=False,
                    ),
                    "relation_anchor_eligible": bool(
                        group["relation_anchor_eligible"].eq(
                            True
                        ).any()
                    ),
                    "node_creation_eligible": False,
                    "llm_used": False,
                    "neo4j_load": False,
                    "policy_version": str(
                        noun_policy["policy_version"]
                    ),
                }
            )
    surfaces = pd.DataFrame(surface_rows, columns=surface_columns)
    registration_status_counts: dict[str, int] = {}
    relation_anchor_eligible_mention_count = 0
    if not mentions.empty:
        registration_status_counts = {
            str(key): int(value)
            for key, value in mentions[
                "registration_status"
            ].value_counts().to_dict().items()
        }
        relation_anchor_eligible_mention_count = int(
            mentions["relation_anchor_eligible"].eq(True).sum()
        )
    statistics = {
        "datasets": {
            dataset: dict(counts)
            for dataset, counts in sorted(
                dataset_statistics.items()
            )
        },
        "noun_phrase_mention_count": len(mentions),
        "unique_noun_phrase_surface_count": len(surfaces),
        "registration_status_counts": registration_status_counts,
        "relation_anchor_eligible_mention_count": (
            relation_anchor_eligible_mention_count
        ),
        "minimum_registered_endpoint_count_per_relation": int(
            noun_policy[
                "minimum_registered_endpoint_count_per_relation"
            ]
        ),
        "relation_candidate_count": 0,
        "node_creation_eligible_count": 0,
        "llm_used": False,
        "neo4j_load": False,
    }
    return {
        "mentions": mentions,
        "surfaces": surfaces,
    }, statistics

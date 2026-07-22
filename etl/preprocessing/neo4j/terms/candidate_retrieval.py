from collections import Counter, defaultdict
from difflib import SequenceMatcher

from prep_thesaurus import build_match_key


def build_search_index(records: list[dict], retrieval_policy: dict) -> dict:
    """이름과 설명의 문자 n-gram 역색인을 구축한다."""
    ngram_sizes = retrieval_policy["ngram_sizes"]
    exact_index: dict[str, set[int]] = defaultdict(set)
    name_ngram_index: dict[str, set[int]] = defaultdict(set)
    description_ngram_index: dict[str, set[int]] = defaultdict(set)
    entries: list[dict] = []

    for record in records:
        names = [str(name).strip() for name in record.get("search_names", []) if str(name).strip()]
        name_pairs = [(name, build_match_key(name)) for name in names]
        name_pairs = [(name, key) for name, key in name_pairs if key]
        description = str(record.get("search_text") or "").strip()
        description_key = build_match_key(description)
        entry_id = len(entries)
        entries.append(
            {
                "name_pairs": name_pairs,
                "description": description,
                "description_key": description_key,
                "payload": record["payload"],
            }
        )

        for _, name_key in name_pairs:
            exact_index[name_key].add(entry_id)
            for ngram in make_ngrams(name_key, ngram_sizes):
                name_ngram_index[ngram].add(entry_id)
        for ngram in make_ngrams(description_key, ngram_sizes):
            description_ngram_index[ngram].add(entry_id)

    return {
        "entries": entries,
        "exact": exact_index,
        "name_ngrams": name_ngram_index,
        "description_ngrams": description_ngram_index,
    }


def make_ngrams(text: str, sizes: list[int]) -> set[str]:
    """설정된 길이의 문자 n-gram 집합을 만든다."""
    ngrams: set[str] = set()
    for size in sizes:
        if size <= 0 or len(text) < size:
            continue
        ngrams.update(text[index:index + size] for index in range(len(text) - size + 1))
    return ngrams


def calculate_ngram_coverage(query: str, target: str, sizes: list[int]) -> float:
    """크기별 쿼리 n-gram 재현율 중 가장 높은 값을 반환한다."""
    query_ngram_groups = {
        size: make_ngrams(query, [size])
        for size in sizes
    }
    return calculate_target_coverage(query_ngram_groups, target)


def calculate_target_coverage(
    query_ngram_groups: dict[int, set[str]],
    target: str,
) -> float:
    """미리 계산한 쿼리 n-gram의 대상 문자열 포함 비율을 반환한다."""
    coverages: list[float] = []
    for query_ngrams in query_ngram_groups.values():
        if not query_ngrams:
            continue
        shared_count = sum(ngram in target for ngram in query_ngrams)
        coverages.append(shared_count / len(query_ngrams))
    if not coverages:
        return 0.0
    return max(coverages)


def retrieve_candidates(
    term: str,
    search_index: dict,
    retrieval_policy: dict,
    policy_version: str,
    max_candidates: int | None = None,
) -> list[dict]:
    """정확·양방향 포함·문자 유사도·설명 유사도를 결합해 후보를 반환한다."""
    query_key = build_match_key(term)
    if not query_key:
        return []

    ngram_sizes = retrieval_policy["ngram_sizes"]
    query_ngram_groups = {
        size: make_ngrams(query_key, [size])
        for size in ngram_sizes
    }
    query_characters = set(query_key)
    posting_weights = retrieval_policy["posting_weights"]
    hit_counts: Counter[int] = Counter()
    exact_ids = search_index["exact"].get(query_key, set())
    for entry_id in exact_ids:
        hit_counts[entry_id] += posting_weights["name"]

    for ngram in make_ngrams(query_key, ngram_sizes):
        for entry_id in search_index["name_ngrams"].get(ngram, set()):
            hit_counts[entry_id] += posting_weights["name"]
        for entry_id in search_index["description_ngrams"].get(ngram, set()):
            hit_counts[entry_id] += posting_weights["description"]

    evaluation_limit = retrieval_policy["max_evaluated_candidates"]
    evaluated_ids = {entry_id for entry_id, _ in hit_counts.most_common(evaluation_limit)}
    evaluated_ids.update(exact_ids)

    weights = retrieval_policy["score_weights"]
    minimum_containment_length = retrieval_policy["minimum_containment_length"]
    containment_length_power = retrieval_policy["containment_length_power"]
    reverse_minimum_length = retrieval_policy["reverse_containment_minimum_length"]
    reverse_minimum_ratio = retrieval_policy["reverse_containment_minimum_ratio"]
    minimum_score = retrieval_policy["minimum_score"]
    candidates: list[dict] = []
    for entry_id in evaluated_ids:
        entry = search_index["entries"][entry_id]
        best_name = ""
        best_name_key = ""
        best_name_coverage = 0.0
        best_sequence_similarity = 0.0
        best_character_overlap = 0.0
        containment = 0.0

        for name, name_key in entry["name_pairs"]:
            name_coverage = calculate_target_coverage(
                query_ngram_groups,
                name_key,
            )
            sequence_similarity = SequenceMatcher(None, query_key, name_key).ratio()
            name_characters = set(name_key)
            character_denominator = len(query_characters) + len(name_characters)
            character_overlap = 0.0
            if character_denominator > 0:
                character_overlap = (
                    2 * len(query_characters.intersection(name_characters))
                    / character_denominator
                )

            # 정방향: 용어가 표제어에 포함 (운요호 -> 운요호사건)
            # 역방향: 표제어가 용어에 포함 (짧은 표제어의 오탐이 많아
            #   '묘수' ⊂ '진묘수' 같은 매칭을 막기 위해 길이·비율 조건을 더 건다)
            shorter_length = min(len(query_key), len(name_key))
            longer_length = max(len(query_key), len(name_key))
            length_ratio = 0.0
            if longer_length > 0:
                length_ratio = shorter_length / longer_length
            forward_contains = (
                query_key in name_key
                and shorter_length >= minimum_containment_length
            )
            reverse_contains = (
                name_key in query_key
                and name_key != query_key
                and len(name_key) >= reverse_minimum_length
                and length_ratio >= reverse_minimum_ratio
            )
            current_containment = 0.0
            if forward_contains or reverse_contains:
                current_containment = length_ratio ** containment_length_power

            name_rank = (
                current_containment,
                name_coverage,
                sequence_similarity,
                character_overlap,
            )
            best_rank = (
                containment,
                best_name_coverage,
                best_sequence_similarity,
                best_character_overlap,
            )
            if name_rank > best_rank:
                best_name = name
                best_name_key = name_key
                containment = current_containment
                best_name_coverage = name_coverage
                best_sequence_similarity = sequence_similarity
                best_character_overlap = character_overlap

        description_coverage = calculate_target_coverage(
            query_ngram_groups,
            entry["description_key"],
        )
        # 용어 전체가 설명 문장에 그대로 등장하면 강한 신호로 별도 가산한다
        # ('진묘수'처럼 표제어가 다른 이름(무령왕릉 석수)인 문서를 설명으로 잡는 채널)
        description_containment = 0.0
        if (
            len(query_key) >= minimum_containment_length
            and query_key in entry["description_key"]
        ):
            description_containment = 1.0
        is_exact = query_key == best_name_key
        score = (
            containment * weights["bidirectional_containment"]
            + best_name_coverage * weights["name_ngram_coverage"]
            + best_sequence_similarity * weights["name_sequence_similarity"]
            + best_character_overlap * weights["name_character_overlap"]
            + description_coverage * weights["description_ngram_coverage"]
            + description_containment * weights["description_containment"]
        )
        if is_exact:
            score = 1.0
        if not is_exact and score < minimum_score:
            continue

        methods: list[str] = []
        if is_exact:
            methods.append("exact")
        if containment > 0.0 and not is_exact:
            methods.append("bidirectional_containment")
        if best_name_coverage > 0.0 and not is_exact:
            methods.append("name_ngram")
        if description_containment > 0.0 and not is_exact:
            methods.append("description_containment")
        if description_coverage > 0.0 and not is_exact:
            methods.append("description_ngram")
        if best_sequence_similarity > 0.0 and not is_exact:
            methods.append("sequence_similarity")

        matched_field = "name"
        if not is_exact and description_containment > 0.0:
            matched_field = "description"
        elif (
            not is_exact
            and description_coverage > containment
            and description_coverage > best_name_coverage
        ):
            matched_field = "description"

        primary_method = "sequence_similarity"
        if is_exact:
            primary_method = "exact"
        elif description_containment > 0.0 and containment == 0.0:
            primary_method = "description_containment"
        elif containment > 0.0:
            primary_method = "bidirectional_containment"
        elif matched_field == "description":
            primary_method = "description_ngram"
        elif best_name_coverage > 0.0:
            primary_method = "name_ngram"

        candidate = dict(entry["payload"])
        candidate.update(
            {
                "matched_name": best_name,
                "matched_field": matched_field,
                "retrieval_method": primary_method,
                "retrieval_methods": methods,
                "retrieval_score": round(score, 6),
                "score_components": {
                    "bidirectional_containment": round(containment, 6),
                    "name_ngram_coverage": round(best_name_coverage, 6),
                    "name_sequence_similarity": round(best_sequence_similarity, 6),
                    "name_character_overlap": round(best_character_overlap, 6),
                    "description_ngram_coverage": round(description_coverage, 6),
                    "description_containment": round(description_containment, 6),
                },
                "verification_status": "PROPOSED",
                "retrieval_policy_version": policy_version,
            }
        )
        candidates.append(candidate)

    candidate_limit = max_candidates
    if candidate_limit is None:
        candidate_limit = retrieval_policy["max_candidates"]
    candidates.sort(
        key=lambda candidate: (
            -candidate["retrieval_score"],
            str(candidate.get("source_record_id") or ""),
        )
    )
    exact_candidates = [
        candidate
        for candidate in candidates
        if candidate["retrieval_method"] == "exact"
    ]
    fuzzy_candidates = [
        candidate
        for candidate in candidates
        if candidate["retrieval_method"] != "exact"
    ]
    if exact_candidates:
        minimum_expanded_score = retrieval_policy[
            "minimum_expanded_score_with_exact_candidate"
        ]
        fuzzy_candidates = [
            candidate
            for candidate in fuzzy_candidates
            if candidate["retrieval_score"] >= minimum_expanded_score
        ]
    fuzzy_limit = max(candidate_limit - len(exact_candidates), 0)
    return exact_candidates + fuzzy_candidates[:fuzzy_limit]

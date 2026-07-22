import re
import sys
import unicodedata
from argparse import ArgumentParser
from collections import deque
from json import dump, load
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import calculate_source_release, load_pipeline_policy
from match_names import get_primary_type_part, is_category_compatible
from prep_thesaurus import iter_encyclopedia_rows
from scan_definitions import collect_aks_enrichment_terms

def normalize_search_text(text: str) -> str:
    """본문 대조용으로 유니코드·대소문자를 정리하고 부호·공백을 제거한다."""
    normalized = unicodedata.normalize("NFC", str(text)).casefold()
    return re.sub(r"[^0-9a-z가-힣一-龥]", "", normalized)


def build_normalized_view(text: str) -> tuple[str, list[int]]:
    """정규화 문자열과 각 문자의 원본 위치 매핑을 만든다 (스니펫 추출용)."""
    source = unicodedata.normalize("NFC", str(text))
    keep_pattern = re.compile(r"[^0-9a-z가-힣一-龥]")
    normalized_characters: list[str] = []
    source_positions: list[int] = []
    for position, character in enumerate(source):
        lowered = character.casefold()
        if len(lowered) == 1 and not keep_pattern.match(lowered):
            normalized_characters.append(lowered)
            source_positions.append(position)
    return "".join(normalized_characters), source_positions


def split_term_tokens(term: str, minimum_length: int) -> list[str]:
    """용어를 공백 기준 토큰으로 나누고 대조용으로 정규화한다."""
    tokens: list[str] = []
    for raw_token in str(term).split():
        token = normalize_search_text(raw_token)
        if len(token) >= minimum_length and token not in tokens:
            tokens.append(token)
    if not tokens:
        whole_term = normalize_search_text(term)
        if whole_term:
            tokens.append(whole_term)
    return tokens


def build_anchor_automaton(term_entries: list[dict]) -> dict:
    """여러 용어 앵커를 본문 한 번의 순회로 찾는 상태 기계를 만든다."""
    transitions: list[dict[str, int]] = [{}]
    failures: list[int] = [0]
    outputs: list[list[int]] = [[]]
    for entry_index, entry in enumerate(term_entries):
        state = 0
        for character in entry["anchor"]:
            next_state = transitions[state].get(character)
            if next_state is None:
                next_state = len(transitions)
                transitions[state][character] = next_state
                transitions.append({})
                failures.append(0)
                outputs.append([])
            state = next_state
        outputs[state].append(entry_index)

    pending_states = deque(transitions[0].values())
    while pending_states:
        state = pending_states.popleft()
        for character, next_state in transitions[state].items():
            pending_states.append(next_state)
            failure_state = failures[state]
            while (
                failure_state
                and character not in transitions[failure_state]
            ):
                failure_state = failures[failure_state]
            failures[next_state] = transitions[failure_state].get(character, 0)
            outputs[next_state].extend(outputs[failures[next_state]])
    return {
        "transitions": transitions,
        "failures": failures,
        "outputs": outputs,
    }


def find_anchor_entry_indexes(text: str, automaton: dict) -> set[int]:
    """정규화 본문에 실제 등장한 앵커의 용어 인덱스를 반환한다."""
    transitions = automaton["transitions"]
    failures = automaton["failures"]
    outputs = automaton["outputs"]
    state = 0
    matched_indexes: set[int] = set()
    for character in text:
        while state and character not in transitions[state]:
            state = failures[state]
        state = transitions[state].get(character, 0)
        matched_indexes.update(outputs[state])
    return matched_indexes


def score_mention_windows(
    normalized_text: str,
    tokens: list[str],
    window_characters: int,
) -> dict:
    """앵커 토큰 주변 윈도우의 토큰 커버리지 최고점과 위치를 구한다."""
    anchor = max(tokens, key=len)
    best_coverage = 0.0
    best_span = (0, 0)
    mention_count = 0
    for anchor_match in re.finditer(re.escape(anchor), normalized_text):
        mention_count += 1
        window_start = max(0, anchor_match.start() - window_characters)
        window_end = anchor_match.end() + window_characters
        segment = normalized_text[window_start:window_end]
        coverage = sum(1 for token in tokens if token in segment) / len(tokens)
        if coverage > best_coverage:
            best_coverage = coverage
            best_span = (window_start, min(window_end, len(normalized_text)))
    return {
        "token_coverage": best_coverage,
        "anchor_mention_count": mention_count,
        "window_span": best_span,
    }


def extract_source_snippet(field_text: str, window_span: tuple[int, int]) -> str:
    """정규화 좌표의 윈도우를 원본 문자열 스니펫으로 되돌린다."""
    _, source_positions = build_normalized_view(field_text)
    if not source_positions:
        return ""
    start_index, end_index = window_span
    start_index = min(start_index, len(source_positions) - 1)
    end_index = min(max(end_index - 1, start_index), len(source_positions) - 1)
    source = unicodedata.normalize("NFC", str(field_text))
    snippet = source[source_positions[start_index] : source_positions[end_index] + 1]
    return re.sub(r"\s+", " ", snippet).strip()


def build_article_payload(row: dict, source_release: str) -> dict:
    """scan_definitions와 같은 구조의 AKS 후보 payload를 만든다."""
    eid = (row.get("eid") or "").strip()
    headword = (row.get("headword") or "").strip()
    definition = (row.get("definition") or "").strip()
    aliases = row.get("articleAliases") or []
    if not isinstance(aliases, list):
        aliases = []
    alias_names: list[str] = []
    for alias in aliases:
        if isinstance(alias, str) and alias.strip():
            alias_names.append(alias.strip())
        elif isinstance(alias, dict):
            alias_word = str(alias.get("word") or "").strip()
            if alias_word:
                alias_names.append(alias_word)
    return {
        "source": "AKS",
        "source_id": eid,
        "source_release": source_release,
        "source_record_id": f"AKS:ARTICLE:{eid}:{source_release}",
        "eid": eid,
        "headword": headword,
        "aliases": alias_names,
        "origin": str(row.get("origin") or "").strip(),
        "headword_origin": str(row.get("headwordOrigin") or "").strip(),
        "primary_type": (row.get("primaryType") or "").strip(),
        "primary_type_part": get_primary_type_part(row),
        "era": (row.get("era") or "").strip(),
        "definition": definition,
        "context": definition,
        "source_url": str(row.get("url") or "").strip(),
    }


def scan_article_for_terms(
    row: dict,
    term_entries: list[dict],
    anchor_automaton: dict,
    scan_policy: dict,
    source_release: str,
    policy_version: str,
    accumulator: dict[tuple[str, str], list[dict]],
) -> None:
    """문서 하나의 검색 필드에서 모든 용어의 언급 윈도우를 평가한다."""
    field_texts: dict[str, str] = {}
    normalized_fields: dict[str, str] = {}
    for field_name in scan_policy["search_fields"]:
        field_text = str(row.get(field_name) or "").strip()
        if field_text:
            field_texts[field_name] = field_text
            normalized_fields[field_name] = normalize_search_text(field_text)
    if not normalized_fields:
        return

    fields_by_entry_index: dict[int, list[str]] = {}
    for field_name, normalized_text in normalized_fields.items():
        for entry_index in find_anchor_entry_indexes(
            normalized_text,
            anchor_automaton,
        ):
            fields_by_entry_index.setdefault(entry_index, []).append(field_name)

    payload: dict | None = None
    for entry_index, matched_fields in fields_by_entry_index.items():
        entry = term_entries[entry_index]
        best_result: dict | None = None
        best_field = ""
        for field_name in matched_fields:
            normalized_text = normalized_fields[field_name]
            result = score_mention_windows(
                normalized_text,
                entry["tokens"],
                scan_policy["window_characters"],
            )
            if best_result is None or (
                result["token_coverage"] > best_result["token_coverage"]
            ):
                best_result = result
                best_field = field_name
        if best_result is None:
            continue
        if best_result["token_coverage"] < scan_policy["minimum_token_coverage"]:
            continue
        if payload is None:
            payload = build_article_payload(row, source_release)
        candidate = dict(payload)
        candidate["matched_name"] = entry["canonical_term"]
        candidate["matched_field"] = best_field
        candidate["token_coverage"] = round(best_result["token_coverage"], 4)
        candidate["anchor_mention_count"] = best_result["anchor_mention_count"]
        candidate["snippet"] = extract_source_snippet(
            field_texts[best_field],
            best_result["window_span"],
        )
        candidate["context"] = candidate["snippet"]
        candidate["retrieval_method"] = "body_mention"
        candidate["retrieval_methods"] = ["body_mention"]
        candidate["retrieval_score"] = candidate["token_coverage"]
        candidate["score_components"] = {
            "token_coverage": candidate["token_coverage"],
            "anchor_mention_count": candidate["anchor_mention_count"],
        }
        candidate["verification_status"] = "PROPOSED"
        candidate["retrieval_policy_version"] = policy_version
        accumulator[(entry["canonical_term"], entry["category"])].append(
            candidate
        )


def scan_body_mentions(
    match_json: str,
    encyclopedia_jsonl: str,
    policy: dict,
) -> list[dict]:
    """
    AKS 정확 이름 후보가 없는 용어를 definition·body의 근접 윈도우
    토큰 커버리지로 검색한다. 결과는 항상 PROPOSED 후보로 반환한다.
    """
    scan_policy = policy["body_mention_scan"]
    with open(match_json, "r", encoding="utf-8") as match_file:
        match_results = load(match_file)
    enrichment_terms = collect_aks_enrichment_terms(
        match_results,
        policy["candidate_retrieval"],
    )
    print(f"본문 언급 검색 대상(AKS 정확 이름 미발견): {len(enrichment_terms)}개")
    if not enrichment_terms:
        return []

    term_entries: list[dict] = []
    for item in enrichment_terms:
        tokens = split_term_tokens(
            item["canonical_term"],
            scan_policy["minimum_token_length"],
        )
        if not tokens:
            continue
        term_entries.append(
            {
                "canonical_term": item["canonical_term"],
                "category": item["category"],
                "tokens": tokens,
                "anchor": max(tokens, key=len),
            }
        )

    source_release = calculate_source_release(
        encyclopedia_jsonl,
        policy["source_release"],
    )
    accumulator: dict[tuple[str, str], list[dict]] = {
        (entry["canonical_term"], entry["category"]): []
        for entry in term_entries
    }
    anchor_automaton = build_anchor_automaton(term_entries)
    article_count = 0
    for row in iter_encyclopedia_rows(encyclopedia_jsonl):
        article_count += 1
        scan_article_for_terms(
            row,
            term_entries,
            anchor_automaton,
            scan_policy,
            source_release,
            policy["policy_version"],
            accumulator,
        )
    print(f"백과사전 문서 스캔 완료: {article_count}개")

    max_candidates = scan_policy["max_candidates"]
    results: list[dict] = []
    for entry in term_entries:
        candidates = accumulator[
            (entry["canonical_term"], entry["category"])
        ]
        for candidate in candidates:
            candidate["category_mismatch"] = not is_category_compatible(
                entry["category"],
                candidate["primary_type_part"],
                policy["category_compatibility"],
            )
        if not scan_policy["include_category_mismatch"]:
            candidates = [
                candidate
                for candidate in candidates
                if not candidate["category_mismatch"]
            ]
        candidates.sort(
            key=lambda candidate: (
                candidate["category_mismatch"],
                -candidate["token_coverage"],
                -candidate["anchor_mention_count"],
            )
        )
        results.append(
            {
                "canonical_term": entry["canonical_term"],
                "category": entry["category"],
                "body_mention_hit_count": len(candidates),
                "candidates": candidates[:max_candidates],
                "resolution_policy_version": policy["policy_version"],
                "normalization_policy_version": policy[
                    "normalization_policy_version"
                ],
            }
        )
    return results


def print_mention_report(results: list[dict], display_limit: int = 20) -> None:
    """본문 언급 후보 생성 통계를 출력한다."""
    total = len(results)
    found = [item for item in results if item["body_mention_hit_count"] > 0]
    single = [item for item in found if item["body_mention_hit_count"] == 1]
    all_mismatch = [
        item
        for item in found
        if item["candidates"]
        and all(
            candidate["category_mismatch"] for candidate in item["candidates"]
        )
    ]

    print(f"본문 언급 검색 대상: {total}개")
    if total == 0:
        return
    print(
        f"본문 언급 후보 발견: {len(found)}개 "
        f"({len(found) / total * 100:.1f}%)"
    )
    print(f"  후보 1건: {len(single)}개")
    print(f"  후보 전부 category-유형 불일치: {len(all_mismatch)}개")
    print(f"미발견: {total - len(found)}개")

    single_names = [item["canonical_term"] for item in single]
    if single_names:
        print(f"후보 1건 예시: {single_names[:display_limit]}")


def resolve_default_paths(policy: dict) -> dict[str, str]:
    """정책의 출력 레이아웃으로 기본 입·출력 경로를 구성한다."""
    neo4j_root = Path(__file__).resolve().parent.parent
    project_root = Path(__file__).resolve().parents[4]
    layout = policy["output_layout"]
    output_root = neo4j_root / layout["default_output_root"]
    retrieval_directory = (
        output_root
        / layout["directories"]["internal"]
        / layout["directories"]["candidate_retrieval"]
    )
    encyclopedia_path = (
        project_root
        / "etl"
        / "raw_data"
        / "한국민족문화대백과사전"
        / "articles_detail.jsonl"
    )
    return {
        "match_json": str(
            retrieval_directory / layout["files"]["name_matches"]
        ),
        "encyclopedia_jsonl": str(encyclopedia_path),
        "output": str(
            retrieval_directory / layout["files"]["body_mention_matches"]
        ),
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = ArgumentParser(
        description=(
            "AKS 정확 이름 후보가 없는 용어를 definition·body의 "
            "근접 윈도우 토큰 커버리지로 검색"
        )
    )
    parser.add_argument(
        "--match-json",
        default="",
        help="name_match_candidates.json 경로",
    )
    parser.add_argument(
        "--encyclopedia-jsonl",
        default="",
        help="백과사전 articles_detail.jsonl 경로",
    )
    parser.add_argument(
        "--output",
        default="",
        help="검색 결과 JSON 저장 경로",
    )
    parser.add_argument(
        "--display-limit",
        type=int,
        default=20,
        help="보고서에 표시할 용어 예시 개수",
    )
    parser.add_argument(
        "--policy",
        default=str(
            Path(__file__).resolve().parent.parent
            / "config"
            / "resolution_policy.json"
        ),
        help="정규화·후보 생성 정책 JSON 경로",
    )
    cli_args = parser.parse_args()
    pipeline_policy = load_pipeline_policy(cli_args.policy)
    default_paths = resolve_default_paths(pipeline_policy)

    scan_results = scan_body_mentions(
        match_json=cli_args.match_json or default_paths["match_json"],
        encyclopedia_jsonl=(
            cli_args.encyclopedia_jsonl or default_paths["encyclopedia_jsonl"]
        ),
        policy=pipeline_policy,
    )
    print_mention_report(scan_results, cli_args.display_limit)

    output_path = Path(cli_args.output or default_paths["output"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        dump(scan_results, output_file, ensure_ascii=False, indent=2)
    print(f"저장 완료: {output_path}")

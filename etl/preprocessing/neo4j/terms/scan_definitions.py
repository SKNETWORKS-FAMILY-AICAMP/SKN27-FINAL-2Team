import re
import sys
from argparse import ArgumentParser
from json import dump, load
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from match_names import get_primary_type_part, is_category_compatible
from prep_thesaurus import build_match_key, iter_encyclopedia_rows


def collect_unmatched_terms(match_results: list[dict]) -> list[dict]:
    """
    이름 매칭 결과에서 어느 소스에도 매칭되지 않은 용어만 골라낸다.
    노이즈 플래그가 붙은 용어는 스캔 대상에서 제외한다.
    """
    unmatched: list[dict] = []
    for item in match_results:
        if item.get("is_noise"):
            continue
        if item.get("encyclopedia") or item.get("itkc_people") or item.get("itkc_events"):
            continue
        unmatched.append(
            {"canonical_term": item["canonical_term"], "category": item["category"]}
        )
    return unmatched


def build_scan_candidate(term: str, category: str, row: dict, definition: str) -> dict:
    """스캔에서 발견한 문서를 의심 플래그와 함께 후보 레코드로 만든다."""
    context, suspect = find_surface_context(term, definition)
    primary_type_part = get_primary_type_part(row)
    return {
        "eid": (row.get("eid") or "").strip(),
        "headword": (row.get("headword") or "").strip(),
        "primary_type": (row.get("primaryType") or "").strip(),
        "definition": definition,
        "context": context,
        "suspect_place_suffix": suspect,
        "category_mismatch": not is_category_compatible(category, primary_type_part),
    }


def build_term_pattern(terms: list[dict], min_key_length: int) -> tuple[dict[str, str], re.Pattern | None]:
    """
    용어들의 정규화 키를 하나의 정규식으로 묶는다.
    너무 짧은 키는 오탐이 많아 제외한다.
    반환: ({키: 용어}, 컴파일된 패턴). 유효한 키가 없으면 패턴은 None.
    """
    key_to_term: dict[str, str] = {}
    for item in terms:
        key = build_match_key(item["canonical_term"])
        if len(key) >= min_key_length:
            key_to_term.setdefault(key, item["canonical_term"])

    if not key_to_term:
        return key_to_term, None

    sorted_keys = sorted(key_to_term, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(key) for key in sorted_keys))
    return key_to_term, pattern


def find_surface_context(term: str, definition: str, window: int = 15) -> tuple[str, bool]:
    """
    definition 원문에서 용어의 표면형을 찾아 (주변 문맥, 지명 접미 의심 여부)를 돌려준다.
    - 문맥: 매칭 지점 앞뒤 window 글자 (판별 단계에서 LLM 근거로 사용)
    - 의심: 용어 바로 뒤에 행정구역 접미(면·리·군·읍·동·촌)가 붙으면 True
      ('광혜원'이 '진천군 광혜원면'의 일부에 걸린 경우 등)
    표면형을 못 찾으면 (definition 앞부분, False)를 돌려준다.
    """
    compact = term.replace(" ", "")
    pattern = re.compile(r"\s*".join(re.escape(char) for char in compact))
    match = pattern.search(definition)
    if match is None:
        return definition[: window * 2], False

    start, end = match.span()
    context = definition[max(0, start - window):end + window]
    place_suffixes = "면리군읍동촌"
    suspect = end < len(definition) and definition[end] in place_suffixes
    return context, suspect


def scan_definitions(
    match_json: str,
    encyclopedia_jsonl: str,
    min_key_length: int = 3,
    max_candidates: int = 10,
) -> list[dict]:
    """
    이름 매칭에 실패한 용어를 백과사전 definition 문장에서 찾는다.
    definition은 1문장이라 body 전체 검색보다 오탐이 훨씬 적다.
    후보를 확정하지 않고 전부 기록한다. 판별은 다음 단계에서 수행한다.
    """
    with open(match_json, "r", encoding="utf-8") as input_file:
        match_results = load(input_file)

    unmatched = collect_unmatched_terms(match_results)
    print(f"이름 매칭 실패 용어: {len(unmatched)}개 (definition 스캔 대상)")

    term_categories = {
        item["canonical_term"]: item["category"] for item in unmatched
    }
    key_to_term, pattern = build_term_pattern(unmatched, min_key_length)
    skipped = len(unmatched) - len(key_to_term)
    if skipped > 0:
        print(f"키 {min_key_length}글자 미만이라 스캔에서 제외: {skipped}개")
    if pattern is None:
        print("스캔할 용어가 없습니다.")
        return []

    candidates_by_term: dict[str, list[dict]] = {}
    hit_counts: dict[str, int] = {}
    scanned = 0
    for row in iter_encyclopedia_rows(encyclopedia_jsonl):
        scanned += 1
        eid = (row.get("eid") or "").strip()
        headword = (row.get("headword") or "").strip()
        definition = (row.get("definition") or "").strip()
        if not eid or not headword or not definition:
            continue

        normalized_definition = build_match_key(definition)
        for key in set(pattern.findall(normalized_definition)):
            term = key_to_term[key]
            hit_counts[term] = hit_counts.get(term, 0) + 1
            bucket = candidates_by_term.setdefault(term, [])
            if len(bucket) < max_candidates * 3:
                category = term_categories.get(term, "")
                bucket.append(build_scan_candidate(term, category, row, definition))
    print(f"백과사전 문서 {scanned}건 스캔 완료")

    results: list[dict] = []
    for item in unmatched:
        term = item["canonical_term"]
        # 의심 플래그(지명 접미, category 불일치)가 붙은 후보를 뒤로 보내고 상한까지만 남긴다
        bucket = sorted(
            candidates_by_term.get(term, []),
            key=lambda candidate: (
                candidate["suspect_place_suffix"],
                candidate["category_mismatch"],
            ),
        )[:max_candidates]
        results.append(
            {
                "canonical_term": term,
                "category": item["category"],
                "definition_hit_count": hit_counts.get(term, 0),
                "candidates": bucket,
            }
        )
    return results


def print_scan_report(results: list[dict], display_limit: int = 20) -> None:
    """definition 스캔 결과 통계를 출력한다."""
    total = len(results)
    found = [item for item in results if item["definition_hit_count"] > 0]
    single = [item for item in found if item["definition_hit_count"] == 1]
    many = [item for item in found if item["definition_hit_count"] > 5]

    all_suspect = [
        item
        for item in found
        if item["candidates"]
        and all(
            candidate["suspect_place_suffix"] or candidate["category_mismatch"]
            for candidate in item["candidates"]
        )
    ]

    print(f"스캔 대상 용어: {total}개")
    if total == 0:
        return
    print(f"definition에서 발견: {len(found)}개 ({len(found) / total * 100:.1f}%)")
    print(f"  후보 1건 (바로 확정 후보): {len(single)}개")
    print(f"  후보 6건 이상 (판별 필요): {len(many)}개")
    print(f"  후보 전부 의심 플래그 (지명 접미·category 불일치, 오탐 가능성 높음): {len(all_suspect)}개")
    print(f"미발견: {total - len(found)}개")

    single_names = [item["canonical_term"] for item in single]
    if single_names:
        print(f"후보 1건 예시: {single_names[:display_limit]}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = ArgumentParser(
        description="이름 매칭 실패 용어를 백과사전 definition에서 스캔"
    )
    parser.add_argument(
        "match_json",
        nargs="?",
        default="term_name_matches.json",
        help="match_names.py 결과 JSON 경로",
    )
    parser.add_argument(
        "--encyclopedia-jsonl", default="", help="백과사전 articles_detail.jsonl 경로"
    )
    parser.add_argument(
        "--output",
        default="definition_scan_matches.json",
        help="스캔 결과 JSON 저장 경로",
    )
    parser.add_argument(
        "--min-key-length", type=int, default=3, help="스캔할 용어 키의 최소 길이"
    )
    parser.add_argument(
        "--max-candidates", type=int, default=10, help="용어당 기록할 최대 후보 수"
    )
    cli_args = parser.parse_args()

    encyclopedia_path = cli_args.encyclopedia_jsonl
    if not encyclopedia_path:
        project_root = Path(__file__).resolve().parents[4]
        encyclopedia_path = str(
            project_root / "etl" / "raw_data" / "한국민족문화대백과사전" / "articles_detail.jsonl"
        )

    scan_results = scan_definitions(
        match_json=cli_args.match_json,
        encyclopedia_jsonl=encyclopedia_path,
        min_key_length=cli_args.min_key_length,
        max_candidates=cli_args.max_candidates,
    )
    print_scan_report(scan_results)

    with open(cli_args.output, "w", encoding="utf-8") as output_file:
        dump(scan_results, output_file, ensure_ascii=False, indent=2)
    print(f"저장 완료: {cli_args.output}")

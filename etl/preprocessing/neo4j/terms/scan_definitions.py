import sys
from argparse import ArgumentParser
from json import dump, load
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import calculate_source_release, load_pipeline_policy
from candidate_retrieval import build_search_index, retrieve_candidates
from match_names import get_primary_type_part, is_category_compatible
from prep_thesaurus import iter_encyclopedia_rows


def collect_aks_enrichment_terms(
    match_results: list[dict],
    retrieval_policy: dict,
) -> list[dict]:
    """AKS 정확 이름 후보가 없어 definition·body 보강이 필요한 용어를 고른다."""
    skip_methods = set(
        retrieval_policy["enrichment_skip_retrieval_methods"]
    )
    enrichment_terms: list[dict] = []
    for item in match_results:
        if item.get("is_noise"):
            continue
        has_strong_aks_candidate = False
        for candidate in item.get("encyclopedia", []):
            candidate_methods = set(candidate.get("retrieval_methods", []))
            primary_method = str(candidate.get("retrieval_method") or "")
            if primary_method:
                candidate_methods.add(primary_method)
            if candidate_methods.intersection(skip_methods):
                has_strong_aks_candidate = True
                break
        if has_strong_aks_candidate:
            continue
        enrichment_terms.append(
            {
                "canonical_term": item["canonical_term"],
                "category": item["category"],
            }
        )
    return enrichment_terms


def build_definition_index(
    encyclopedia_jsonl: str,
    retrieval_policy: dict,
    source_release: str,
) -> dict:
    """AKS definition을 검색 본문으로 사용한 후보 인덱스를 만든다."""
    records: list[dict] = []
    for row in iter_encyclopedia_rows(encyclopedia_jsonl):
        eid = (row.get("eid") or "").strip()
        headword = (row.get("headword") or "").strip()
        definition = (row.get("definition") or "").strip()
        if not eid or not headword or not definition:
            continue

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

        records.append(
            {
                "search_names": [headword] + alias_names,
                "search_text": definition,
                "payload": {
                    "source": "AKS",
                    "source_id": eid,
                    "source_release": source_release,
                    "source_record_id": (
                        f"AKS:ARTICLE:{eid}:{source_release}"
                    ),
                    "eid": eid,
                    "headword": headword,
                    "aliases": alias_names,
                    "origin": str(row.get("origin") or "").strip(),
                    "headword_origin": str(
                        row.get("headwordOrigin") or ""
                    ).strip(),
                    "primary_type": (row.get("primaryType") or "").strip(),
                    "primary_type_part": get_primary_type_part(row),
                    "era": (row.get("era") or "").strip(),
                    "definition": definition,
                    "context": definition,
                    "source_url": str(row.get("url") or "").strip(),
                },
            }
        )
    return build_search_index(records, retrieval_policy)


def scan_definitions(
    match_json: str,
    encyclopedia_jsonl: str,
    policy: dict,
    max_candidates: int | None = None,
) -> list[dict]:
    """
    AKS 정확 이름 후보가 없는 용어를 definition 문자 유사도로 검색한다.
    결과는 확정하지 않고 항상 PROPOSED 후보로 반환한다.
    """
    with open(match_json, "r", encoding="utf-8") as input_file:
        match_results = load(input_file)

    enrichment_terms = collect_aks_enrichment_terms(
        match_results,
        policy["candidate_retrieval"],
    )
    print(
        "AKS 정확 이름 후보 미발견 용어: "
        f"{len(enrichment_terms)}개 (definition 검색 대상)"
    )
    if not enrichment_terms:
        return []

    source_release = calculate_source_release(
        encyclopedia_jsonl,
        policy["source_release"],
    )
    definition_index = build_definition_index(
        encyclopedia_jsonl,
        policy["candidate_retrieval"],
        source_release,
    )
    print(f"AKS definition 인덱스: {len(definition_index['entries'])}개")

    candidate_limit = max_candidates
    if candidate_limit is None:
        candidate_limit = policy["definition_scan"]["max_candidates"]

    results: list[dict] = []
    for item in enrichment_terms:
        term = item["canonical_term"]
        category = item["category"]
        candidates = retrieve_candidates(
            term=term,
            search_index=definition_index,
            retrieval_policy=policy["candidate_retrieval"],
            policy_version=policy["policy_version"],
            max_candidates=candidate_limit,
        )
        definition_candidates: list[dict] = []
        for candidate in candidates:
            description_score = candidate["score_components"][
                "description_ngram_coverage"
            ]
            if description_score <= 0.0:
                continue
            candidate["category_mismatch"] = not is_category_compatible(
                category,
                candidate["primary_type_part"],
                policy["category_compatibility"],
            )
            if (
                candidate["category_mismatch"]
                and not policy["definition_scan"][
                    "include_category_mismatch"
                ]
            ):
                continue
            definition_candidates.append(candidate)

        definition_candidates.sort(
            key=lambda candidate: (
                candidate["category_mismatch"],
                -candidate["retrieval_score"],
            )
        )
        results.append(
            {
                "canonical_term": term,
                "category": category,
                "definition_hit_count": len(definition_candidates),
                "candidates": definition_candidates[:candidate_limit],
                "resolution_policy_version": policy["policy_version"],
                "normalization_policy_version": policy[
                    "normalization_policy_version"
                ],
            }
        )
    return results


def print_scan_report(results: list[dict], display_limit: int = 20) -> None:
    """definition 후보 생성 통계를 출력한다."""
    total = len(results)
    found = [item for item in results if item["definition_hit_count"] > 0]
    single = [item for item in found if item["definition_hit_count"] == 1]
    many = [item for item in found if item["definition_hit_count"] > 5]
    all_mismatch = [
        item
        for item in found
        if item["candidates"]
        and all(
            candidate["category_mismatch"]
            for candidate in item["candidates"]
        )
    ]

    print(f"definition 검색 대상: {total}개")
    if total == 0:
        return
    print(f"definition 후보 발견: {len(found)}개 ({len(found) / total * 100:.1f}%)")
    print(f"  후보 1건: {len(single)}개")
    print(f"  후보 6건 이상: {len(many)}개")
    print(f"  후보 전부 category-유형 불일치: {len(all_mismatch)}개")
    print(f"미발견: {total - len(found)}개")

    single_names = [item["canonical_term"] for item in single]
    if single_names:
        print(f"후보 1건 예시: {single_names[:display_limit]}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = ArgumentParser(
        description="이름 후보가 없는 용어를 AKS definition에서 검색"
    )
    parser.add_argument(
        "match_json",
        nargs="?",
        default="term_name_matches.json",
        help="match_names.py 결과 JSON 경로",
    )
    parser.add_argument(
        "--encyclopedia-jsonl",
        default="",
        help="백과사전 articles_detail.jsonl 경로",
    )
    parser.add_argument(
        "--output",
        default="definition_scan_matches.json",
        help="검색 결과 JSON 저장 경로",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="용어당 최대 후보 수(0이면 정책값)",
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

    encyclopedia_path = cli_args.encyclopedia_jsonl
    if not encyclopedia_path:
        project_root = Path(__file__).resolve().parents[4]
        encyclopedia_path = str(
            project_root
            / "etl"
            / "raw_data"
            / "한국민족문화대백과사전"
            / "articles_detail.jsonl"
        )

    scan_results = scan_definitions(
        match_json=cli_args.match_json,
        encyclopedia_jsonl=encyclopedia_path,
        policy=pipeline_policy,
        max_candidates=cli_args.max_candidates or None,
    )
    print_scan_report(scan_results)

    with open(cli_args.output, "w", encoding="utf-8") as output_file:
        dump(scan_results, output_file, ensure_ascii=False, indent=2)
    print(f"저장 완료: {cli_args.output}")

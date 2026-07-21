import re
import sys
from ast import literal_eval
from argparse import ArgumentParser
from json import JSONDecodeError, dump, loads
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import calculate_source_release, load_pipeline_policy
from candidate_retrieval import build_search_index, retrieve_candidates
from prep_thesaurus import (
    build_match_key,
    is_noise_term,
    iter_encyclopedia_rows,
)


def parse_problem_ids(raw_value: object) -> list[str]:
    """신규 JSON 배열과 기존 CSV의 파이썬 리스트 문자열을 모두 읽는다."""
    if isinstance(raw_value, list):
        return [str(problem_id) for problem_id in raw_value]
    if raw_value is None or pd.isna(raw_value):
        return []

    serialized = str(raw_value).strip()
    if not serialized:
        return []
    try:
        parsed = loads(serialized)
    except JSONDecodeError:
        parsed = literal_eval(serialized)
    if not isinstance(parsed, list):
        raise ValueError("problem_ids는 배열이어야 합니다.")
    return [str(problem_id) for problem_id in parsed]


def split_itkc_name(raw_name: str) -> tuple[str, str]:
    """'가일(可逸)' 형태의 ITKC 이름을 (한글, 한자)로 분리한다."""
    stripped = str(raw_name).strip()
    match = re.fullmatch(r"([^(（]+)[（(](.+)[)）]\s*", stripped)
    if match is None:
        return stripped, ""
    return match.group(1).strip(), match.group(2).strip()


def get_primary_type_part(row: dict) -> str:
    """백과사전 레코드에서 primaryType 대분류를 얻는다."""
    part = (row.get("primaryTypePartA") or "").strip()
    if part:
        return part
    primary = (row.get("primaryType") or "").strip()
    return primary.split("/")[0]


def is_category_compatible(
    category: str,
    primary_type_part: str,
    compatibility_policy: dict,
) -> bool:
    """외부 정책의 category-원천 유형 대응표로 호환성을 판정한다."""
    if category not in compatibility_policy:
        return True
    if not primary_type_part:
        return True
    return primary_type_part in compatibility_policy[category]


def build_encyclopedia_index(
    jsonl_path: str,
    retrieval_policy: dict,
    source_release: str,
) -> dict:
    """백과사전 표제어와 문자열·객체형 이칭을 이름 검색 인덱스로 만든다."""
    records: list[dict] = []
    for row in iter_encyclopedia_rows(jsonl_path):
        eid = (row.get("eid") or "").strip()
        headword = (row.get("headword") or "").strip()
        if not eid or not headword:
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
                "search_text": "",
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
                    "definition": (row.get("definition") or "").strip(),
                    "source_url": str(row.get("url") or "").strip(),
                },
            }
        )
    return build_search_index(records, retrieval_policy)


def build_thesaurus_index(
    csv_path: str,
    retrieval_policy: dict,
    source_release: str,
) -> dict:
    """시소러스 표제어·한자·설명을 함께 검색하는 인덱스를 만든다."""
    thesaurus_df = pd.read_csv(csv_path, dtype=str).fillna("")
    required_columns = {
        "term_id",
        "term_name",
        "term_ch",
        "term_times",
        "term_lk",
        "term_desc",
    }
    missing_columns = required_columns.difference(thesaurus_df.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(f"시소러스 후보 생성에 필요한 컬럼이 없습니다: {missing_text}")

    records: list[dict] = []
    for row in thesaurus_df.to_dict("records"):
        term_id = row["term_id"].strip()
        term_name = row["term_name"].strip()
        if not term_id or not term_name:
            continue
        hanja = row["term_ch"].strip()
        search_names = [term_name]
        if hanja:
            search_names.append(hanja)
        records.append(
            {
                "search_names": search_names,
                "search_text": row["term_desc"].strip(),
                "payload": {
                    "source": "THESAURUS",
                    "source_id": term_id,
                    "source_release": source_release,
                    "source_record_id": (
                        f"THESAURUS:TERM:{term_id}:{source_release}"
                    ),
                    "term_id": term_id,
                    "term_name": term_name,
                    "hanja": hanja,
                    "era": row["term_times"].strip(),
                    "thesaurus_category": row["term_lk"].strip(),
                    "description": row["term_desc"].strip(),
                    "term_year": str(row.get("term_year") or "").strip(),
                    "term_remark": str(row.get("term_remark") or "").strip(),
                    "term_attr": str(row.get("term_attr") or "").strip(),
                },
            }
        )
    return build_search_index(records, retrieval_policy)


def build_itkc_people_index(
    csv_path: str,
    policy_version: str,
    source_release: str,
) -> dict[str, list[dict]]:
    """ITKC 인물 이름의 정확 일치 인덱스를 만든다."""
    people_df = pd.read_csv(csv_path, dtype=str).fillna("")
    index: dict[str, list[dict]] = {}
    for record in people_df.to_dict("records"):
        hangul, hanja = split_itkc_name(record["name"])
        key = build_match_key(hangul)
        if not key:
            continue
        person_id = record["person_id"]
        index.setdefault(key, []).append(
            {
                "source": "ITKC_PERSON",
                "source_id": person_id,
                "source_release": source_release,
                "source_record_id": (
                    f"ITKC:PERSON:{person_id}:{source_release}"
                ),
                "person_id": person_id,
                "name": hangul,
                "hanja": hanja,
                "birth_year": record["birth_year"],
                "death_year": record["death_year"],
                "bonkwan": record["bonkwan"],
                "ja": str(record.get("ja") or "").strip(),
                "ho": str(record.get("ho") or "").strip(),
                "father": str(record.get("father") or "").strip(),
                "source_url": str(record.get("detail_url") or "").strip(),
                "matched_name": hangul,
                "matched_field": "name",
                "retrieval_method": "exact",
                "retrieval_methods": ["exact"],
                "retrieval_score": 1.0,
                "verification_status": "PROPOSED",
                "retrieval_policy_version": policy_version,
            }
        )
    return index


def build_itkc_event_index(
    csv_path: str,
    retrieval_policy: dict,
    source_release: str,
) -> dict:
    """ITKC 사건명을 범용 이름 검색 인덱스로 만든다."""
    events_df = pd.read_csv(csv_path, dtype=str).fillna("")
    records: list[dict] = []
    for record in events_df.to_dict("records"):
        event_id = record["event_id"].strip()
        event_name = record["event_name"].strip()
        if not event_id or not event_name:
            continue
        records.append(
            {
                "search_names": [event_name],
                "search_text": "",
                "payload": {
                    "source": "ITKC_EVENT",
                    "source_id": event_id,
                    "source_release": source_release,
                    "source_record_id": (
                        f"ITKC:EVENT:{event_id}:{source_release}"
                    ),
                    "event_id": event_id,
                    "event_name": event_name,
                    "subject_category": record["subject_category"].strip(),
                    "period": record["period"].strip(),
                    "event_date": str(
                        record.get("event_date") or ""
                    ).strip(),
                    "related_event": str(
                        record.get("related_event") or ""
                    ).strip(),
                    "source_url": str(
                        record.get("detail_url") or ""
                    ).strip(),
                },
            }
        )
    return build_search_index(records, retrieval_policy)


def lookup_with_policy(
    terms: list[str],
    search_index: dict,
    policy: dict,
    max_candidates: int | None,
) -> dict[str, dict]:
    """모든 용어에 동일한 외부 정책을 적용해 원천 후보를 생성한다."""
    results: dict[str, dict] = {}
    for term in terms:
        candidates = retrieve_candidates(
            term=term,
            search_index=search_index,
            retrieval_policy=policy["candidate_retrieval"],
            policy_version=policy["policy_version"],
            max_candidates=max_candidates,
        )
        if candidates:
            results[term] = {
                "via": candidates[0]["retrieval_method"],
                "candidates": candidates,
            }
    return results


def match_names(
    terms_csv: str,
    thesaurus_csv: str,
    encyclopedia_jsonl: str,
    itkc_people_csv: str,
    itkc_events_csv: str,
    policy: dict,
    max_candidates: int | None = None,
) -> list[dict]:
    """
    추출 용어를 AKS·시소러스·ITKC와 고재현율 후보 검색한다.
    후보는 항상 PROPOSED이며 이 단계에서 CanonicalEntity 병합을 확정하지 않는다.
    """
    term_df = pd.read_csv(terms_csv, encoding="utf-8-sig")
    term_records = term_df.to_dict("records")
    term_keys = {
        record["canonical_term"]: build_match_key(record["canonical_term"])
        for record in term_records
        if build_match_key(record["canonical_term"])
    }
    retrieval_policy = policy["candidate_retrieval"]
    release_policy = policy["source_release"]
    encyclopedia_release = calculate_source_release(
        encyclopedia_jsonl,
        release_policy,
    )
    thesaurus_release = calculate_source_release(thesaurus_csv, release_policy)
    people_release = calculate_source_release(itkc_people_csv, release_policy)
    event_release = calculate_source_release(itkc_events_csv, release_policy)

    print("백과사전 인덱스 구축 중...")
    encyclopedia_index = build_encyclopedia_index(
        encyclopedia_jsonl,
        retrieval_policy,
        encyclopedia_release,
    )
    print(f"  백과사전 레코드 {len(encyclopedia_index['entries'])}개")
    print("시소러스 인덱스 구축 중...")
    thesaurus_index = build_thesaurus_index(
        thesaurus_csv,
        retrieval_policy,
        thesaurus_release,
    )
    print(f"  시소러스 레코드 {len(thesaurus_index['entries'])}개")
    print("ITKC 인물 인덱스 구축 중...")
    people_index = build_itkc_people_index(
        itkc_people_csv,
        policy["policy_version"],
        people_release,
    )
    print(f"  인물 이름 키 {len(people_index)}개")
    print("ITKC 사건 인덱스 구축 중...")
    event_index = build_itkc_event_index(
        itkc_events_csv,
        retrieval_policy,
        event_release,
    )
    print(f"  사건 레코드 {len(event_index['entries'])}개")

    terms = list(term_keys)
    encyclopedia_results = lookup_with_policy(
        terms,
        encyclopedia_index,
        policy,
        max_candidates,
    )
    thesaurus_results = lookup_with_policy(
        terms,
        thesaurus_index,
        policy,
        max_candidates,
    )
    event_results = lookup_with_policy(
        terms,
        event_index,
        policy,
        max_candidates,
    )

    people_limit = max_candidates
    if people_limit is None:
        people_limit = retrieval_policy["max_candidates"]

    results: list[dict] = []
    for record in term_records:
        term = record["canonical_term"]
        category = record["category"]
        key = term_keys.get(term, "")
        encyclopedia = encyclopedia_results.get(term)
        thesaurus = thesaurus_results.get(term)
        event = event_results.get(term)
        people_candidates = people_index.get(key, [])[:people_limit]

        encyclopedia_candidates: list[dict] = []
        encyclopedia_via = None
        if encyclopedia:
            encyclopedia_via = encyclopedia["via"]
            encyclopedia_candidates = [
                {
                    **candidate,
                    "category_mismatch": not is_category_compatible(
                        category,
                        candidate["primary_type_part"],
                        policy["category_compatibility"],
                    ),
                }
                for candidate in encyclopedia["candidates"]
            ]
            encyclopedia_candidates.sort(
                key=lambda candidate: (
                    candidate["category_mismatch"],
                    -candidate["retrieval_score"],
                )
            )

        thesaurus_via = None
        thesaurus_candidates: list[dict] = []
        if thesaurus:
            thesaurus_via = thesaurus["via"]
            thesaurus_candidates = thesaurus["candidates"]

        event_via = None
        event_candidates: list[dict] = []
        if event:
            event_via = event["via"]
            event_candidates = event["candidates"]

        results.append(
            {
                "canonical_term": term,
                "category": category,
                "problem_count": int(record["count"]),
                "problem_ids": parse_problem_ids(record.get("problem_ids", "")),
                "extraction_model": str(record.get("extraction_model") or ""),
                "extraction_reasoning_effort": str(
                    record.get("extraction_reasoning_effort") or ""
                ),
                "extraction_policy_version": str(
                    record.get("extraction_policy_version") or ""
                ),
                "is_noise": bool(is_noise_term(term, policy["noise"])),
                "resolution_policy_version": policy["policy_version"],
                "normalization_policy_version": policy[
                    "normalization_policy_version"
                ],
                "encyclopedia_via": encyclopedia_via,
                "encyclopedia": encyclopedia_candidates,
                "thesaurus_via": thesaurus_via,
                "thesaurus": thesaurus_candidates,
                "itkc_people": people_candidates,
                "itkc_events_via": event_via,
                "itkc_events": event_candidates,
            }
        )
    return results


def print_match_report(results: list[dict], noise_display_limit: int = 30) -> None:
    """원천별 후보 생성 통계와 노이즈 의심 용어를 출력한다."""
    noise_items = [item for item in results if item["is_noise"]]
    counted = [item for item in results if not item["is_noise"]]
    total = len(counted)
    encyclopedia_hit = sum(1 for item in counted if item["encyclopedia"])
    encyclopedia_multi = sum(
        1 for item in counted if len(item["encyclopedia"]) > 1
    )
    thesaurus_hit = sum(1 for item in counted if item["thesaurus"])
    thesaurus_multi = sum(1 for item in counted if len(item["thesaurus"]) > 1)
    people_hit = sum(1 for item in counted if item["itkc_people"])
    people_multi = sum(1 for item in counted if len(item["itkc_people"]) > 1)
    event_hit = sum(1 for item in counted if item["itkc_events"])
    any_hit = sum(
        1
        for item in counted
        if item["encyclopedia"]
        or item["thesaurus"]
        or item["itkc_people"]
        or item["itkc_events"]
    )
    all_mismatch = sum(
        1
        for item in counted
        if item["encyclopedia"]
        and all(
            candidate["category_mismatch"]
            for candidate in item["encyclopedia"]
        )
    )

    print(f"전체 용어: {len(results)}개 (노이즈 의심 {len(noise_items)}개 제외)")
    print(
        f"백과사전 후보: {encyclopedia_hit}개 "
        f"(복수 후보 {encyclopedia_multi}개)"
    )
    print(f"  후보 전부 category-유형 불일치: {all_mismatch}개")
    print(f"시소러스 후보: {thesaurus_hit}개 (복수 후보 {thesaurus_multi}개)")
    print(f"ITKC 인물 후보: {people_hit}개 (복수 후보 {people_multi}개)")
    print(f"ITKC 사건 후보: {event_hit}개")
    if total > 0:
        print(f"하나 이상 후보: {any_hit}개 ({any_hit / total * 100:.1f}%)")
    print(f"전부 미매칭: {total - any_hit}개")

    if noise_items:
        noise_names = [item["canonical_term"] for item in noise_items]
        print("노이즈 의심 용어:")
        print(f"  {noise_names[:noise_display_limit]}")
        if len(noise_names) > noise_display_limit:
            print(f"  ... 외 {len(noise_names) - noise_display_limit}개")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = ArgumentParser(
        description="추출 용어를 AKS·시소러스·ITKC 원천과 후보 매칭"
    )
    parser.add_argument("terms_csv", nargs="?", default="", help="용어 집계 CSV 경로")
    parser.add_argument("--thesaurus-csv", default="", help="시소러스 CSV 경로")
    parser.add_argument(
        "--encyclopedia-jsonl",
        default="",
        help="백과사전 articles_detail.jsonl 경로",
    )
    parser.add_argument("--itkc-people", default="", help="itkc_people.csv 경로")
    parser.add_argument("--itkc-events", default="", help="itkc_events.csv 경로")
    parser.add_argument(
        "--output",
        default="term_name_matches.json",
        help="매칭 결과 JSON 저장 경로",
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

    project_root = Path(__file__).resolve().parents[4]
    raw_data_dir = project_root / "etl" / "raw_data"
    pipeline_policy = load_pipeline_policy(cli_args.policy)

    terms_csv_path = cli_args.terms_csv
    if not terms_csv_path:
        terms_csv_path = str(
            Path(__file__).resolve().parent.parent
            / "output"
            / "csv"
            / "exam_history_terms.csv"
        )

    thesaurus_path = cli_args.thesaurus_csv
    if not thesaurus_path:
        thesaurus_candidates = list(raw_data_dir.glob("*20211028*.csv"))
        if len(thesaurus_candidates) != 1:
            raise FileNotFoundError(
                "시소러스 CSV를 하나로 확정할 수 없습니다. 경로를 지정하세요."
            )
        thesaurus_path = str(thesaurus_candidates[0])

    encyclopedia_path = cli_args.encyclopedia_jsonl
    if not encyclopedia_path:
        encyclopedia_path = str(
            raw_data_dir
            / "한국민족문화대백과사전"
            / "articles_detail.jsonl"
        )

    itkc_directory = raw_data_dir / "한국고전종합DB_관계망"
    itkc_people_path = cli_args.itkc_people
    if not itkc_people_path:
        itkc_people_path = str(itkc_directory / "itkc_people.csv")
    itkc_events_path = cli_args.itkc_events
    if not itkc_events_path:
        itkc_events_path = str(itkc_directory / "itkc_events.csv")

    match_results = match_names(
        terms_csv=terms_csv_path,
        thesaurus_csv=thesaurus_path,
        encyclopedia_jsonl=encyclopedia_path,
        itkc_people_csv=itkc_people_path,
        itkc_events_csv=itkc_events_path,
        policy=pipeline_policy,
        max_candidates=cli_args.max_candidates or None,
    )
    print_match_report(match_results)

    with open(cli_args.output, "w", encoding="utf-8") as output_file:
        dump(match_results, output_file, ensure_ascii=False, indent=2)
    print(f"저장 완료: {cli_args.output}")

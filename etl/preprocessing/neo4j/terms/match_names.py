import re
import sys
from argparse import ArgumentParser
from json import dump
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from prep_thesaurus import (
    build_match_key,
    find_affix_matches,
    is_noise_term,
    iter_encyclopedia_rows,
)


def split_itkc_name(raw_name: str) -> tuple[str, str]:
    """'가일(可逸)' 형태의 ITKC 이름을 (한글, 한자)로 분리한다."""
    stripped = str(raw_name).strip()
    match = re.fullmatch(r"([^(（]+)[（(](.+)[)）]\s*", stripped)
    if match is None:
        return stripped, ""
    return match.group(1).strip(), match.group(2).strip()


def get_primary_type_part(row: dict) -> str:
    """백과사전 레코드에서 primaryType 대분류(인물, 유적, 지명 등)를 얻는다."""
    part = (row.get("primaryTypePartA") or "").strip()
    if part:
        return part
    primary = (row.get("primaryType") or "").strip()
    return primary.split("/")[0]


def is_category_compatible(category: str, primary_type_part: str) -> bool:
    """
    추출 category와 백과사전 primaryType 대분류가 어울리는지 판정한다.
    ('광혜원'[기관]이 묘소 문서[유적]에 붙는 오탐을 표시하기 위한 대응표)
    대응표에 없는 category나 빈 primaryType은 판단 불가로 보고 호환으로 처리한다.
    """
    compatible_types = {
        "인물": {"인물"},
        "사건": {"사건"},
        "국가": {"지명", "개념"},
        "왕조": {"지명", "개념", "인물"},
        "제도": {"제도", "개념", "의례·행사"},
        "정책": {"제도", "개념", "사건"},
        "단체": {"단체", "제도"},
        "기관": {"단체", "제도", "지명"},
        "문헌": {"문헌", "작품"},
        "문화재": {"작품", "유적", "물품", "문헌"},
        "조약": {"사건", "문헌", "제도", "개념"},
        "사상": {"개념", "단체", "작품"},
        "지명": {"지명", "유적"},
        "유물": {"물품", "작품", "유적", "문헌"},
        "유적": {"유적", "지명"},
    }
    if category not in compatible_types:
        return True
    if not primary_type_part:
        return True
    return primary_type_part in compatible_types[category]


def build_encyclopedia_index(jsonl_path: str) -> dict[str, list[dict]]:
    """백과사전 표제어·이칭 -> 문서 목록(eid, headword, 유형) 인덱스를 만든다."""
    index: dict[str, list[dict]] = {}
    for row in iter_encyclopedia_rows(jsonl_path):
        eid = (row.get("eid") or "").strip()
        headword = (row.get("headword") or "").strip()
        if not eid or not headword:
            continue
        aliases = row.get("articleAliases") or []
        if not isinstance(aliases, list):
            aliases = []
        entry = {
            "eid": eid,
            "headword": headword,
            "primary_type": (row.get("primaryType") or "").strip(),
            "primary_type_part": get_primary_type_part(row),
        }
        names = [headword] + [alias for alias in aliases if isinstance(alias, str)]
        for name in names:
            key = build_match_key(name)
            if not key:
                continue
            bucket = index.setdefault(key, [])
            if all(item["eid"] != eid for item in bucket):
                bucket.append(entry)
    return index


def build_itkc_people_index(csv_path: str) -> dict[str, list[dict]]:
    """ITKC 인물 이름(한글 부분) -> 인물 목록 인덱스를 만든다."""
    people_df = pd.read_csv(csv_path, dtype=str).fillna("")
    index: dict[str, list[dict]] = {}
    for record in people_df.to_dict("records"):
        hangul, hanja = split_itkc_name(record["name"])
        key = build_match_key(hangul)
        if not key:
            continue
        index.setdefault(key, []).append(
            {
                "person_id": record["person_id"],
                "name": hangul,
                "hanja": hanja,
                "birth_year": record["birth_year"],
                "death_year": record["death_year"],
                "bonkwan": record["bonkwan"],
            }
        )
    return index


def build_itkc_event_index(csv_path: str) -> dict[str, list[dict]]:
    """ITKC 사건명 -> 사건 목록 인덱스를 만든다."""
    events_df = pd.read_csv(csv_path, dtype=str).fillna("")
    index: dict[str, list[dict]] = {}
    for record in events_df.to_dict("records"):
        key = build_match_key(record["event_name"])
        if not key:
            continue
        bucket = index.setdefault(key, [])
        if all(item["event_id"] != record["event_id"] for item in bucket):
            bucket.append(
                {
                    "event_id": record["event_id"],
                    "event_name": record["event_name"],
                    "subject_category": record["subject_category"],
                    "period": record["period"],
                }
            )
    return index


def lookup_with_affix(
    term_keys: dict[str, str],
    index: dict[str, list[dict]],
    max_candidates: int,
) -> dict[str, dict]:
    """
    용어 키를 인덱스와 대조해 {용어: {"via", "candidates"}}를 만든다.
    정확 일치를 먼저 보고, 실패한 키만 모아 접두·접미 매칭을 한 번에 수행한다.
    """
    results: dict[str, dict] = {}
    unmatched_keys: set[str] = set()
    for term, key in term_keys.items():
        if key in index:
            results[term] = {"via": "exact", "candidates": index[key][:max_candidates]}
            continue
        unmatched_keys.add(key)

    affix_map = find_affix_matches(unmatched_keys, set(index.keys()))
    for term, key in term_keys.items():
        if term in results:
            continue
        if key in affix_map:
            matched_key = affix_map[key]
            results[term] = {
                "via": "affix",
                "candidates": index[matched_key][:max_candidates],
            }
    return results


def match_names(
    terms_csv: str,
    encyclopedia_jsonl: str,
    itkc_people_csv: str,
    itkc_events_csv: str,
    max_candidates: int = 20,
) -> list[dict]:
    """
    추출 용어를 백과사전 표제어·이칭, ITKC 인물명, ITKC 사건명과 이름 매칭한다.
    - 백과사전·사건: 정확 일치 + 접두·접미 보조 매칭
    - 인물 이름: 오탐 방지를 위해 정확 일치만 (김구 -> 김구해 오매칭 방지)
    후보를 확정하지 않고 전부 기록한다. 판별(동명이인 등)은 다음 단계에서 수행한다.
    """
    term_df = pd.read_csv(terms_csv, encoding="utf-8-sig")
    term_records = term_df.to_dict("records")
    term_keys = {
        record["canonical_term"]: build_match_key(record["canonical_term"])
        for record in term_records
        if build_match_key(record["canonical_term"])
    }

    print("백과사전 인덱스 구축 중...")
    ency_index = build_encyclopedia_index(encyclopedia_jsonl)
    print(f"  표제어·이칭 키 {len(ency_index)}개")
    print("ITKC 인물 인덱스 구축 중...")
    people_index = build_itkc_people_index(itkc_people_csv)
    print(f"  인물 이름 키 {len(people_index)}개")
    print("ITKC 사건 인덱스 구축 중...")
    event_index = build_itkc_event_index(itkc_events_csv)
    print(f"  사건명 키 {len(event_index)}개")

    ency_results = lookup_with_affix(term_keys, ency_index, max_candidates)
    event_results = lookup_with_affix(term_keys, event_index, max_candidates)

    results: list[dict] = []
    for record in term_records:
        term = record["canonical_term"]
        category = record["category"]
        key = term_keys.get(term, "")
        ency = ency_results.get(term)
        event = event_results.get(term)
        people_candidates = people_index.get(key, [])[:max_candidates]

        # 후보마다 category-유형 불일치 플래그를 붙인다 (인덱스 공유 객체라 복사해서 부착)
        ency_candidates = []
        if ency:
            ency_candidates = [
                {
                    **candidate,
                    "category_mismatch": not is_category_compatible(
                        category, candidate["primary_type_part"]
                    ),
                }
                for candidate in ency["candidates"]
            ]
            ency_candidates.sort(key=lambda candidate: candidate["category_mismatch"])

        results.append(
            {
                "canonical_term": term,
                "category": category,
                "problem_count": int(record["count"]),
                "is_noise": bool(is_noise_term(term)),
                "encyclopedia_via": ency["via"] if ency else None,
                "encyclopedia": ency_candidates,
                "itkc_people": people_candidates,
                "itkc_events_via": event["via"] if event else None,
                "itkc_events": event["candidates"] if event else [],
            }
        )
    return results


def print_match_report(results: list[dict], noise_display_limit: int = 30) -> None:
    """
    이름 매칭 결과 통계를 출력한다.
    노이즈 의심 용어는 결과에서 제거하지 않고 플래그로만 분리 집계한다.
    """
    noise_items = [item for item in results if item["is_noise"]]
    counted = [item for item in results if not item["is_noise"]]
    total = len(counted)
    ency_hit = sum(1 for item in counted if item["encyclopedia"])
    ency_multi = sum(1 for item in counted if len(item["encyclopedia"]) > 1)
    people_hit = sum(1 for item in counted if item["itkc_people"])
    people_multi = sum(1 for item in counted if len(item["itkc_people"]) > 1)
    event_hit = sum(1 for item in counted if item["itkc_events"])
    any_hit = sum(
        1
        for item in counted
        if item["encyclopedia"] or item["itkc_people"] or item["itkc_events"]
    )

    all_mismatch = sum(
        1
        for item in counted
        if item["encyclopedia"]
        and all(candidate["category_mismatch"] for candidate in item["encyclopedia"])
    )

    print(f"전체 용어: {len(results)}개 (노이즈 의심 {len(noise_items)}개 제외하고 집계)")
    print(f"백과사전 매칭: {ency_hit}개 (복수 후보 {ency_multi}개)")
    print(f"  후보 전부 category-유형 불일치 (오매칭 의심): {all_mismatch}개")
    print(f"ITKC 인물 매칭: {people_hit}개 (복수 후보 {people_multi}개)")
    print(f"ITKC 사건 매칭: {event_hit}개")
    print(f"하나 이상 매칭: {any_hit}개 ({any_hit / total * 100:.1f}%)")
    print(f"전부 미매칭: {total - any_hit}개")

    person_terms = [item for item in counted if item["category"] == "인물"]
    if person_terms:
        person_hit = sum(
            1
            for item in person_terms
            if item["itkc_people"] or item["encyclopedia"]
        )
        print(
            f"인물 용어 {len(person_terms)}개 중 백과·ITKC 어느 쪽이든 매칭: "
            f"{person_hit}개 ({person_hit / len(person_terms) * 100:.1f}%)"
        )

    if noise_items:
        noise_names = [item["canonical_term"] for item in noise_items]
        print(f"노이즈 의심 용어 (검토용, 결과 JSON에는 is_noise=true로 포함됨):")
        print(f"  {noise_names[:noise_display_limit]}")
        if len(noise_names) > noise_display_limit:
            print(f"  ... 외 {len(noise_names) - noise_display_limit}개")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = ArgumentParser(
        description="추출 용어를 백과사전·ITKC 인물·ITKC 사건과 이름 매칭"
    )
    parser.add_argument("terms_csv", nargs="?", default="", help="용어 집계 CSV 경로")
    parser.add_argument(
        "--encyclopedia-jsonl", default="", help="백과사전 articles_detail.jsonl 경로"
    )
    parser.add_argument("--itkc-people", default="", help="itkc_people.csv 경로")
    parser.add_argument("--itkc-events", default="", help="itkc_events.csv 경로")
    parser.add_argument(
        "--output", default="term_name_matches.json", help="매칭 결과 JSON 저장 경로"
    )
    parser.add_argument(
        "--max-candidates", type=int, default=20, help="용어당 기록할 최대 후보 수"
    )
    cli_args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[4]
    raw_data_dir = project_root / "etl" / "raw_data"

    terms_csv_path = cli_args.terms_csv
    if not terms_csv_path:
        terms_csv_path = str(
            Path(__file__).resolve().parent.parent
            / "output" / "csv" / "exam_history_terms.csv"
        )

    encyclopedia_path = cli_args.encyclopedia_jsonl
    if not encyclopedia_path:
        encyclopedia_path = str(
            raw_data_dir / "한국민족문화대백과사전" / "articles_detail.jsonl"
        )

    itkc_people_path = cli_args.itkc_people
    if not itkc_people_path:
        itkc_people_path = str(raw_data_dir / "한국고전종합DB_관계망" / "itkc_people.csv")

    itkc_events_path = cli_args.itkc_events
    if not itkc_events_path:
        itkc_events_path = str(raw_data_dir / "한국고전종합DB_관계망" / "itkc_events.csv")

    match_results = match_names(
        terms_csv=terms_csv_path,
        encyclopedia_jsonl=encyclopedia_path,
        itkc_people_csv=itkc_people_path,
        itkc_events_csv=itkc_events_path,
        max_candidates=cli_args.max_candidates,
    )
    print_match_report(match_results)

    with open(cli_args.output, "w", encoding="utf-8") as output_file:
        dump(match_results, output_file, ensure_ascii=False, indent=2)
    print(f"저장 완료: {cli_args.output}")

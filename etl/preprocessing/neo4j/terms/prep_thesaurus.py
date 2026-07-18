import re
import sys
from argparse import ArgumentParser
from bisect import bisect_left
from json import dump, load
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import normalize_history_term


def build_match_key(term: str) -> str:
    """
    커버리지 대조용 키를 만든다.
    - normalize_history_term(유니코드·공백 정리) 후 문장부호 제거
    - 홀로 있는 한 자리 숫자는 한글 표기로 통일 (황룡사9층목탑 == 황룡사구층목탑)
      여러 자리 숫자(1392, 10정)는 그대로 둔다
    """
    digit_to_hangul = {
        "0": "영", "1": "일", "2": "이", "3": "삼", "4": "사",
        "5": "오", "6": "육", "7": "칠", "8": "팔", "9": "구",
    }
    normalized = normalize_history_term(term)
    normalized = re.sub(r"[^0-9a-z가-힣一-龥]", "", normalized)

    converted: list[str] = []
    for index, char in enumerate(normalized):
        replacement = char
        if char.isdigit():
            prev_is_digit = index > 0 and normalized[index - 1].isdigit()
            next_is_digit = index + 1 < len(normalized) and normalized[index + 1].isdigit()
            if not prev_is_digit and not next_is_digit:
                replacement = digit_to_hangul[char]
        converted.append(replacement)
    return "".join(converted)


def prep_thesaurus(csv_path: str) -> pd.DataFrame:
    """한국 역사 용어 시소러스 CSV를 용어 JSON 구조에 맞는 DataFrame으로 변환한다."""
    thesaurus_df = pd.read_csv(csv_path, dtype=str).fillna("")
    required_columns = {
        "term_id",
        "term_name",
        "term_ch",
        "term_year",
        "term_times",
        "term_lk",
        "term_desc",
    }
    missing_columns = required_columns.difference(thesaurus_df.columns)
    if missing_columns:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(f"필수 CSV 컬럼이 없습니다: {missing_text}")

    for column in required_columns:
        thesaurus_df[column] = thesaurus_df[column].str.strip()

    if thesaurus_df["term_id"].duplicated().any():
        duplicated_ids = thesaurus_df.loc[
            thesaurus_df["term_id"].duplicated(keep=False), "term_id"
        ].drop_duplicates()
        raise ValueError(f"중복 term_id가 있습니다: {duplicated_ids.tolist()}")

    thesaurus_df = thesaurus_df.loc[thesaurus_df["term_name"].ne("")].copy()
    thesaurus_df["problem_id"] = "thesaurus_" + thesaurus_df["term_id"]
    thesaurus_df["raw_term"] = thesaurus_df["term_name"]
    thesaurus_df["canonical_term"] = thesaurus_df["term_name"]
    categories: list[str | None] = []
    for category_path in thesaurus_df["term_lk"]:
        category = None
        if category_path:
            category = category_path.split(">")[-1].strip()
        categories.append(category)
    thesaurus_df["category"] = categories
    thesaurus_df["era"] = thesaurus_df["term_times"].replace("", None)
    thesaurus_df["hanja"] = thesaurus_df["term_ch"].replace("", None)
    thesaurus_df["aliases"] = [[] for _ in range(len(thesaurus_df))]
    thesaurus_df["context"] = thesaurus_df["term_desc"].replace("", None)
    thesaurus_df["normalized_term"] = thesaurus_df["canonical_term"].map(
        normalize_history_term
    )

    top_categories = thesaurus_df["term_lk"].str.split(">").str[0]
    person_df = thesaurus_df.loc[top_categories.eq("인명")]
    same_person_columns = [
        "normalized_term",
        "term_ch",
        "term_times",
        "term_desc",
    ]
    duplicate_person_indexes = person_df.loc[
        person_df.duplicated(subset=same_person_columns, keep="first")
    ].index
    thesaurus_df = thesaurus_df.drop(index=duplicate_person_indexes)
    return thesaurus_df.reset_index(drop=True)


def find_homonym_candidates(thesaurus_df: pd.DataFrame) -> pd.DataFrame:
    """인명 분류에서 표준화한 이름이 같은 동명이인 후보를 찾는다."""
    top_categories = thesaurus_df["term_lk"].str.split(">").str[0]
    person_df = thesaurus_df.loc[top_categories.eq("인명")]
    duplicate_mask = person_df["normalized_term"].duplicated(keep=False)
    columns = [
        "term_id",
        "canonical_term",
        "hanja",
        "term_year",
        "era",
        "term_lk",
        "context",
    ]
    return person_df.loc[duplicate_mask, columns].sort_values(
        ["canonical_term", "term_id"]
    )


def build_thesaurus_json(thesaurus_df: pd.DataFrame) -> list[dict]:
    """DataFrame을 기출문제 용어 추출 결과와 같은 JSON 구조로 변환한다."""
    term_fields = [
        "raw_term",
        "canonical_term",
        "category",
        "era",
        "hanja",
        "aliases",
        "context",
    ]
    results: list[dict] = []
    for row in thesaurus_df.itertuples(index=False):
        term = {field: getattr(row, field) for field in term_fields}
        results.append({"problem_id": row.problem_id, "terms": [term]})
    return results


def save_thesaurus_json(thesaurus_df: pd.DataFrame, output_path: str) -> None:
    """변환한 시소러스를 UTF-8 JSON 파일로 저장한다."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as output_file:
        dump(
            build_thesaurus_json(thesaurus_df),
            output_file,
            ensure_ascii=False,
            indent=4,
        )


def load_extracted_terms(json_path: str) -> pd.DataFrame:
    """문항별 용어 JSON을 커버리지 비교용 DataFrame으로 변환한다."""
    with open(json_path, "r", encoding="utf-8") as input_file:
        extracted_items = load(input_file)

    rows: list[dict] = []
    for item in extracted_items:
        problem_id = item.get("problem_id")
        for term in item.get("terms", []):
            rows.append(
                {
                    "problem_id": problem_id,
                    "raw_term": term.get("raw_term"),
                    "canonical_term": term.get("canonical_term"),
                }
            )

    extracted_df = pd.DataFrame(
        rows,
        columns=["problem_id", "raw_term", "canonical_term"],
    )
    extracted_df = extracted_df.dropna(subset=["canonical_term"])
    extracted_df["normalized_term"] = extracted_df["canonical_term"].map(
        normalize_history_term
    )
    extracted_df = extracted_df.loc[extracted_df["normalized_term"].ne("")]
    return extracted_df.reset_index(drop=True)


def calculate_coverage(
    extracted_df: pd.DataFrame,
    thesaurus_df: pd.DataFrame,
    threshold: float = 90.0,
) -> dict[str, object]:
    """추출한 고유 용어 중 시소러스가 커버하는 비율을 계산한다."""
    if not 0.0 <= threshold <= 100.0:
        raise ValueError("임계치는 0에서 100 사이여야 합니다.")

    if "canonical_term" not in extracted_df.columns:
        raise ValueError("추출 용어 DataFrame에 canonical_term 컬럼이 없습니다.")
    if "canonical_term" not in thesaurus_df.columns:
        raise ValueError("시소러스 DataFrame에 canonical_term 컬럼이 없습니다.")

    extracted_terms_df = extracted_df.dropna(subset=["canonical_term"]).copy()
    thesaurus_terms_df = thesaurus_df.dropna(subset=["canonical_term"]).copy()
    extracted_terms_df["normalized_term"] = extracted_terms_df["canonical_term"].map(
        build_match_key
    )
    thesaurus_terms_df["normalized_term"] = thesaurus_terms_df[
        "canonical_term"
    ].map(build_match_key)

    extracted_terms = set(extracted_terms_df["normalized_term"])
    thesaurus_terms = set(thesaurus_terms_df["normalized_term"])
    extracted_terms.discard("")
    thesaurus_terms.discard("")
    exact_covered = extracted_terms.intersection(thesaurus_terms)

    # 부분 포함 매칭: 추출어가 시소러스 표제어의 접두어이고 꼬리가 3글자 이하면
    # 커버로 인정 (운요호 -> 운요호사건, 청산리 -> 청산리대첩)
    # 2글자 이하 용어는 오탐이 많아 제외 (남성 -> 남성록 방지)
    sorted_thesaurus = sorted(thesaurus_terms)
    partial_matches: dict[str, str] = {}
    for term in extracted_terms.difference(exact_covered):
        if len(term) < 3:
            continue
        position = bisect_left(sorted_thesaurus, term)
        while position < len(sorted_thesaurus):
            candidate = sorted_thesaurus[position]
            if not candidate.startswith(term):
                break
            if len(candidate) - len(term) <= 3:
                partial_matches[term] = candidate
                break
            position += 1

    covered_terms = exact_covered.union(partial_matches.keys())
    uncovered_terms = extracted_terms.difference(covered_terms)

    coverage_percent = 0.0
    if extracted_terms:
        coverage_percent = len(covered_terms) / len(extracted_terms) * 100

    display_names = (
        extracted_terms_df.drop_duplicates("normalized_term")
        .set_index("normalized_term")["canonical_term"]
        .to_dict()
    )
    thesaurus_display_names = (
        thesaurus_terms_df.drop_duplicates("normalized_term")
        .set_index("normalized_term")["canonical_term"]
        .to_dict()
    )
    uncovered_names = sorted(display_names[term] for term in uncovered_terms)
    partial_match_names = {
        display_names[term]: thesaurus_display_names[candidate]
        for term, candidate in partial_matches.items()
    }
    return {
        "extracted_count": len(extracted_terms),
        "covered_count": len(covered_terms),
        "exact_covered_count": len(exact_covered),
        "partial_covered_count": len(partial_matches),
        "uncovered_count": len(uncovered_terms),
        "coverage_percent": coverage_percent,
        "threshold": threshold,
        "meets_threshold": coverage_percent >= threshold,
        "partial_matches": partial_match_names,
        "uncovered_terms": uncovered_names,
    }


def print_homonym_report(candidates: pd.DataFrame, display_limit: int) -> None:
    """동명이인 후보 검사 결과를 터미널에 출력한다."""
    if candidates.empty:
        print("동명이인 후보: 없음")
        return

    group_count = candidates["canonical_term"].map(normalize_history_term).nunique()
    print(f"동명이인 후보: {group_count}개 이름, {len(candidates)}개 행")
    print(candidates.head(display_limit).to_string(index=False))
    if len(candidates) > display_limit:
        print(f"... 나머지 {len(candidates) - display_limit}개 행 생략")


def print_coverage_report(report: dict[str, object], display_limit: int) -> None:
    """시소러스 커버리지 결과를 터미널에 출력한다."""
    print(f"추출 고유 용어: {report['extracted_count']}개")
    print(
        f"시소러스 커버 용어: {report['covered_count']}개 "
        f"(정확 일치 {report['exact_covered_count']} + 부분 일치 {report['partial_covered_count']})"
    )
    print(f"미커버 용어: {report['uncovered_count']}개")
    print(f"커버리지: {report['coverage_percent']:.2f}%")
    decision = "사용 보류"
    if report["meets_threshold"]:
        decision = "사용 가능"
    print(f"임계치 {report['threshold']:.2f}% 판정: {decision}")

    uncovered_terms = report["uncovered_terms"]
    if uncovered_terms:
        print(f"미커버 용어 예시: {uncovered_terms[:display_limit]}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = ArgumentParser(
        description="한국 역사 용어 시소러스 변환 및 추출 용어 커버리지 계산"
    )
    parser.add_argument("csv_path", help="한국 역사 용어 시소러스 CSV 경로")
    parser.add_argument("--output", required=True, help="변환한 JSON 저장 경로")
    parser.add_argument(
        "--extracted-json",
        default="",
        help="LLM으로 추출한 문항별 용어 JSON 경로",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=90.0,
        help="시소러스 사용 판정 커버리지 임계치",
    )
    parser.add_argument(
        "--display-limit",
        type=int,
        default=20,
        help="후보 및 미커버 용어 표시 개수",
    )
    cli_args = parser.parse_args()

    prepared_df = prep_thesaurus(cli_args.csv_path)
    homonym_candidates = find_homonym_candidates(prepared_df)
    print_homonym_report(homonym_candidates, cli_args.display_limit)

    save_thesaurus_json(prepared_df, cli_args.output)
    print(f"시소러스 JSON 저장 완료: {cli_args.output} ({len(prepared_df)}개 용어)")

    if cli_args.extracted_json:
        exam_term_df = load_extracted_terms(cli_args.extracted_json)
        coverage_report = calculate_coverage(
            exam_term_df,
            prepared_df,
            threshold=cli_args.threshold,
        )
        print_coverage_report(coverage_report, cli_args.display_limit)

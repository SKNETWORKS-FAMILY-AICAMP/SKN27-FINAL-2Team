import re
import sys
from argparse import ArgumentParser
from bisect import bisect_left
from json import JSONDecodeError, JSONDecoder, dump, load
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import load_pipeline_policy, normalize_history_term


def read_short_number(digits: str) -> str:
    """
    1~2자리 숫자를 한자어 독음으로 바꾼다 (9 -> 구, 12 -> 십이, 20 -> 이십).
    세 자리 이상이거나 0으로 시작하면 그대로 반환한다 (1392, 6·15의 615 등).
    """
    digit_to_hangul = {
        "0": "영", "1": "일", "2": "이", "3": "삼", "4": "사",
        "5": "오", "6": "육", "7": "칠", "8": "팔", "9": "구",
    }
    if len(digits) == 1:
        return digit_to_hangul[digits]
    if len(digits) == 2 and digits[0] != "0":
        reading = ""
        if digits[0] != "1":
            reading += digit_to_hangul[digits[0]]
        reading += "십"
        if digits[1] != "0":
            reading += digit_to_hangul[digits[1]]
        return reading
    return digits


def apply_dueum_rule(text: str) -> str:
    """
    첫 글자가 ㄹ 초성이면 두음법칙을 적용한다 (류성룡 -> 유성룡, 락랑 -> 낙랑).
    ㅣ·y계 모음 앞에서는 ㅇ으로, 그 외 모음 앞에서는 ㄴ으로 바꾼다.
    """
    hangul_base = 0xAC00
    syllable_count = 11172
    rieul_initial = 5
    nieun_initial = 2
    ieung_initial = 11
    # 유니코드 중성 순서 기준 ㅑ, ㅒ, ㅕ, ㅖ, ㅛ, ㅠ, ㅣ
    y_medials = {2, 3, 6, 7, 12, 17, 20}

    if not text:
        return text
    code = ord(text[0]) - hangul_base
    if not 0 <= code < syllable_count:
        return text
    initial = code // 588
    if initial != rieul_initial:
        return text
    medial = (code % 588) // 28
    final = code % 28
    new_initial = nieun_initial
    if medial in y_medials:
        new_initial = ieung_initial
    converted = chr(hangul_base + new_initial * 588 + medial * 28 + final)
    return converted + text[1:]


def build_match_key(term: str) -> str:
    """
    커버리지 대조용 키를 만든다.
    - normalize_history_term(유니코드·공백 정리) 후 문장부호 제거
    - 1~2자리 숫자는 한자어 독음으로 통일 (황룡사9층목탑 == 황룡사구층목탑, 12목 == 십이목)
    - 첫 글자에 두음법칙을 적용해 표기 차이를 흡수한다 (류성룡 == 유성룡)
    """
    normalized = normalize_history_term(term)
    normalized = re.sub(r"[^0-9a-z가-힣一-龥]", "", normalized)
    normalized = re.sub(r"\d+", lambda match: read_short_number(match.group()), normalized)
    return apply_dueum_rule(normalized)


def iter_encyclopedia_rows(jsonl_path: str):
    """백과사전 JSONL의 JSON 레코드를 순서대로 읽는다. 여러 줄에 걸친 레코드도 허용한다."""
    decoder = JSONDecoder(strict=False)
    buffer = ""
    with open(jsonl_path, "r", encoding="utf-8") as input_file:
        for line in input_file:
            buffer += line
            while buffer.strip():
                candidate = buffer.lstrip()
                try:
                    row, end = decoder.raw_decode(candidate)
                except JSONDecodeError:
                    buffer = candidate
                    break
                yield row
                buffer = candidate[end:]


def load_encyclopedia_terms(jsonl_path: str) -> dict[str, str]:
    """
    백과사전 JSONL에서 표제어(headword)와 이칭(articleAliases)을 읽어
    커버리지 대조용 {match_key: 표제어} 사전으로 만든다.
    같은 키가 여러 문서에 나오면 먼저 나온 표제어를 유지한다.
    """
    reference: dict[str, str] = {}
    for row in iter_encyclopedia_rows(jsonl_path):
        headword = (row.get("headword") or "").strip()
        if not headword:
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
        names = [headword] + alias_names
        for name in names:
            key = build_match_key(name)
            if key and key not in reference:
                reference[key] = headword
    return reference


def find_key_with_prefix(sorted_keys: list[str], prefix: str, max_tail: int) -> str | None:
    """정렬된 키 목록에서 prefix로 시작하고 꼬리 길이가 max_tail 이하인 키를 찾는다."""
    position = bisect_left(sorted_keys, prefix)
    while position < len(sorted_keys):
        candidate = sorted_keys[position]
        if not candidate.startswith(prefix):
            return None
        if len(candidate) - len(prefix) <= max_tail:
            return candidate
        position += 1
    return None


def find_affix_matches(
    terms: set[str],
    reference_keys: set[str],
    minimum_length: int,
    maximum_difference: int,
) -> dict[str, str]:
    """
    외부 정책의 최소 길이와 최대 길이 차이를 적용해 양방향 접두·접미 매칭한다.
    추출어가 더 짧은 경우와 원천 표제어가 더 짧은 경우를 모두 후보로 인정한다.
    """
    sorted_forward = sorted(reference_keys)
    sorted_backward = sorted(key[::-1] for key in reference_keys)
    matches: dict[str, str] = {}
    for term in terms:
        if len(term) < minimum_length:
            continue
        found = find_key_with_prefix(sorted_forward, term, maximum_difference)
        if found is None:
            reversed_found = find_key_with_prefix(
                sorted_backward,
                term[::-1],
                maximum_difference,
            )
            if reversed_found is not None:
                found = reversed_found[::-1]
        if found is None:
            for difference in range(1, maximum_difference + 1):
                if len(term) - difference < minimum_length:
                    continue
                prefix_candidate = term[:-difference]
                suffix_candidate = term[difference:]
                if prefix_candidate in reference_keys:
                    found = prefix_candidate
                    break
                if suffix_candidate in reference_keys:
                    found = suffix_candidate
                    break
        if found is not None:
            matches[term] = found
    return matches


def is_noise_term(term: str, noise_policy: dict) -> bool:
    """
    문장형 서술이나 한 글자 일반어가 용어로 잘못 추출된 경우를 판정한다.
    ('도둑질한 자에게 12배로 배상하게 하였다', '왕' 등)
    """
    stripped = str(term).strip()
    if len(stripped.replace(" ", "")) < noise_policy["minimum_compact_length"]:
        return True
    sentence_endings = tuple(noise_policy["sentence_endings"])
    if (
        stripped.count(" ") >= noise_policy["minimum_sentence_spaces"]
        and stripped.endswith(sentence_endings)
    ):
        return True
    return False


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
    policy: dict,
    threshold: float = 90.0,
    encyclopedia_terms: dict[str, str] | None = None,
) -> dict[str, object]:
    """
    추출한 고유 용어 중 시소러스가 커버하는 비율을 계산한다.
    문장형·한 글자 노이즈 용어는 집계에서 제외하고 리포트에 따로 기록한다.
    encyclopedia_terms를 주면 시소러스에서 못 찾은 용어를
    백과사전 표제어·이칭({match_key: 표제어})으로 2차 매칭한다.
    """
    if not 0.0 <= threshold <= 100.0:
        raise ValueError("임계치는 0에서 100 사이여야 합니다.")

    if "canonical_term" not in extracted_df.columns:
        raise ValueError("추출 용어 DataFrame에 canonical_term 컬럼이 없습니다.")
    if "canonical_term" not in thesaurus_df.columns:
        raise ValueError("시소러스 DataFrame에 canonical_term 컬럼이 없습니다.")

    extracted_terms_df = extracted_df.dropna(subset=["canonical_term"]).copy()
    noise_mask = extracted_terms_df["canonical_term"].map(
        lambda term: is_noise_term(term, policy["noise"])
    )
    noise_names = sorted(set(extracted_terms_df.loc[noise_mask, "canonical_term"]))
    extracted_terms_df = extracted_terms_df.loc[~noise_mask].copy()
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

    partial_matches = find_affix_matches(
        extracted_terms.difference(exact_covered),
        thesaurus_terms,
        minimum_length=policy["coverage"]["minimum_affix_length"],
        maximum_difference=policy["coverage"]["maximum_affix_difference"],
    )

    covered_terms = exact_covered.union(partial_matches.keys())

    # 시소러스 미커버 용어를 백과사전 표제어·이칭으로 2차 매칭
    encyclopedia_reference = encyclopedia_terms or {}
    remaining_terms = extracted_terms.difference(covered_terms)
    encyclopedia_covered: dict[str, str] = {
        term: encyclopedia_reference[term]
        for term in remaining_terms
        if term in encyclopedia_reference
    }
    encyclopedia_affix = find_affix_matches(
        remaining_terms.difference(encyclopedia_covered.keys()),
        set(encyclopedia_reference.keys()),
        minimum_length=policy["coverage"]["minimum_affix_length"],
        maximum_difference=policy["coverage"]["maximum_affix_difference"],
    )
    for term, reference_key in encyclopedia_affix.items():
        encyclopedia_covered[term] = encyclopedia_reference[reference_key]

    covered_terms = covered_terms.union(encyclopedia_covered.keys())
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
    encyclopedia_match_names = {
        display_names[term]: headword
        for term, headword in encyclopedia_covered.items()
    }
    return {
        "extracted_count": len(extracted_terms),
        "covered_count": len(covered_terms),
        "exact_covered_count": len(exact_covered),
        "partial_covered_count": len(partial_matches),
        "encyclopedia_covered_count": len(encyclopedia_covered),
        "noise_filtered_count": len(noise_names),
        "noise_filtered_terms": noise_names,
        "uncovered_count": len(uncovered_terms),
        "coverage_percent": coverage_percent,
        "threshold": threshold,
        "meets_threshold": coverage_percent >= threshold,
        "coverage_scope": "normalized_name_exact_and_bidirectional_affix",
        "uncovered_interpretation": "NAME_ONLY_UNCOVERED_NOT_SOURCE_ABSENT",
        "resolution_policy_version": policy["policy_version"],
        "normalization_policy_version": policy["normalization_policy_version"],
        "partial_matches": partial_match_names,
        "encyclopedia_matches": encyclopedia_match_names,
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
        f"시소러스 커버 용어: {report['exact_covered_count'] + report['partial_covered_count']}개 "
        f"(정확 일치 {report['exact_covered_count']} + 부분 일치 {report['partial_covered_count']})"
    )
    print(f"백과사전 커버 용어: {report['encyclopedia_covered_count']}개 (표제어·이칭 2차 매칭)")
    print(f"전체 커버 용어: {report['covered_count']}개")
    print(f"노이즈 제외 용어: {report['noise_filtered_count']}개 (문장형·한 글자)")
    print(f"이름 기준 미커버 용어: {report['uncovered_count']}개")
    print(f"이름 기준 커버리지: {report['coverage_percent']:.2f}%")
    decision = "사용 보류"
    if report["meets_threshold"]:
        decision = "사용 가능"
    print(f"임계치 {report['threshold']:.2f}% 판정: {decision}")

    uncovered_terms = report["uncovered_terms"]
    if uncovered_terms:
        print(
            "이름 기준 미커버 예시(원천 부재 의미 아님): "
            f"{uncovered_terms[:display_limit]}"
        )


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
        "--encyclopedia-jsonl",
        default="",
        help="백과사전 JSONL 경로 (미커버 용어를 표제어·이칭으로 2차 매칭)",
    )
    parser.add_argument(
        "--display-limit",
        type=int,
        default=20,
        help="후보 및 미커버 용어 표시 개수",
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

    prepared_df = prep_thesaurus(cli_args.csv_path)
    homonym_candidates = find_homonym_candidates(prepared_df)
    print_homonym_report(homonym_candidates, cli_args.display_limit)

    save_thesaurus_json(prepared_df, cli_args.output)
    print(f"시소러스 JSON 저장 완료: {cli_args.output} ({len(prepared_df)}개 용어)")

    if cli_args.extracted_json:
        encyclopedia_reference: dict[str, str] = {}
        if cli_args.encyclopedia_jsonl:
            encyclopedia_reference = load_encyclopedia_terms(cli_args.encyclopedia_jsonl)
            print(f"백과사전 표제어·이칭 로드: {len(encyclopedia_reference)}개 키")

        exam_term_df = load_extracted_terms(cli_args.extracted_json)
        coverage_report = calculate_coverage(
            exam_term_df,
            prepared_df,
            policy=pipeline_policy,
            threshold=cli_args.threshold,
            encyclopedia_terms=encyclopedia_reference,
        )
        print_coverage_report(coverage_report, cli_args.display_limit)

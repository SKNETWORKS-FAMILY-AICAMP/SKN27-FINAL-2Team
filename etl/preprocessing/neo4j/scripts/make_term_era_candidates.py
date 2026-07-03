"""고조선/초기 국가 시대 후보 용어 추출.

원본 시소러스의 시대 표기(term_times)에는 고조선, 초기 국가가 존재하지 않는다.
이 스크립트는 용어 이름과 설명문에서 시대 지표 단어를 찾아
검수용 후보 CSV(staging/term_era_candidate.csv)를 생성한다.

- SAFE 지표(고조선, 옥저, 사출도 등 다른 의미로 쓰이기 어려운 단어)가
  설명문 첫 문장에 나오면 confidence=HIGH, review_status=AUTO_APPROVED.
- AMBIGUOUS 지표(부여=동사/지명, 진한=형용사 등)나 뒤쪽 문장 매칭은
  confidence=LOW, review_status=PENDING으로 사람 검수 대상.
- 재실행 시 기존 후보 파일의 검수 결정(APPROVED/REJECTED)은 보존한다.

이 스크립트는 runner에 포함하지 않는다. 후보를 갱신하고 싶을 때 수동 실행한다.
검수 완료 후 make_theme_era_csv.py가 AUTO_APPROVED/APPROVED 행만
term_in_era.csv에 match_source=DESC_KEYWORD로 반영한다.
"""

from pathlib import Path
import argparse
import re

import pandas as pd

from neo4j_common import read_csv, save_csv, print_summary


def build_default_paths(script_path):
    neo4j_dir = script_path.parents[1]

    return {
        "terms": neo4j_dir / "normalized" / "terms.csv",
        "candidate_output": neo4j_dir / "staging" / "term_era_candidate.csv",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="고조선/초기 국가 시대 후보 용어를 추출해 검수용 CSV를 만든다."
    )
    parser.add_argument("--terms-path", type=Path, default=default_paths["terms"])
    parser.add_argument(
        "--candidate-output-path",
        type=Path,
        default=default_paths["candidate_output"],
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="CSV 파일을 실제로 저장한다. 없으면 dry run으로 요약만 출력한다.",
    )

    return parser.parse_args()


def build_era_markers():
    # SAFE: 다른 의미로 쓰이기 어려운 지표. AMBIGUOUS: 동사/형용사/지명과 혼동 가능.
    return {
        "고조선": {
            "safe": ["고조선", "위만조선", "단군조선", "기자조선", "왕검성", "단군왕검", "우거왕"],
            "ambiguous": ["위만", "준왕"],
        },
        "초기 국가": {
            "safe": ["옥저", "동예", "사출도", "민며느리제", "서옥제", "골장제", "목지국", "삼한"],
            "ambiguous": ["부여", "마한", "진한", "변한", "소도", "천군", "영고", "무천", "읍군", "삼로"],
        },
    }


def build_false_positive_patterns():
    # '명칭을 부여하였다' 같은 동사 사용을 제외하기 위한 패턴.
    return [
        re.compile(r"부여[하받되할한함]"),
        re.compile(r"진한\s*(색|맛|향)"),
    ]


def remove_false_positive_text(text, false_positive_patterns):
    cleaned_text = text

    for pattern in false_positive_patterns:
        cleaned_text = pattern.sub(" ", cleaned_text)

    return cleaned_text


def build_book_context_pattern():
    # 후대에 편찬된 역사서가 시대 지표를 언급하는 경우(예: 단군조선부터 서술한 책)는
    # 해당 시대 용어가 아니므로 자동 승인하지 않는다.
    return re.compile(r"편찬|저술|간행|서술한|역사책|다루어 쓴|기록한 책")


def extract_first_sentence(text):
    parts = re.split(r"[.。]\s*", text, maxsplit=1)
    return parts[0]


def build_snippet(text, marker, width):
    marker_index = text.find(marker)

    if marker_index == -1:
        return ""

    snippet_start = max(0, marker_index - width)
    snippet_end = marker_index + len(marker) + width

    return text[snippet_start:snippet_end].replace("\n", " ").strip()


def match_term_row(term_name, term_desc, era_markers, false_positive_patterns):
    # 한 용어에 대해 (era, marker, confidence) 매칭 목록을 만든다.
    cleaned_desc = remove_false_positive_text(term_desc, false_positive_patterns)
    first_sentence = extract_first_sentence(cleaned_desc)
    is_book_context = bool(build_book_context_pattern().search(cleaned_desc))
    safe_confidence = "HIGH"

    if is_book_context:
        safe_confidence = "LOW"

    matches = []

    for era_name, markers in era_markers.items():
        for marker in markers["safe"]:
            if marker in term_name:
                matches.append((era_name, marker, safe_confidence, term_name))
            elif marker in first_sentence:
                matches.append(
                    (
                        era_name,
                        marker,
                        safe_confidence,
                        build_snippet(cleaned_desc, marker, 30),
                    )
                )
            elif marker in cleaned_desc:
                matches.append(
                    (era_name, marker, "LOW", build_snippet(cleaned_desc, marker, 30))
                )

        for marker in markers["ambiguous"]:
            if marker in term_name or marker in cleaned_desc:
                snippet_source = cleaned_desc if marker in cleaned_desc else term_name
                matches.append(
                    (era_name, marker, "LOW", build_snippet(snippet_source, marker, 30))
                )

    return matches


def build_candidates(terms, era_markers, false_positive_patterns):
    candidate_rows = []

    for term_row in terms.itertuples(index=False):
        term_name = str(term_row.term_name or "")
        term_desc = str(term_row.term_desc or "")
        matches = match_term_row(
            term_name, term_desc, era_markers, false_positive_patterns
        )

        for era_name, marker, confidence, snippet in matches:
            candidate_rows.append(
                {
                    "term_id": term_row.term_id,
                    "term_name": term_name,
                    "era_name": era_name,
                    "matched_marker": marker,
                    "evidence_snippet": snippet,
                    "confidence": confidence,
                }
            )

    candidate_data = pd.DataFrame(candidate_rows)

    if candidate_data.empty:
        return candidate_data

    # 같은 (용어, 시대)는 confidence가 높은 매칭 하나만 남긴다.
    candidate_data["confidence_rank"] = candidate_data["confidence"].map(
        {"HIGH": 0, "LOW": 1}
    )
    candidate_data = candidate_data.sort_values("confidence_rank")
    candidate_data = candidate_data.drop_duplicates(subset=["term_id", "era_name"])
    candidate_data = candidate_data.drop(columns=["confidence_rank"])
    candidate_data["review_status"] = candidate_data["confidence"].map(
        {"HIGH": "AUTO_APPROVED", "LOW": "PENDING"}
    )

    return candidate_data.reset_index(drop=True)


def preserve_existing_reviews(candidate_data, candidate_output_path):
    # 재실행 시 사람이 내린 검수 결정(APPROVED/REJECTED)을 보존한다.
    if not candidate_output_path.exists():
        return candidate_data

    existing_data = read_csv(candidate_output_path, "existing_term_era_candidate")
    decided_data = existing_data[
        existing_data["review_status"].isin(["APPROVED", "REJECTED"])
    ][["term_id", "era_name", "review_status"]]
    decided_lookup = {
        (row.term_id, row.era_name): row.review_status
        for row in decided_data.itertuples(index=False)
    }

    def resolve_status(candidate_row):
        decided_status = decided_lookup.get(
            (candidate_row["term_id"], candidate_row["era_name"])
        )

        if decided_status:
            return decided_status

        return candidate_row["review_status"]

    candidate_data["review_status"] = candidate_data.apply(resolve_status, axis=1)

    return candidate_data


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)

    terms = read_csv(args.terms_path, "terms")
    era_markers = build_era_markers()
    false_positive_patterns = build_false_positive_patterns()

    candidate_data = build_candidates(terms, era_markers, false_positive_patterns)
    candidate_data = preserve_existing_reviews(
        candidate_data, args.candidate_output_path
    )

    print_summary("term_era_candidate.csv", candidate_data)
    print(candidate_data.groupby(["era_name", "review_status"]).size())

    if args.save:
        save_csv(candidate_data, args.candidate_output_path)
        print(f"saved: {args.candidate_output_path}")

    if not args.save:
        print("dry_run: no files saved. Use --save to write CSV files.")


if __name__ == "__main__":
    main()

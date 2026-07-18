from argparse import ArgumentParser

import pandas as pd


def match_thesaurus(term_df: pd.DataFrame, thesaurus_path: str) -> pd.DataFrame:
    """
    추출된 용어 DataFrame을 한국역사용어시소러스 CSV와 대조해 정보를 붙이는 함수
    - 매칭 키: 공백을 제거한 용어명 (canonical_term 기준, 없으면 raw_term)
    - match_count: 시소러스에서 같은 이름으로 검색된 건수
      0이면 미등재, 1이면 정보 채움, 2 이상이면 동음이의어 (정보 비움)
    - needs_review: 동음이의어인 경우 후보 목록을 문자열로 기록 (검수용), 그 외에는 None
    - 추가 컬럼: term_id, hanja, era, thesaurus_category, description, needs_review
    - 유일 매칭이면 canonical_term을 시소러스 표기(term_name)로 덮어써 표기를 통일
    """
    thesaurus = pd.read_csv(thesaurus_path, encoding="utf-8")
    thesaurus["match_key"] = thesaurus["term_name"].str.replace(" ", "", regex=False)
    grouped = thesaurus.groupby("match_key")

    matched_rows = []
    for row in term_df.itertuples():
        key = row.canonical_term.replace(" ", "")
        result = {
            "canonical_term": None,
            "term_id": None,
            "hanja": None,
            "era": None,
            "thesaurus_category": None,
            "description": None,
            "match_count": 0,
            "needs_review": None,
        }

        if key in grouped.groups:
            candidates = grouped.get_group(key)
            result["match_count"] = len(candidates)
            if len(candidates) == 1:
                hit = candidates.iloc[0]
                result["canonical_term"] = hit["term_name"]
                result["term_id"] = hit["term_id"]
                result["hanja"] = hit["term_ch"]
                result["era"] = hit["term_times"]
                result["thesaurus_category"] = hit["term_lk"]
                result["description"] = hit["term_desc"]
            elif len(candidates) > 1:
                summaries = [
                    f"[term_id {c.term_id}] {c.term_ch} | {c.term_times} | {c.term_desc}"
                    for c in candidates.itertuples()
                ]
                result["needs_review"] = " ;; ".join(summaries)
        matched_rows.append(result)

    matched_df = pd.DataFrame(matched_rows, index=term_df.index)
    combined = pd.concat([term_df, matched_df.drop(columns=["canonical_term"])], axis=1)
    overwrite = matched_df["canonical_term"].notna()
    combined.loc[overwrite, "canonical_term"] = matched_df.loc[overwrite, "canonical_term"]
    return combined


if __name__ == "__main__":
    parser = ArgumentParser(description="추출된 용어를 한국역사용어시소러스와 대조")
    parser.add_argument("terms_csv", help="count_terms 결과 csv 경로")
    parser.add_argument("thesaurus_csv", help="한국역사용어시소러스 csv 경로")
    parser.add_argument("--output", default="", help="매칭 결과를 저장할 csv 경로")
    cli_args = parser.parse_args()

    term_df = pd.read_csv(cli_args.terms_csv, encoding="utf-8-sig")
    result_df = match_thesaurus(term_df, cli_args.thesaurus_csv)

    total = len(result_df)
    unique_hit = (result_df["match_count"] == 1).sum()
    ambiguous = (result_df["match_count"] > 1).sum()
    missing = (result_df["match_count"] == 0).sum()
    print(f"전체 {total}개 용어 | 매칭 {unique_hit} | 동음이의 {ambiguous} | 미등재 {missing}")

    if cli_args.output:
        result_df.to_csv(cli_args.output, index=False, encoding="utf-8-sig")
        print(f"저장 완료: {cli_args.output}")

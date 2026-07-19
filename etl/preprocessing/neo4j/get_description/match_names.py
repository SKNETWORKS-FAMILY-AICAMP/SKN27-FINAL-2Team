import sys
from argparse import ArgumentParser
from json import loads
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent / "terms"))

from prep_thesaurus import build_match_key


def load_encyclopedia_headwords(articles_list_path: str) -> pd.DataFrame:
    """
    민족문화대백과사전 articles_list.jsonl에서 eid와 표제어(headword)만 읽는 함수
    """
    rows = []
    with open(articles_list_path, "r", encoding="utf-8") as source:
        for line in source:
            article = loads(line)
            rows.append({"eid": article["eid"], "headword": article["headword"]})
    return pd.DataFrame(rows)


def match_names(
    extracted_csv: str,
    thesaurus_csv: str,
    articles_list_path: str,
) -> pd.DataFrame:
    """
    추출 용어를 시소러스 term_name·백과사전 headword와 이름만으로 매칭하는 함수
    매칭 키: build_match_key (문장부호 제거 + 한 자리 숫자 한글화)
    추가 컬럼:
    - in_thesaurus / in_encyclopedia: 이름 일치 여부
    - thesaurus_term_ids / encyclopedia_eids: 일치한 항목 id 목록 (;로 연결)
    - match_status: both / thesaurus_only / encyclopedia_only / none
    """
    extracted = pd.read_csv(extracted_csv, encoding="utf-8-sig")
    extracted["match_key"] = extracted["canonical_term"].map(build_match_key)

    thesaurus = pd.read_csv(thesaurus_csv, encoding="utf-8")
    thesaurus["match_key"] = thesaurus["term_name"].map(build_match_key)
    thesaurus_ids = thesaurus.groupby("match_key")["term_id"].agg(
        lambda ids: ";".join(map(str, ids))
    )

    encyclopedia = load_encyclopedia_headwords(articles_list_path)
    encyclopedia["match_key"] = encyclopedia["headword"].map(build_match_key)
    encyclopedia_ids = encyclopedia.groupby("match_key")["eid"].agg(";".join)

    extracted["thesaurus_term_ids"] = extracted["match_key"].map(thesaurus_ids)
    extracted["encyclopedia_eids"] = extracted["match_key"].map(encyclopedia_ids)
    extracted["in_thesaurus"] = extracted["thesaurus_term_ids"].notna()
    extracted["in_encyclopedia"] = extracted["encyclopedia_eids"].notna()

    statuses = []
    for row in extracted.itertuples():
        status = "none"
        if row.in_thesaurus and row.in_encyclopedia:
            status = "both"
        elif row.in_thesaurus:
            status = "thesaurus_only"
        elif row.in_encyclopedia:
            status = "encyclopedia_only"
        statuses.append(status)
    extracted["match_status"] = statuses
    return extracted


if __name__ == "__main__":
    parser = ArgumentParser(description="추출 용어를 시소러스·백과사전과 이름만으로 매칭")
    parser.add_argument("extracted_csv", help="용어 집계 csv 경로 (exam_history_terms.csv)")
    parser.add_argument("thesaurus_csv", help="한국역사용어시소러스 csv 경로")
    parser.add_argument("articles_list", help="백과사전 articles_list.jsonl 경로")
    parser.add_argument("--output", default="", help="매칭 결과 csv 저장 경로")
    cli_args = parser.parse_args()

    result = match_names(
        cli_args.extracted_csv,
        cli_args.thesaurus_csv,
        cli_args.articles_list,
    )

    total = len(result)
    print(f"전체 {total}개 용어")
    print(result["match_status"].value_counts().to_string())
    covered = result["match_status"].ne("none").sum()
    print(f"이름 매칭 커버리지: {covered / total * 100:.1f}%")

    if cli_args.output:
        result.to_csv(cli_args.output, index=False, encoding="utf-8-sig")
        print(f"저장 완료: {cli_args.output}")

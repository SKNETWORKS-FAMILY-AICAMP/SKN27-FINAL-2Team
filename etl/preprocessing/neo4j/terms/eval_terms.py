import sys
from argparse import ArgumentParser
from json import load
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from get_history_terms import get_history_terms
from prep_json import prep_json


def evaluate(golden_path: str, json_path: str) -> pd.DataFrame:
    """
    골든셋(정답 용어 목록)과 LLM 추출 결과를 비교해 문항별 정밀도/재현율을 계산하는 함수
    - golden_path: [{"problem_id": ..., "terms": [...]}] 형식의 json
    - 비교는 공백을 제거한 표기 기준 (raw_term과 대조)
    """
    golden = load(open(golden_path, "r", encoding="utf-8"))
    golden_ids = [entry["problem_id"] for entry in golden]

    df = prep_json(json_path)
    subset = df[df["problem_id"].isin(golden_ids)]
    problems = [
        {"problem_id": row.problem_id, "full_text": row.full_text}
        for row in subset.itertuples()
    ]
    results = get_history_terms(problems)
    predicted = {
        item["problem_id"]: {term["raw_term"].replace(" ", "") for term in item["terms"]}
        for item in results
    }

    rows = []
    for entry in golden:
        expected = {term.replace(" ", "") for term in entry["terms"]}
        extracted = predicted.get(entry["problem_id"], set())
        correct = expected & extracted

        precision = 0.0
        recall = 0.0
        if extracted:
            precision = len(correct) / len(extracted)
        if expected:
            recall = len(correct) / len(expected)

        rows.append({
            "problem_id": entry["problem_id"],
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "missed": sorted(expected - extracted),
            "extra": sorted(extracted - expected),
        })

    report = pd.DataFrame(rows)
    return report


if __name__ == "__main__":
    parser = ArgumentParser(description="골든셋 기준 용어 추출 정확도 평가")
    parser.add_argument("golden_path", help="골든셋 json 경로")
    parser.add_argument("json_path", help="기출문제 json 파일 경로")
    cli_args = parser.parse_args()

    report = evaluate(cli_args.golden_path, cli_args.json_path)
    pd.set_option("display.max_colwidth", 120)
    print(report.to_string())
    print(f"평균 정밀도: {report['precision'].mean():.3f}")
    print(f"평균 재현율: {report['recall'].mean():.3f}")

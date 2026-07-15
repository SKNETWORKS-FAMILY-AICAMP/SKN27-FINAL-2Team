"""ML 평가용 split 파일을 생성하는 스크립트입니다.
시간 기반 split과 era+topic_train 조합 기반 stratified split을 함께 만듭니다.
생성된 split은 era, 원본 topic, 통합 topic_train 평가에 공통으로 사용합니다.

Run:
  python ai/ml/make_eval_splits_v1.py

Run with custom paths:
  python ai/ml/make_eval_splits_v1.py --input INPUT_JSON --output-dir OUTPUT_DIR
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ML_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_JSON = ML_DIR / "output" / "split_topic_merged_v1" / "full_features_topic_merged_v1.json"
DEFAULT_OUTPUT_DIR = ML_DIR / "output" / "eval_splits_v1"

RANDOM_STATE = 42
TEST_RATIO = 0.25

OUTPUT_COLUMNS = [
    "ml_sequence_index",
    "split",
    "eval_split",
    "round_no",
    "question_no",
    "problem_id",
    "data_source",
    "input_text",
    "keywords",
    "text",
    "era",
    "topic",
    "topic_train",
    "question_type",
    "question_subtype",
    "core_concept",
    "stratify_key",
]


# JSON 피처 파일을 읽어 split 생성의 기준 데이터로 사용합니다.
# 입력 데이터에는 era, topic, topic_train, text가 모두 포함되어 있어야 합니다.
# 누락된 핵심 컬럼이 있으면 학습 단계에서 오류가 나기 때문에 먼저 검사합니다.
def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"input file not found: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError(f"input file is empty: {path}")

    required = {"era", "topic", "topic_train", "text", "split"}
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    return rows


# 문자열 라벨을 집계와 stratify key에 안전하게 사용할 수 있도록 정리합니다.
# 실제 row의 원본 값은 바꾸지 않고 split 계산용 문자열만 변환합니다.
# 결측값은 리포트에서 확인되도록 '(missing)'으로 표시합니다.
def label_value(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "(missing)"


# 시대와 통합 주제를 묶어 층화추출 기준 키를 만듭니다.
# 주제 단독이 아니라 시대+주제 조합을 유지해야 평가셋이 더 현실적인 분포를 가집니다.
# 예: 조선 / 정치, 고려 / 문화
def build_stratify_key(row: dict[str, Any]) -> str:
    return f"{label_value(row.get('era'))} / {label_value(row.get('topic_train'))}"


# row를 출력용 형태로 복사하고 eval_split, stratify_key를 추가합니다.
# 기존 split 컬럼은 원래 시간 기반 train/test 정보를 보존하는 용도로 남깁니다.
# 새 eval_split은 이번 평가셋에서 train/test 중 어디에 속하는지 표시합니다.
def output_row(row: dict[str, Any], eval_split: str, stratify_key: str = "") -> dict[str, Any]:
    output = dict(row)
    output["eval_split"] = eval_split
    output["stratify_key"] = stratify_key
    return output


# 기존 47~70 train, 71~78 test 기준의 시간 기반 split을 생성합니다.
# 이 split은 실제 최신 회차 예측 목적과 가장 가까운 평가셋입니다.
# 원본 split 값을 그대로 사용하므로 기존 실험과 비교하기 쉽습니다.
def make_time_split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train_rows: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []

    for row in rows:
        original_split = label_value(row.get("split"))
        stratify_key = build_stratify_key(row)
        if original_split == "train":
            train_rows.append(output_row(row, "train", stratify_key))
        elif original_split == "test":
            test_rows.append(output_row(row, "test", stratify_key))

    return train_rows, test_rows


# 각 era+topic_train 조합 내부에서 일정 비율을 test로 뽑습니다.
# 조합별 최소 1개 이상 test를 확보하되, 2개짜리 조합은 train/test 1개씩 나눕니다.
# 전체 test 비율은 25%에 가깝게 맞추고, seed를 고정해 재현성을 보장합니다.
def make_stratified_split(
    rows: list[dict[str, Any]],
    *,
    test_ratio: float,
    random_state: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(random_state)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        grouped[build_stratify_key(row)].append(row)

    train_rows: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []

    for stratify_key in sorted(grouped):
        group_rows = list(grouped[stratify_key])
        rng.shuffle(group_rows)

        group_size = len(group_rows)
        if group_size == 1:
            test_size = 0
        else:
            test_size = round(group_size * test_ratio)
            test_size = max(1, test_size)
            test_size = min(group_size - 1, test_size)

        group_test = group_rows[:test_size]
        group_train = group_rows[test_size:]

        train_rows.extend(output_row(row, "train", stratify_key) for row in group_train)
        test_rows.extend(output_row(row, "test", stratify_key) for row in group_test)

    train_rows.sort(key=sort_key)
    test_rows.sort(key=sort_key)
    return train_rows, test_rows


# 문항 순서를 안정적으로 유지하기 위한 정렬 키입니다.
# split 결과가 매번 같은 순서로 저장되어 diff와 검토가 쉬워집니다.
# round_no, question_no가 없으면 ml_sequence_index 기준으로도 정렬될 수 있게 합니다.
def sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    return (
        int(row.get("round_no") or 0),
        int(row.get("question_no") or 0),
        int(row.get("ml_sequence_index") or 0),
    )


# JSON 파일을 보기 좋게 저장합니다.
# ensure_ascii=False를 사용해 한국어 라벨과 문항 텍스트가 깨지지 않도록 합니다.
# RunPod와 로컬에서 같은 파일을 사용할 수 있습니다.
def write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# CSV 파일은 Excel 확인을 위해 utf-8-sig로 저장합니다.
# OUTPUT_COLUMNS에 없는 추가 컬럼은 JSON에는 남고 CSV에서는 제외되어 표 형태를 단순하게 유지합니다.
# 학습용으로는 JSON, 검토용으로는 CSV를 쓰면 됩니다.
def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in OUTPUT_COLUMNS} for row in rows)


# split 하나를 JSON/CSV 파일 쌍으로 저장합니다.
# 폴더 구조를 split별로 분리해 RunPod 업로드와 실험 비교를 쉽게 합니다.
# 반환값은 리포트 작성에 필요한 row 수와 분포 정보입니다.
def write_split_dir(path: Path, train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]]) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    write_json(path / "train.json", train_rows)
    write_json(path / "test.json", test_rows)
    write_csv(path / "train.csv", train_rows)
    write_csv(path / "test.csv", test_rows)

    return {
        "name": path.name,
        "path": path,
        "train_count": len(train_rows),
        "test_count": len(test_rows),
        "train_rows": train_rows,
        "test_rows": test_rows,
    }


# 라벨 분포를 train/test별로 계산합니다.
# era, topic, topic_train의 분포가 split 이후 얼마나 유지되는지 확인하기 위해 사용합니다.
# 리포트에 간단한 표로 출력합니다.
def label_counts(train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]], key: str) -> list[tuple[str, int, int]]:
    train_counter = Counter(label_value(row.get(key)) for row in train_rows)
    test_counter = Counter(label_value(row.get(key)) for row in test_rows)
    labels = sorted(set(train_counter) | set(test_counter))
    return [(label, train_counter[label], test_counter[label]) for label in labels]


# train/test 분포를 Markdown 표로 변환합니다.
# 라벨별 test 비율을 같이 보여줘 특정 라벨이 평가셋에 과하게 들어갔는지 확인할 수 있습니다.
# 전체 분포가 완전히 같을 필요는 없지만 큰 왜곡은 여기서 바로 드러납니다.
def label_table_lines(train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]], key: str) -> list[str]:
    lines = ["| 라벨 | train | test | test 비율 |", "|---|---:|---:|---:|"]
    for label, train_count, test_count in label_counts(train_rows, test_rows, key):
        total = train_count + test_count
        pct = 0 if total == 0 else test_count / total * 100
        lines.append(f"| {label} | {train_count} | {test_count} | {pct:.1f}% |")
    return lines


# stratify key 기준 분포를 요약합니다.
# 통합 주제 조합 split이 각 조합에서 train/test를 모두 확보했는지 확인합니다.
# 1개짜리 조합은 이번 데이터에서는 거의 없지만, 방어적으로 리포트에 표시합니다.
def stratify_summary(train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]]) -> dict[str, int]:
    train_counter = Counter(label_value(row.get("stratify_key")) for row in train_rows)
    test_counter = Counter(label_value(row.get("stratify_key")) for row in test_rows)
    keys = set(train_counter) | set(test_counter)
    return {
        "combo_count": len(keys),
        "train_missing_combo_count": sum(1 for key in keys if train_counter[key] == 0),
        "test_missing_combo_count": sum(1 for key in keys if test_counter[key] == 0),
        "min_train_combo_count": min((train_counter[key] for key in keys), default=0),
        "min_test_combo_count": min((test_counter[key] for key in keys), default=0),
    }


# 생성된 평가셋의 목적과 분포를 Markdown 리포트로 작성합니다.
# 다음 단계에서 어떤 split을 RunPod 학습에 넣을지 판단하는 기준 문서입니다.
# 기존 v1~v5 실험 결과와 분리해 eval_splits_v1 아래에 저장합니다.
def build_report(split_infos: list[dict[str, Any]], *, input_path: Path, output_dir: Path) -> str:
    lines: list[str] = []
    lines.append("# ML Evaluation Splits v1")
    lines.append("")
    lines.append("## 목적")
    lines.append("")
    lines.append("- 시간 기반 평가셋과 `era + topic_train` 조합 기준 층화추출 평가셋을 생성했습니다.")
    lines.append("- 하나의 split 파일로 `era`, 원본 `topic`, 통합 `topic_train`을 모두 평가할 수 있습니다.")
    lines.append("- `question_type`은 현재 평가 대상에서 제외합니다.")
    lines.append("")
    lines.append("## 입력/출력")
    lines.append("")
    lines.append(f"- 입력 파일: `{input_path.as_posix()}`")
    lines.append(f"- 출력 폴더: `{output_dir.as_posix()}`")
    lines.append(f"- random_state: {RANDOM_STATE}")
    lines.append(f"- stratified test ratio: {TEST_RATIO}")
    lines.append("")

    for info in split_infos:
        train_rows = info["train_rows"]
        test_rows = info["test_rows"]
        total = len(train_rows) + len(test_rows)
        test_pct = 0 if total == 0 else len(test_rows) / total * 100
        summary = stratify_summary(train_rows, test_rows)

        lines.append(f"## {info['name']}")
        lines.append("")
        lines.append(f"- 경로: `{Path(info['path']).as_posix()}`")
        lines.append(f"- train: {len(train_rows)}")
        lines.append(f"- test: {len(test_rows)}")
        lines.append(f"- test 비율: {test_pct:.1f}%")
        lines.append(f"- stratify 조합 수: {summary['combo_count']}")
        lines.append(f"- train에 없는 조합 수: {summary['train_missing_combo_count']}")
        lines.append(f"- test에 없는 조합 수: {summary['test_missing_combo_count']}")
        lines.append(f"- 조합별 최소 train 수: {summary['min_train_combo_count']}")
        lines.append(f"- 조합별 최소 test 수: {summary['min_test_combo_count']}")
        lines.append("")

        for key, title in [("era", "era 분포"), ("topic", "원본 topic 분포"), ("topic_train", "통합 topic_train 분포")]:
            lines.append(f"### {title}")
            lines.append("")
            lines.extend(label_table_lines(train_rows, test_rows, key))
            lines.append("")

    lines.append("## 다음 단계")
    lines.append("")
    lines.append("1. RunPod 학습 코드는 이 split 폴더의 `train.json`, `test.json`을 입력으로 받도록 작성합니다.")
    lines.append("2. 먼저 `split_era_topic_train_stratified_v1`로 `topic_train` 성능 개선 실험을 진행합니다.")
    lines.append("3. 같은 split에서 원본 `topic` 모델도 학습해 원본 주제 성능을 함께 확인합니다.")
    lines.append("4. 시간 기반 split은 실제 최신 회차 예측 성능 비교용으로 유지합니다.")
    lines.append("")
    return "\n".join(lines)


# CLI 인자를 정의합니다.
# 기본값은 로컬 프로젝트 경로지만, RunPod에서는 --input과 --output-dir만 바꿔 그대로 실행할 수 있습니다.
# 외부 라이브러리를 쓰지 않아 환경 차이에 덜 민감합니다.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create ML evaluation split files.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_JSON, help="Input full feature JSON path.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output split directory.")
    parser.add_argument("--test-ratio", type=float, default=TEST_RATIO, help="Test ratio for stratified split.")
    parser.add_argument("--random-state", type=int, default=RANDOM_STATE, help="Random seed.")
    return parser.parse_args()


# 전체 평가셋 생성 작업을 실행합니다.
# 기존 split_topic_merged_v1 파일은 수정하지 않고 eval_splits_v1 아래에 새 파일만 생성합니다.
# 생성 후 리포트 경로를 출력해 바로 확인할 수 있게 합니다.
def main() -> None:
    args = parse_args()
    rows = read_rows(args.input)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    time_train, time_test = make_time_split(rows)
    strat_train, strat_test = make_stratified_split(
        rows,
        test_ratio=args.test_ratio,
        random_state=args.random_state,
    )

    split_infos = [
        write_split_dir(args.output_dir / "split_time_v1", time_train, time_test),
        write_split_dir(args.output_dir / "split_era_topic_train_stratified_v1", strat_train, strat_test),
    ]

    report_path = args.output_dir / "split_report_v1.md"
    report_path.write_text(
        build_report(split_infos, input_path=args.input, output_dir=args.output_dir),
        encoding="utf-8",
    )

    print(f"output_dir: {args.output_dir}")
    print(f"report: {report_path}")
    for info in split_infos:
        print(f"{info['name']}: train={info['train_count']} test={info['test_count']}")


if __name__ == "__main__":
    main()

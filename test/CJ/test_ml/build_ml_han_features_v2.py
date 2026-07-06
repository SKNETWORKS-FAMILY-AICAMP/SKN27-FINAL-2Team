"""
Build v2 ML feature data from ML_han_v1.json.

v2 keeps the existing v1 labels and adds question_type_v2, which is assigned
by checking narrow/rare question patterns before the broad data-analysis type.

Run:
  python test/CJ/test_ml/build_ml_han_features_v2.py
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from build_ml_han_features_v1 import build_rows


ROOT_DIR = Path(__file__).resolve().parents[3]
ML_DIR = ROOT_DIR / "test" / "CJ" / "test_ml"
INPUT_JSON = ML_DIR / "ML_han_v1.json"
OUT_DIR = ML_DIR / "output"

OUTPUT_JSON = OUT_DIR / "ml_han_features_v2.json"
OUTPUT_CSV = OUT_DIR / "ml_han_features_v2.csv"
REPORT_JSON = OUT_DIR / "ml_han_features_v2_report.json"
REPORT_MD = OUT_DIR / "ML_feature_v2.md"

QUESTION_TYPE_VALUES = [
    "역사 지식의 이해",
    "연대기의 파악",
    "역사 상황 및 쟁점의 인식",
    "역사 자료의 분석 및 해석",
    "역사 탐구의 설계 및 수행",
    "결론의 도출 및 평가",
]

FIELDNAMES = [
    "ml_sequence_index",
    "split",
    "round_no",
    "question_no",
    "problem_id",
    "data_source",
    "input_text",
    "keywords",
    "era",
    "topic",
    "question_type",
    "question_type_v2",
    "question_type_v2_rule",
    "question_subtype",
    "core_concept",
]


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def contains_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def infer_question_type_v2(source_item: dict) -> tuple[str, str]:
    """Assign narrow/rare types first, broad data analysis last."""
    minor_type = normalize_text(source_item.get("minor_type"))
    task = normalize_text(source_item.get("question_task"))
    question = normalize_text(source_item.get("question"))
    material = normalize_text(source_item.get("material"))
    text = f"{material}\n{question}"

    if minor_type in {"의의·영향·결과 평가", "비교·공통점 도출"}:
        return "결론의 도출 및 평가", f"minor_type={minor_type}"

    if task == "multi_select_combo" or minor_type == "보기 조합 판단":
        return "결론의 도출 및 평가", "question_task=multi_select_combo 또는 보기 조합 판단"

    if minor_type in {"탐구 주제·활동 선정", "자료 수집·검색 방법"}:
        return "역사 탐구의 설계 및 수행", f"minor_type={minor_type}"

    if contains_any(question, ["탐구", "조사", "검색", "수집"]):
        return "역사 탐구의 설계 및 수행", "question_text=탐구/조사/검색/수집"

    if minor_type in {"기본 사실·개념 확인", "제도·기관·정책 기능 이해"}:
        return "역사 지식의 이해", f"minor_type={minor_type}"

    if minor_type in {"전후 시기 판단", "사건·자료 순서 배열", "연표·흐름 빈칸"}:
        return "연대기의 파악", f"minor_type={minor_type}"

    if task in {"order", "timeline_position", "period_between"}:
        return "연대기의 파악", f"question_task={task}"

    if task == "negative_select":
        return "역사 상황 및 쟁점의 인식", "question_task=negative_select"

    if contains_any(question, ["옳지 않은", "아닌 것", "잘못된"]):
        return "역사 상황 및 쟁점의 인식", "question_text=부정형 선택"

    return "역사 자료의 분석 및 해석", "fallback=자료 기반 해석"


def count_by(rows: list[dict], field: str, split: str | None = None) -> Counter:
    items = rows if split is None else [row for row in rows if row.get("split") == split]
    return Counter(normalize_text(row.get(field)) or "(blank)" for row in items)


def pct(value: int, total: int) -> float:
    return 0.0 if total == 0 else value / total * 100


def normalized_entropy(counts: Counter) -> float:
    values = [value for value in counts.values() if value > 0]
    if len(values) <= 1:
        return 0.0
    total = sum(values)
    entropy = -sum((value / total) * math.log(value / total) for value in values)
    return entropy / math.log(len(values))


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDNAMES} for row in rows)


def sorted_counts_dict(counter: Counter) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def add_question_type_v2(rows: list[dict]) -> list[dict]:
    source_rows = read_json(INPUT_JSON, [])
    if len(rows) != len(source_rows):
        raise ValueError(f"row count mismatch: v1={len(rows)}, source={len(source_rows)}")

    output: list[dict] = []
    for row, source_item in zip(rows, source_rows):
        label, rule = infer_question_type_v2(source_item)
        updated = dict(row)
        updated["question_type_v2"] = label
        updated["question_type_v2_rule"] = rule
        output.append(updated)
    return output


def append_distribution_table(md: list[str], title: str, counts: Counter) -> None:
    total = sum(counts.values())
    md.append(f"## {title}")
    md.append("")
    md.append("| 라벨 | 건수 | 비율 |")
    md.append("|---|---:|---:|")
    for label, count in counts.most_common():
        md.append(f"| {label} | {count} | {pct(count, total):.1f}% |")
    md.append("")


def build_markdown(rows: list[dict]) -> str:
    train_v1 = count_by(rows, "question_type", "train")
    train_v2 = count_by(rows, "question_type_v2", "train")
    test_v2 = count_by(rows, "question_type_v2", "test")
    overall_v2 = count_by(rows, "question_type_v2")

    def summary_row(name: str, counts: Counter) -> str:
        total = sum(counts.values())
        top_label, top_count = counts.most_common(1)[0]
        min_count = min(counts.values())
        ratio = top_count / min_count if min_count else 0
        return (
            f"| {name} | {len(counts)} | {top_label} | {top_count} | "
            f"{pct(top_count, total):.1f}% | {min_count} | {ratio:.2f} | "
            f"{normalized_entropy(counts):.3f} |"
        )

    md: list[str] = []
    md.append("# ML_feature_v2")
    md.append("")
    md.append("- 기준 데이터: `test/CJ/test_ml/ML_han_v1.json`")
    md.append("- 생성 피처: `test/CJ/test_ml/output/ml_han_features_v2.json`")
    md.append("- 추가 컬럼: `question_type_v2`, `question_type_v2_rule`")
    md.append("- 목적: 문제 유형을 배정할 때 낮은 비율의 구체 유형을 먼저 확인하고, 넓은 `역사 자료의 분석 및 해석`은 마지막 fallback으로 배정")
    md.append("- 주의: `question_type_v2`는 실험용 라벨입니다. 특히 `negative_select -> 역사 상황 및 쟁점의 인식` 규칙은 샘플 검토가 필요합니다.")
    md.append("")
    md.append("## 1. v2 우선순위 규칙")
    md.append("")
    md.append("| 우선순위 | 조건 | 배정 라벨 |")
    md.append("|---:|---|---|")
    md.append("| 1 | `의의·영향·결과 평가`, `비교·공통점 도출`, `multi_select_combo`, `보기 조합 판단` | 결론의 도출 및 평가 |")
    md.append("| 2 | `탐구 주제·활동 선정`, `자료 수집·검색 방법`, 질문문에 탐구/조사/검색/수집 포함 | 역사 탐구의 설계 및 수행 |")
    md.append("| 3 | `기본 사실·개념 확인`, `제도·기관·정책 기능 이해` | 역사 지식의 이해 |")
    md.append("| 4 | `전후 시기 판단`, `사건·자료 순서 배열`, `연표·흐름 빈칸`, 순서/연표/전후 task | 연대기의 파악 |")
    md.append("| 5 | `negative_select`, 질문문 부정형 | 역사 상황 및 쟁점의 인식 |")
    md.append("| 6 | 위 조건에 해당하지 않는 자료 기반 문제 | 역사 자료의 분석 및 해석 |")
    md.append("")
    md.append("## 2. 요약 지표")
    md.append("")
    md.append("| 기준 | 클래스 수 | 최다 라벨 | 최다 건수 | 최다 비율 | 최소 건수 | 최대/최소 비율 | 정규화 엔트로피 |")
    md.append("|---|---:|---|---:|---:|---:|---:|---:|")
    md.append(summary_row("v1 train question_type", train_v1))
    md.append(summary_row("v2 train question_type_v2", train_v2))
    md.append(summary_row("v2 test question_type_v2", test_v2))
    md.append(summary_row("v2 overall question_type_v2", overall_v2))
    md.append("")

    append_distribution_table(md, "3. v2 Train 기준 상세 분포", train_v2)
    append_distribution_table(md, "4. 기존 v1 Train question_type 분포", train_v1)
    append_distribution_table(md, "5. v2 Test 참고 분포", test_v2)

    md.append("## 6. 해석")
    md.append("")
    md.append("- v2에서는 `역사 자료의 분석 및 해석`을 마지막에 배정했기 때문에 train 기준 비율이 낮아지는지 확인할 수 있습니다.")
    md.append("- `결론의 도출 및 평가`, `역사 상황 및 쟁점의 인식`처럼 낮은 비율의 유형이 늘어나면 모델이 학습할 클래스가 조금 더 분산됩니다.")
    md.append("- 다만 이 방식은 규칙 기반 재라벨링이므로, 실제 의미가 맞는지 각 규칙별 샘플 검토가 필요합니다.")
    md.append("- 특히 `negative_select`는 단순히 '옳지 않은 것'을 고르는 형식일 수 있으므로, 정말 `역사 상황 및 쟁점의 인식`으로 볼 수 있는지 확인해야 합니다.")
    md.append("")
    return "\n".join(md) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = add_question_type_v2(build_rows())

    OUTPUT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(OUTPUT_CSV, rows)

    report = {
        "input": INPUT_JSON.relative_to(ROOT_DIR).as_posix(),
        "outputs": {
            "json": OUTPUT_JSON.relative_to(ROOT_DIR).as_posix(),
            "csv": OUTPUT_CSV.relative_to(ROOT_DIR).as_posix(),
            "md": REPORT_MD.relative_to(ROOT_DIR).as_posix(),
        },
        "total_rows": len(rows),
        "split_counts": sorted_counts_dict(count_by(rows, "split")),
        "question_type_v1_train_counts": sorted_counts_dict(count_by(rows, "question_type", "train")),
        "question_type_v2_train_counts": sorted_counts_dict(count_by(rows, "question_type_v2", "train")),
        "question_type_v2_test_counts": sorted_counts_dict(count_by(rows, "question_type_v2", "test")),
        "question_type_v2_overall_counts": sorted_counts_dict(count_by(rows, "question_type_v2")),
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT_MD.write_text(build_markdown(rows), encoding="utf-8")

    print(
        json.dumps(
            {
                "rows": len(rows),
                "output_json": report["outputs"]["json"],
                "output_csv": report["outputs"]["csv"],
                "report_md": report["outputs"]["md"],
                "question_type_v2_train_counts": report["question_type_v2_train_counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

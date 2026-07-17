"""
Build review artifacts for Korean history topic labels.

This script keeps the existing labels unchanged and creates a human review
table for misclassified samples. It is intended to refine labeling criteria
before changing data or model logic.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PREDICTIONS_CSV = Path(
    r"C:\Users\Playdata\Downloads\klue_eval_with_core_v3\klue_eval_with_core_v3"
    r"\topic_train_era_topic_train_stratified_v1"
    r"\topic_train_split_era_topic_train_stratified_v1_predictions.csv"
)
DEFAULT_SPLIT_CSV = (
    ROOT_DIR
    / "ai"
    / "ml"
    / "output"
    / "eval_splits_with_core_v1"
    / "split_era_topic_train_stratified_v1"
    / "test.csv"
)
DEFAULT_OUTPUT_DIR = ROOT_DIR / "ai" / "ml" / "output" / "topic_label_review_v1"

LABEL_CRITERIA = [
    {
        "label": "문화",
        "definition": "사상, 종교, 학문, 예술, 문화재, 생활 문화처럼 문화 요소가 정답 판단의 중심인 문제",
        "positive": "불교 수용, 문화재 특징, 사상가의 사상 내용, 예술 양식, 서적과 학문",
        "borderline": "인물이 등장해도 핵심이 업적보다 사상/문화 내용이면 문화",
    },
    {
        "label": "사건",
        "definition": "전쟁, 반란, 운동, 조약, 개혁, 선언 등 특정 사건의 원인, 전개, 결과, 순서가 중심인 문제",
        "positive": "임진왜란 전개, 3.1 운동, 갑신정변 결과, 사건 순서 배열",
        "borderline": "정책이나 인물이 등장해도 사건의 흐름과 결과를 묻는다면 사건",
    },
    {
        "label": "인물",
        "definition": "특정 인물의 업적, 활동, 정책, 발언, 저술, 관련 자료가 정답 판단의 중심인 문제",
        "positive": "세종의 업적, 정도전의 활동, 독립운동가의 활동, 왕의 정책 비교",
        "borderline": "정책 자체보다 그 정책을 추진한 인물 식별이 핵심이면 인물",
    },
    {
        "label": "정치",
        "definition": "권력 구조, 왕권/정권 운영, 통치 체제, 중앙/지방 정치 운영, 대외 정책 방향이 중심인 문제",
        "positive": "왕권 강화, 붕당 정치, 정권 교체, 통치 방향, 대외 정책 노선",
        "borderline": "구체적인 법, 수취, 관직, 행정 제도 자체를 묻는다면 제도를 우선 검토",
    },
    {
        "label": "제도",
        "definition": "법, 행정, 수취, 토지, 신분, 관직, 교육, 군역 등 구조화된 제도 자체가 중심인 문제",
        "positive": "대동법, 과거제, 토지 제도, 신분 제도, 중앙/지방 관제, 군역 제도",
        "borderline": "제도가 정치 운영의 배경으로만 등장하고 핵심이 권력 운영이면 정치",
    },
]

PRIORITY_RULES = [
    "구체적인 제도명이나 제도 운용 방식이 정답 결정의 핵심이면 '제도'를 우선한다.",
    "특정 사건의 원인, 전개, 결과, 순서가 핵심이면 '사건'을 우선한다.",
    "특정 인물의 활동, 업적, 발언, 저술을 식별하는 문제면 '인물'을 우선한다.",
    "문화재, 사상, 종교, 예술, 학문 내용이 핵심이면 '문화'를 우선한다.",
    "위 항목으로 좁혀지지 않고 권력 구조, 통치 운영, 정책 방향이 핵심이면 '정치'로 본다.",
]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """
    Read a UTF-8 CSV file into dictionaries.

    RunPod and Windows Excel often create UTF-8 files with or without BOM, so
    utf-8-sig is used to keep Korean text stable.
    """
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    """
    Write rows as UTF-8 with BOM for Excel-friendly Korean display.

    Missing fields are written as blank values so review columns can be added
    without failing on older prediction files.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def normalize_bool(value: str) -> bool:
    """
    Convert prediction CSV correctness values into booleans.

    The notebook writes True/False strings, but this helper also accepts common
    lowercase forms for reuse with future outputs.
    """
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def truncate_text(value: str, limit: int = 260) -> str:
    """
    Keep review tables readable by shortening long text fields.

    The full text remains available in the source split CSV, while this output
    focuses on fast manual label inspection.
    """
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def build_label_criteria_markdown() -> str:
    """
    Build a Markdown criteria table for the current topic labels.

    This is a review guide only; it does not rename or overwrite existing
    labels in the dataset.
    """
    lines = [
        "# 통합 주제 라벨 기준표 v1",
        "",
        "## 목적",
        "",
        "- 기존 라벨명은 유지한다.",
        "- 모델 성능이 낮은 원인을 파악하기 위해 라벨 판단 기준을 더 명확히 한다.",
        "- 특히 `정치`, `제도`, `사건`, `인물` 사이의 경계가 흔들리는 문제를 우선 검토한다.",
        "",
        "## 라벨 기준",
        "",
        "| 라벨 | 판단 기준 | 대표 예시 | 경계 사례 |",
        "|---|---|---|---|",
    ]
    for item in LABEL_CRITERIA:
        lines.append(
            f"| {item['label']} | {item['definition']} | {item['positive']} | {item['borderline']} |"
        )

    lines.extend(
        [
            "",
            "## 우선순위 규칙",
            "",
        ]
    )
    for idx, rule in enumerate(PRIORITY_RULES, start=1):
        lines.append(f"{idx}. {rule}")

    lines.extend(
        [
            "",
            "## 이번 검토에서 볼 것",
            "",
            "- `actual=정치`인데 `사건`, `인물`, `제도`로 예측된 문제를 먼저 본다.",
            "- `actual=제도`인데 `정치` 또는 `인물`로 예측된 문제를 본다.",
            "- 라벨 자체가 애매한 문제는 `suggested_label`에 추천 라벨을 적고, 기존 라벨 유지가 맞으면 `유지`라고 적는다.",
            "- 기준표만 보완할 문제인지, 실제 데이터 라벨 수정이 필요한 문제인지 구분한다.",
            "",
        ]
    )
    return "\n".join(lines)


def index_split_rows(split_rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """
    Build lookup data from the local split CSV.

    The prediction file and split file share ml_sequence_index, so this key is
    used to recover core_concept, question type, and full input text.
    """
    return {row.get("ml_sequence_index", ""): row for row in split_rows}


def enrich_prediction_rows(
    prediction_rows: list[dict[str, str]], split_by_index: dict[str, dict[str, str]]
) -> list[dict[str, str]]:
    """
    Merge prediction rows with source feature metadata.

    The output intentionally includes empty manual review columns so a human can
    mark whether the original label should be kept or reconsidered.
    """
    enriched = []
    for pred in prediction_rows:
        source = split_by_index.get(pred.get("ml_sequence_index", ""), {})
        true_label = pred.get("true_label") or source.get("topic_train", "")
        pred_label = pred.get("pred_label", "")
        row = {
            "review_status": "",
            "suggested_label": "",
            "review_note": "",
            "ml_sequence_index": pred.get("ml_sequence_index", ""),
            "round_no": pred.get("round_no") or source.get("round_no", ""),
            "question_no": pred.get("question_no") or source.get("question_no", ""),
            "problem_id": pred.get("problem_id") or source.get("problem_id", ""),
            "actual_label": true_label,
            "predicted_label": pred_label,
            "era": pred.get("era") or source.get("era", ""),
            "original_topic": pred.get("topic") or source.get("topic", ""),
            "topic_train": pred.get("topic_train") or source.get("topic_train", ""),
            "question_type": source.get("question_type", ""),
            "question_subtype": source.get("question_subtype", ""),
            "core_concept": source.get("core_concept", ""),
            "text_preview": truncate_text(source.get("text_with_core") or pred.get("text", "")),
            "is_correct": pred.get("is_correct", ""),
        }
        enriched.append(row)
    return enriched


def select_priority_misclassifications(rows: list[dict[str, str]], limit_per_pair: int) -> list[dict[str, str]]:
    """
    Select the highest-priority misclassified samples for manual review.

    Politics is the current bottleneck, and institution-vs-politics confusion is
    also important, so those rows are placed first.
    """
    wrong = [row for row in rows if not normalize_bool(row.get("is_correct", ""))]
    priority_pairs = [
        ("정치", "사건"),
        ("정치", "인물"),
        ("정치", "제도"),
        ("정치", "문화"),
        ("제도", "정치"),
        ("제도", "인물"),
        ("사건", "정치"),
        ("인물", "정치"),
    ]

    selected = []
    selected_keys = set()
    for actual, predicted in priority_pairs:
        pair_rows = [
            row
            for row in wrong
            if row.get("actual_label") == actual and row.get("predicted_label") == predicted
        ]
        pair_rows.sort(key=lambda row: (int(row.get("round_no") or 0), int(row.get("question_no") or 0)))
        for row in pair_rows[:limit_per_pair]:
            key = row.get("ml_sequence_index")
            selected.append(row)
            selected_keys.add(key)

    remaining = [row for row in wrong if row.get("ml_sequence_index") not in selected_keys]
    remaining.sort(key=lambda row: (row.get("actual_label", ""), row.get("predicted_label", ""), int(row.get("round_no") or 0), int(row.get("question_no") or 0)))
    selected.extend(remaining[: max(0, 80 - len(selected))])
    return selected


def build_review_markdown(rows: list[dict[str, str]], selected_rows: list[dict[str, str]]) -> str:
    """
    Summarize misclassification patterns for fast team review.

    The Markdown report points reviewers to the CSV when they need to inspect
    individual samples and add suggested labels.
    """
    total = len(rows)
    wrong = [row for row in rows if not normalize_bool(row.get("is_correct", ""))]
    pair_counts = Counter((row["actual_label"], row["predicted_label"]) for row in wrong)
    actual_counts = Counter(row["actual_label"] for row in rows)
    actual_wrong_counts = Counter(row["actual_label"] for row in wrong)

    lines = [
        "# 통합 주제 오분류 검토 v1",
        "",
        "## 요약",
        "",
        f"- 전체 평가 샘플: {total}건",
        f"- 오분류 샘플: {len(wrong)}건",
        f"- 우선 검토 샘플 CSV: `topic_misclassification_priority_v1.csv`",
        f"- 전체 오분류 CSV: `topic_misclassification_all_v1.csv`",
        "",
        "## 라벨별 오분류 현황",
        "",
        "| 실제 라벨 | 전체 건수 | 오분류 건수 | 오분류 비율 |",
        "|---|---:|---:|---:|",
    ]
    for label in sorted(actual_counts):
        total_count = actual_counts[label]
        wrong_count = actual_wrong_counts[label]
        rate = wrong_count / total_count * 100 if total_count else 0
        lines.append(f"| {label} | {total_count} | {wrong_count} | {rate:.1f}% |")

    lines.extend(
        [
            "",
            "## 주요 혼동 패턴",
            "",
            "| 실제 라벨 | 예측 라벨 | 건수 |",
            "|---|---|---:|",
        ]
    )
    for (actual, predicted), count in pair_counts.most_common(15):
        lines.append(f"| {actual} | {predicted} | {count} |")

    lines.extend(
        [
            "",
            "## 우선 검토 샘플",
            "",
            "| 회차 | 번호 | 실제 | 예측 | 시대 | 원래 주제 | 핵심 개념 | 텍스트 미리보기 |",
            "|---:|---:|---|---|---|---|---|---|",
        ]
    )
    for row in selected_rows[:30]:
        lines.append(
            "| {round_no} | {question_no} | {actual_label} | {predicted_label} | {era} | {original_topic} | {core_concept} | {text_preview} |".format(
                **{key: str(value).replace("|", "/") for key, value in row.items()}
            )
        )

    lines.extend(
        [
            "",
            "## 검토 방법",
            "",
            "1. `topic_misclassification_priority_v1.csv`를 먼저 열어 `suggested_label`을 채운다.",
            "2. 기존 라벨이 맞으면 `review_status`에 `유지`를 적는다.",
            "3. 라벨 수정이 필요하면 `review_status`에 `수정 후보`를 적고 `review_note`에 이유를 적는다.",
            "4. 여러 사람이 같은 기준으로 판단할 수 있도록 기준표의 우선순위 규칙을 먼저 적용한다.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    """
    Parse CLI arguments for reusable local or RunPod outputs.

    The defaults point to the latest local core_v3 result and the matching
    split file used in the current review.
    """
    parser = argparse.ArgumentParser(description="Build topic label criteria and misclassification review files.")
    parser.add_argument("--predictions-csv", type=Path, default=DEFAULT_PREDICTIONS_CSV)
    parser.add_argument("--split-csv", type=Path, default=DEFAULT_SPLIT_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit-per-pair", type=int, default=12)
    return parser.parse_args()


def main() -> None:
    """
    Generate label criteria and misclassification review artifacts.

    No training data is modified. The script only reads prediction/split CSVs
    and writes review files under the output directory.
    """
    args = parse_args()
    if not args.predictions_csv.exists():
        raise FileNotFoundError(f"predictions csv not found: {args.predictions_csv}")
    if not args.split_csv.exists():
        raise FileNotFoundError(f"split csv not found: {args.split_csv}")

    prediction_rows = read_csv_rows(args.predictions_csv)
    split_rows = read_csv_rows(args.split_csv)
    enriched_rows = enrich_prediction_rows(prediction_rows, index_split_rows(split_rows))
    wrong_rows = [row for row in enriched_rows if not normalize_bool(row.get("is_correct", ""))]
    selected_rows = select_priority_misclassifications(enriched_rows, args.limit_per_pair)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    criteria_path = args.output_dir / "topic_label_criteria_v1.md"
    review_md_path = args.output_dir / "topic_misclassification_review_v1.md"
    priority_csv_path = args.output_dir / "topic_misclassification_priority_v1.csv"
    all_csv_path = args.output_dir / "topic_misclassification_all_v1.csv"

    criteria_path.write_text(build_label_criteria_markdown(), encoding="utf-8")
    review_md_path.write_text(build_review_markdown(enriched_rows, selected_rows), encoding="utf-8")

    fieldnames = [
        "review_status",
        "suggested_label",
        "review_note",
        "ml_sequence_index",
        "round_no",
        "question_no",
        "problem_id",
        "actual_label",
        "predicted_label",
        "era",
        "original_topic",
        "topic_train",
        "question_type",
        "question_subtype",
        "core_concept",
        "text_preview",
        "is_correct",
    ]
    write_csv_rows(priority_csv_path, selected_rows, fieldnames)
    write_csv_rows(all_csv_path, wrong_rows, fieldnames)

    print(f"criteria: {criteria_path}")
    print(f"review_md: {review_md_path}")
    print(f"priority_csv: {priority_csv_path}")
    print(f"all_csv: {all_csv_path}")
    print(f"total={len(enriched_rows)} wrong={len(wrong_rows)} selected={len(selected_rows)}")


if __name__ == "__main__":
    main()

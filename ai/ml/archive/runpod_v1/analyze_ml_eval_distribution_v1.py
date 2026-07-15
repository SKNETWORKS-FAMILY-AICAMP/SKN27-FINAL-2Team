"""ML 평가셋 설계 전에 라벨 분포와 조합 분포를 확인하는 스크립트입니다.
시대, 원본 주제, 통합 주제의 분포를 보고 층화추출 가능 여부를 점검합니다.
결과는 다음 평가셋 생성 기준을 정하기 위한 Markdown/CSV 리포트로 저장합니다.

Run:
  uv run --no-project python ai/ml/analyze_ml_eval_distribution_v1.py
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ML_DIR = Path(__file__).resolve().parent
INPUT_JSON = ML_DIR / "output" / "split_topic_merged_v1" / "full_features_topic_merged_v1.json"
OUTPUT_DIR = ML_DIR / "output" / "eval_distribution_v1"
REPORT_MD = OUTPUT_DIR / "eval_distribution_report_v1.md"
ERA_TOPIC_CSV = OUTPUT_DIR / "era_topic_combo_counts_v1.csv"
ERA_TOPIC_TRAIN_CSV = OUTPUT_DIR / "era_topic_train_combo_counts_v1.csv"

SPLITS = ("train", "test")


# JSON 피처 데이터를 읽어 평가 분포 분석의 기준 데이터로 사용합니다.
# 현재 파일에는 era, topic, topic_train이 모두 들어있어 세 라벨을 함께 볼 수 있습니다.
# 파일이 없거나 비어 있으면 이후 평가셋 생성이 불가능하므로 즉시 예외로 알립니다.
def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"input file not found: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError(f"input file is empty: {path}")
    return rows


# 라벨 값이 None이거나 공백일 때 리포트에서 확인하기 쉬운 값으로 정리합니다.
# 원본 데이터의 실제 라벨은 변경하지 않고, 집계용 문자열만 안전하게 변환합니다.
# 결측 라벨이 있으면 '(missing)'으로 드러나도록 처리합니다.
def label_value(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "(missing)"


# 지정한 라벨 컬럼의 전체 분포와 train/test 분포를 계산합니다.
# train/test 분포를 같이 봐야 시간 기반 split에서 라벨 분포가 얼마나 달라졌는지 알 수 있습니다.
# 반환값은 Markdown 표와 추가 분석에서 재사용하기 쉬운 Counter 묶음입니다.
def count_label(rows: list[dict[str, Any]], key: str) -> dict[str, Counter[str]]:
    counters: dict[str, Counter[str]] = {"all": Counter()}
    counters.update({split: Counter() for split in SPLITS})

    for row in rows:
        label = label_value(row.get(key))
        split = label_value(row.get("split"))
        counters["all"][label] += 1
        if split in SPLITS:
            counters[split][label] += 1

    return counters


# 시대와 주제처럼 두 라벨을 묶어 조합 분포를 계산합니다.
# 조합 분포는 era+topic 또는 era+topic_train 기준 층화추출 가능성을 판단하는 핵심 자료입니다.
# 각 조합별 전체 수, train 수, test 수를 함께 저장합니다.
def count_combo(rows: list[dict[str, Any]], left_key: str, right_key: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        left = label_value(row.get(left_key))
        right = label_value(row.get(right_key))
        split = label_value(row.get("split"))
        combo_key = (left, right)
        item = grouped.setdefault(
            combo_key,
            {
                left_key: left,
                right_key: right,
                "combo": f"{left} / {right}",
                "total": 0,
                "train": 0,
                "test": 0,
            },
        )
        item["total"] += 1
        if split in SPLITS:
            item[split] += 1

    rows_out = list(grouped.values())
    rows_out.sort(key=lambda item: (-item["total"], item["combo"]))
    return rows_out


# 조합별 데이터 수를 기준으로 holdout/k-fold 평가셋 구성 가능성을 표시합니다.
# count가 1인 조합은 train/test 양쪽에 나눌 수 없어 원본 조합 층화추출을 깨뜨립니다.
# count가 작을수록 k-fold에서도 해당 조합이 특정 fold에 빠질 위험이 커집니다.
def add_feasibility(combo_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in combo_rows:
        total = int(row["total"])
        row["holdout_possible"] = "Y" if total >= 2 else "N"
        row["kfold_3_possible"] = "Y" if total >= 3 else "N"
        row["kfold_5_possible"] = "Y" if total >= 5 else "N"
    return combo_rows


# CSV 파일을 Excel에서 바로 열기 좋게 utf-8-sig 인코딩으로 저장합니다.
# 평가셋 생성 전 희소 조합을 필터링하거나 확인할 때 사용할 수 있습니다.
# 컬럼 순서를 고정해 이후 자동 비교가 쉬운 형태로 둡니다.
def write_combo_csv(path: Path, rows: list[dict[str, Any]], left_key: str, right_key: str) -> None:
    fieldnames = [
        left_key,
        right_key,
        "combo",
        "total",
        "train",
        "test",
        "holdout_possible",
        "kfold_3_possible",
        "kfold_5_possible",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fieldnames} for row in rows)


# Counter를 Markdown 표로 변환합니다.
# 전체 건수 대비 비율과 train/test 건수를 함께 보여줘 라벨 불균형을 빠르게 확인할 수 있습니다.
# 라벨이 많은 경우에도 전체를 보여줘 희소 라벨 누락을 피합니다.
def label_table_lines(counters: dict[str, Counter[str]]) -> list[str]:
    total = sum(counters["all"].values())
    labels = [label for label, _ in counters["all"].most_common()]
    lines = ["| 라벨 | 전체 | 비율 | train | test |", "|---|---:|---:|---:|---:|"]
    for label in labels:
        count = counters["all"][label]
        pct = 0 if total == 0 else count / total * 100
        lines.append(
            f"| {label} | {count} | {pct:.1f}% | "
            f"{counters['train'][label]} | {counters['test'][label]} |"
        )
    return lines


# 조합 분포를 Markdown 표로 변환합니다.
# 상위 조합만 먼저 보여주고, 전체 조합은 CSV 파일에서 확인하도록 안내합니다.
# 희소 조합 수는 별도 요약에서 다시 설명합니다.
def combo_table_lines(combo_rows: list[dict[str, Any]], limit: int = 30) -> list[str]:
    lines = [
        "| 조합 | 전체 | train | test | holdout | 3-fold | 5-fold |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for row in combo_rows[:limit]:
        lines.append(
            f"| {row['combo']} | {row['total']} | {row['train']} | {row['test']} | "
            f"{row['holdout_possible']} | {row['kfold_3_possible']} | {row['kfold_5_possible']} |"
        )
    return lines


# 조합 수 기준으로 평가셋 구성 시 문제가 되는 희소 조합을 요약합니다.
# holdout/k-fold 각각에서 최소 몇 개 이상의 데이터가 필요한지 판단할 수 있게 합니다.
# 이 요약을 보고 era+topic 또는 era+topic_train 중 무엇을 먼저 쓸지 결정합니다.
def combo_summary(combo_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(int(row["total"]) for row in combo_rows)
    return {
        "combo_count": len(combo_rows),
        "total_rows": sum(int(row["total"]) for row in combo_rows),
        "count_1": counts[1],
        "count_2": counts[2],
        "lt_3": sum(count for size, count in counts.items() if size < 3),
        "lt_5": sum(count for size, count in counts.items() if size < 5),
    }


# 분석 결과를 Markdown 문서로 작성합니다.
# 이 문서는 다음 단계인 평가셋 생성 방식 결정의 근거로 사용합니다.
# 특히 era+topic과 era+topic_train 중 어떤 조합 기준이 안정적인지 보여줍니다.
def build_report(
    rows: list[dict[str, Any]],
    era_topic_rows: list[dict[str, Any]],
    era_topic_train_rows: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    split_counts = Counter(label_value(row.get("split")) for row in rows)

    lines.append("# ML Evaluation Distribution v1")
    lines.append("")
    lines.append("## 목적")
    lines.append("")
    lines.append("- 모델 재학습 전에 `era`, 원본 `topic`, 통합 `topic_train` 분포를 확인합니다.")
    lines.append("- 평가셋을 문제 단위로 나누되 `era + topic` 조합을 고려할 수 있는지 확인합니다.")
    lines.append("- 희소 조합이 많으면 원본 주제보다 통합 주제 기준 층화추출을 먼저 사용합니다.")
    lines.append("")

    lines.append("## 입력 데이터")
    lines.append("")
    lines.append(f"- 입력 파일: `{INPUT_JSON.as_posix()}`")
    lines.append(f"- 전체 문항 수: {len(rows)}")
    lines.append(f"- train: {split_counts['train']}")
    lines.append(f"- test: {split_counts['test']}")
    lines.append("")

    for key, title in [
        ("era", "시대 era 분포"),
        ("topic", "원본 주제 topic 분포"),
        ("topic_train", "통합 주제 topic_train 분포"),
    ]:
        lines.append(f"## {title}")
        lines.append("")
        lines.extend(label_table_lines(count_label(rows, key)))
        lines.append("")

    for title, combo_rows, csv_path in [
        ("era + topic 조합 분포", era_topic_rows, ERA_TOPIC_CSV),
        ("era + topic_train 조합 분포", era_topic_train_rows, ERA_TOPIC_TRAIN_CSV),
    ]:
        summary = combo_summary(combo_rows)
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"- 조합 수: {summary['combo_count']}")
        lines.append(f"- 전체 문항 수: {summary['total_rows']}")
        lines.append(f"- 1개뿐인 조합: {summary['count_1']}")
        lines.append(f"- 2개뿐인 조합: {summary['count_2']}")
        lines.append(f"- 3개 미만 조합: {summary['lt_3']}")
        lines.append(f"- 5개 미만 조합: {summary['lt_5']}")
        lines.append(f"- 전체 조합 CSV: `{csv_path.as_posix()}`")
        lines.append("")
        lines.extend(combo_table_lines(combo_rows))
        lines.append("")

    lines.append("## 다음 판단")
    lines.append("")
    lines.append("1. `era + topic_train` 조합에 희소 조합이 적으면 통합 주제 평가셋부터 생성합니다.")
    lines.append("2. `era + topic` 조합에 1~2개 조합이 많으면 원본 주제 조합 층화추출은 예외 처리가 필요합니다.")
    lines.append("3. 시간 기반 split은 그대로 유지하고, 추가 평가셋으로 조합 층화추출 split을 만듭니다.")
    lines.append("4. 이 리포트 이후 작업은 평가셋 생성 스크립트 작성입니다.")
    lines.append("")
    return "\n".join(lines)


# 분포 분석 전체 과정을 실행합니다.
# 기존 산출물은 수정하지 않고 eval_distribution_v1 폴더에 새 결과만 저장합니다.
# 콘솔에는 생성된 리포트 경로를 출력해 바로 확인할 수 있게 합니다.
def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = read_rows(INPUT_JSON)
    era_topic_rows = add_feasibility(count_combo(rows, "era", "topic"))
    era_topic_train_rows = add_feasibility(count_combo(rows, "era", "topic_train"))

    write_combo_csv(ERA_TOPIC_CSV, era_topic_rows, "era", "topic")
    write_combo_csv(ERA_TOPIC_TRAIN_CSV, era_topic_train_rows, "era", "topic_train")
    REPORT_MD.write_text(
        build_report(rows, era_topic_rows, era_topic_train_rows),
        encoding="utf-8",
    )

    print(f"report: {REPORT_MD}")
    print(f"era_topic_csv: {ERA_TOPIC_CSV}")
    print(f"era_topic_train_csv: {ERA_TOPIC_TRAIN_CSV}")


if __name__ == "__main__":
    main()

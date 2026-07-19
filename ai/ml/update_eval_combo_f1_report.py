from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd


RESULT_ROOT = Path(
    r"C:\Users\Playdata\Downloads\eval_fixed_walk_forward_v2\eval_fixed_walk_forward_v2"
)
REPORT_DIR = Path(r"C:\dev\project\SKN27-FINAL-2Team\ai\ml\reports")
SUMMARY_PATH = REPORT_DIR / "ML_eval_fixed_walk_forward_v2_summary_2026-07-16.md"


def classification_metrics(y_true: list[str], y_pred: list[str]) -> dict[str, float | int]:
    labels = sorted(set(y_true) | set(y_pred))
    support = Counter(y_true)
    total = len(y_true)
    correct = sum(t == p for t, p in zip(y_true, y_pred))
    f1_values = []
    weighted_sum = 0.0

    for label in labels:
        tp = sum(t == label and p == label for t, p in zip(y_true, y_pred))
        fp = sum(t != label and p == label for t, p in zip(y_true, y_pred))
        fn = sum(t == label and p != label for t, p in zip(y_true, y_pred))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        weighted_sum += f1 * support[label]

    return {
        "accuracy": correct / total if total else 0.0,
        "macro_f1": sum(f1_values) / len(labels) if labels else 0.0,
        "weighted_f1": weighted_sum / total if total else 0.0,
        "label_count_true": len(set(y_true)),
        "label_count_pred": len(set(y_pred)),
        "test_size": total,
    }


def make_combo_metrics() -> tuple[pd.DataFrame, pd.DataFrame]:
    fixed = pd.read_csv(
        RESULT_ROOT / "fixed_47_70_to_71_78" / "fixed_47_70_to_71_78_combined_predictions.csv"
    )
    walk = pd.read_csv(RESULT_ROOT / "walk_forward_all_rounds_combined_predictions.csv")

    combo_specs = [
        ("era_topic_train", "true_era_topic_train", "pred_era_topic_train"),
        ("era_topic_train_topic", "true_era_topic_train_topic", "pred_era_topic_train_topic"),
    ]

    rows = []
    for eval_mode, df in [("fixed_holdout", fixed), ("walk_forward", walk)]:
        for combo, true_col, pred_col in combo_specs:
            metrics = classification_metrics(df[true_col].astype(str).tolist(), df[pred_col].astype(str).tolist())
            rows.append({"eval_mode": eval_mode, "combo": combo, **metrics})

    round_rows = []
    for round_no, group in walk.groupby("round_no"):
        for combo, true_col, pred_col in combo_specs:
            metrics = classification_metrics(group[true_col].astype(str).tolist(), group[pred_col].astype(str).tolist())
            round_rows.append({"round_no": int(round_no), "combo": combo, **metrics})

    combo_df = pd.DataFrame(rows)
    round_df = pd.DataFrame(round_rows)
    return combo_df, round_df


def to_md_table(df: pd.DataFrame, columns: list[str] | None = None) -> str:
    view = df[columns] if columns else df
    rows = []
    headers = list(view.columns)
    rows.append("| " + " | ".join(headers) + " |")
    rows.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for _, row in view.iterrows():
        values = []
        for column in headers:
            value = row[column]
            if column in {"round_no", "label_count_true", "label_count_pred", "test_size"}:
                values.append(str(int(value)))
            elif isinstance(value, float):
                values.append(f"{value:.4f}")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join(rows)


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    combo_df, round_df = make_combo_metrics()
    combo_df.to_csv(REPORT_DIR / "eval_fixed_walk_forward_combo_f1_metrics.csv", index=False, encoding="utf-8-sig")
    round_df.to_csv(REPORT_DIR / "walk_forward_combo_f1_metrics_by_round.csv", index=False, encoding="utf-8-sig")

    target_table = """| 평가 방식 | target | Accuracy | Macro F1 | Weighted F1 |
| --- | --- | ---: | ---: | ---: |
| fixed holdout | `era` | 0.9375 | 0.9283 | 0.9381 |
| fixed holdout | `topic_train` | 0.7950 | 0.7922 | 0.7930 |
| fixed holdout | `topic` | 0.7850 | 0.6703 | 0.7762 |
| walk-forward mean | `era` | 0.9325 | 0.9333 | 0.9322 |
| walk-forward mean | `topic_train` | 0.8225 | 0.8128 | 0.8228 |
| walk-forward mean | `topic` | 0.7875 | 0.6514 | 0.7807 |"""

    combo_table = to_md_table(
        combo_df,
        ["eval_mode", "combo", "accuracy", "macro_f1", "weighted_f1", "label_count_true", "label_count_pred", "test_size"],
    )
    round_table = to_md_table(
        round_df[round_df["combo"] == "era_topic_train"],
        ["round_no", "accuracy", "macro_f1", "weighted_f1", "label_count_true", "label_count_pred", "test_size"],
    )

    md = f"""# ML Fixed Holdout + Walk-Forward 평가 정리 - 2026-07-16

## 평가 기준

이 프로젝트의 메인 평가지표는 `Macro F1`과 `Weighted F1`이다.

- `Macro F1`: 각 라벨을 동일한 비중으로 평가하므로 소수 라벨 성능을 확인하기 좋다.
- `Weighted F1`: 실제 데이터 분포를 반영한 F1이다.
- `Accuracy`: 전체 정답률을 빠르게 보는 보조 지표다.

따라서 아래 결과는 `Macro F1 -> Weighted F1 -> Accuracy` 순서로 해석하는 것이 적절하다.

## 평가 목적

추가 신규 문제가 없는 상황에서 71~78회차를 최신 회차 검증 데이터로 사용했다.

| 평가 방식 | 학습 데이터 | 테스트 데이터 | 의미 |
| --- | --- | --- | --- |
| fixed holdout | 47~70회차 | 71~78회차 전체 | 70회차까지만 알고 있을 때 이후 회차를 얼마나 잘 맞히는지 확인 |
| walk-forward | 47~70 -> 71, 47~71 -> 72, ... | 각 다음 회차 | 실제 운영처럼 회차가 추가될 때마다 모델을 업데이트했을 때의 성능 확인 |

사용 입력 컬럼은 `text`이며, 이는 `지문 + 질문 + 키워드` 기반 입력이다.

## 사용 파라미터

| target | max_length | learning_rate | batch_size | epochs | class_weight |
| --- | ---: | ---: | ---: | ---: | --- |
| `era` | 512 | 5e-6 | 16 | 17 | True |
| `topic_train` | 512 | 1e-5 | 16 | 5 | True |
| `topic` | 512 | 1e-5 | 8 | 6 | True |

## Target별 성능 비교

{target_table}

## 조합 성능 비교

`era + topic_train` 조합도 Accuracy만 보면 부족하므로 Macro F1과 Weighted F1을 함께 계산했다.

{combo_table}

## Walk-Forward 회차별 조합 성능: era + topic_train

{round_table}

## 해석

### 1. Accuracy는 보조 지표로만 봐야 한다

이전 요약에서는 조합 성능을 accuracy 중심으로 설명했지만, 최종 판단 기준으로는 부족하다.  
특히 `era + topic_train` 조합은 라벨 조합 수가 많고 분포가 균등하지 않기 때문에, Macro F1을 반드시 함께 봐야 한다.

### 2. `era` 모델은 안정적이다

`era`는 fixed holdout과 walk-forward 모두 Macro F1이 0.93 전후다.  
시대 분류는 최신 회차에서도 안정적인 편이다.

### 3. `topic_train`은 walk-forward에서 개선됐다

`topic_train`은 fixed holdout Macro F1 0.7922에서 walk-forward Macro F1 0.8128로 상승했다.  
최신 회차를 순차적으로 학습에 추가하는 방식이 통합 주제 예측에 도움을 주는 것으로 볼 수 있다.

### 4. 조합 평가는 walk-forward가 더 낫다

`era + topic_train` 조합 기준:

- fixed holdout: Macro F1 0.7008, Weighted F1 0.7341
- walk-forward: Macro F1 0.6956, Weighted F1 0.7512

Weighted F1과 Accuracy 기준으로는 walk-forward가 더 좋다.  
다만 Macro F1은 fixed holdout이 0.7008, walk-forward가 0.6956으로 거의 비슷하며 fixed holdout이 아주 조금 높다.

### 5. `topic`은 보조 설명용으로 유지하는 것이 맞다

`topic`은 세부 라벨 수가 많고 소수 클래스가 있어 Macro F1이 낮다.  
따라서 최종 트렌드 판단은 `era + topic_train` 중심으로 하고, `topic`은 설명 보조로 쓰는 것이 적절하다.

## 결론

최종 검증 기준은 다음 순서로 보는 것이 맞다.

1. `era + topic_train` 조합 Macro F1
2. `era + topic_train` 조합 Weighted F1
3. target별 Macro F1 / Weighted F1
4. Accuracy는 보조 지표

현재 결과에서는 `walk-forward` 방식이 fixed holdout보다 조합 F1 기준으로도 더 낫다.  
운영용 모델은 47~78회차 전체로 학습하되, 검증 근거는 walk-forward 결과를 중심으로 제시하는 것이 적절하다.

## 생성된 추가 파일

- `eval_fixed_walk_forward_combo_f1_metrics.csv`
- `walk_forward_combo_f1_metrics_by_round.csv`
"""

    SUMMARY_PATH.write_text(md, encoding="utf-8")
    print(SUMMARY_PATH)
    print(REPORT_DIR / "eval_fixed_walk_forward_combo_f1_metrics.csv")
    print(REPORT_DIR / "walk_forward_combo_f1_metrics_by_round.csv")


if __name__ == "__main__":
    main()

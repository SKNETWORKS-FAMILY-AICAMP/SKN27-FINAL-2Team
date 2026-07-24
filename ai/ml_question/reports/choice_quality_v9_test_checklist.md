# 선지 오류 검수 모델 v9 테스트 체크리스트

## 테스트 목적

v9는 v8 학습 구조를 유지하면서 결과 확인 가독성을 개선한 버전이다.  
주요 확인 대상은 모델 성능 자체뿐 아니라, 팀원이 실제로 볼 `review.csv`가 잘 저장되고 읽기 쉬운지이다.

## v9에 반영된 수정

- `choice_quality_output_v9`로 결과 폴더 분리
- `choice_quality_train_v9.json`, `choice_quality_test_v9.json` 사용
- 상세 결과 CSV에 `passage`, `input_text` 저장
- 팀원 검수용 간단 파일 추가
  - `test_review.csv`
  - `valid_review.csv`
- `error_prob >= 0.8`은 에러 판정 기준이 아니라 `HIGH` 우선순위 기준으로 분리
- 규칙 검사만 걸린 경우는 `검수필요`이 아니라 `참고검수`로 낮춤

## RunPod 파일 구조

```text
/workspace/
├─ train_choice_quality_runpod_v9.ipynb
└─ common/
   ├─ choice_quality_train_v9.json
   └─ choice_quality_test_v9.json
```

## 학습 후 확인할 파일

```text
/workspace/choice_quality_output_v9/results.json
/workspace/choice_quality_output_v9/test_predictions.csv
/workspace/choice_quality_output_v9/test_review.csv
/workspace/choice_quality_output_v9/valid_review.csv
/workspace/choice_quality_output_v9/threshold_report.csv
/workspace/choice_quality_output_v9/model/
```

## 반드시 확인할 항목

### 1. 성능 지표

`results.json`에서 아래 값을 확인한다.

```text
best_threshold
test_binary_metrics.abnormal_precision
test_binary_metrics.abnormal_recall
test_binary_metrics.abnormal_f1
```

목표는 이상 선지를 놓치지 않는 것이므로 `abnormal_recall`을 우선 확인한다.

### 2. 상세 결과 CSV

`test_predictions.csv`에 아래 컬럼이 있는지 확인한다.

```text
passage
question
choice
input_text
model_pred_label
rule_label
final_label
error_prob
```

이 파일은 개발자/분석용이다.

### 3. 팀원 검수용 CSV

`test_review.csv`에 아래 컬럼만 간단히 들어가는지 확인한다.

```text
검수상태
우선순위
오류확률
판단근거
오류코드
문항ID
선지번호
정답여부
지문
질문
선지
```

팀원은 이 파일을 우선 보면 된다.

### 4. 검수상태 기준

`test_review.csv`에서 아래 기준으로 샘플을 확인한다.

```text
검수상태=검수필요
모델이 이상 선지로 본 항목이다. 우선 확인한다.

검수상태=참고검수
규칙 검사만 걸린 항목이다. 오탐 가능성이 있으므로 참고용으로 확인한다.

검수상태=통과
2차 검수 기준에서 이상 가능성이 낮다.
```

### 5. 우선순위 기준

```text
HIGH
모델이 이상이라고 판단했고 error_prob >= 0.8인 고확신 후보

MEDIUM
모델이 이상이라고 판단했지만 error_prob < 0.8인 후보

LOW
통과 또는 참고검수
```

`0.8`은 에러 판정 기준이 아니라 사람이 먼저 볼 후보를 정하는 기준이다.  
실제 모델 판정 기준은 `best_threshold`다.

## 추가로 볼 샘플

아래 조건으로 CSV를 필터링해서 샘플을 확인한다.

```text
검수필요 샘플
test_review.csv에서 검수상태 = 검수필요

고확신 샘플
test_review.csv에서 우선순위 = HIGH

참고검수 오탐 가능성 샘플
test_review.csv에서 검수상태 = 참고검수
```

평가 데이터에서는 `test_predictions.csv`로 아래도 확인한다.

```text
모델이 놓친 이상 선지
true_label=0 and model_pred_label=1

모델이 잘못 잡은 정상 선지
true_label=1 and model_pred_label=0
```

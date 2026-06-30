# ML 전처리 테스트 v2

## 목표

한능검 기출 시험 경향 분석용 라벨 데이터를 더 정확하고 범용적으로 만들기 위한 2차 개선 기록이다.

v1에서는 47회 테스트 데이터를 기준으로 `era`, `topic`, `question_type`, `question_subtype`, `core_concept`를 추출하고 일부 오분류 문항을 보정했다.

v2의 핵심 목표는 특정 회차만 맞추는 것이 아니라, 앞으로 76회, 77회, 전체 기출 문제에도 적용 가능한 전처리 기준을 만드는 것이다.

## 중요 기준

전처리에서 가장 중요한 것은 한 문항을 예쁘게 맞추는 것이 아니라, 모든 기출 문제를 같은 기준으로 안정적으로 처리하는 것이다.

따라서 라벨링 기준은 아래 순서를 따른다.

```text
1. 지문, 자료, 발문이 묻는 실제 시대와 핵심 개념을 우선한다.
2. 정답 선지와 오답 선지에만 등장하는 키워드에 끌려가지 않는다.
3. 해설지는 정답 근거와 시대 배경을 확인하는 검수 자료로 사용한다.
4. 자동 분류 결과가 애매하면 검수 리포트에 남긴다.
5. 사람이 검수한 문항은 override로 고정해 재실행해도 흔들리지 않게 한다.
```

## v2 변경 사항

### 1. 문제지 분류 프롬프트 강화

`ML_feature_data.py`의 문제지 Vision 분류 프롬프트에 아래 기준을 추가했다.

```text
기출 전체를 범용적으로 전처리하는 것이 목표이므로 특정 회차 패턴에 맞추지 말고 모든 문항에 같은 기준을 적용한다.
답을 맞히기 위한 해설이 아니라 ML 라벨 생성을 위한 객관 라벨을 작성한다.
core_concept에는 "문제", "자료", "시기", "상황", "인물", "정책" 같은 일반어를 쓰지 않는다.
```

이 변경은 선택지 키워드에 끌려 시대가 바뀌거나, 핵심개념이 너무 넓게 잡히는 문제를 줄이기 위한 것이다.

### 2. 해설지 분류 프롬프트 강화

해설지 Vision 분류 프롬프트에도 아래 기준을 추가했다.

```text
해설지가 여러 선택지를 함께 설명하더라도 문항이 실제로 묻는 중심 자료와 정답 근거를 우선한다.
기출 전체 전처리에 재사용할 수 있도록 같은 기준으로 안정적인 라벨을 작성한다.
```

해설지는 모델 학습 데이터가 아니라 라벨 검수 자료로 사용한다.

### 3. 해설지 결과 병합 방식 개선

기존에는 해설지 Vision 결과가 있으면 문제지 Vision 결과를 그대로 덮어쓸 수 있었다.

v2에서는 `row_quality_score()`를 추가해 라벨 품질 점수를 계산한 뒤, 더 좋은 라벨만 반영하도록 변경했다.

품질 점수 기준은 다음과 같다.

```text
era가 허용값이고 미분류가 아니면 가점
topic이 허용값이고 미분류가 아니면 가점
question_type이 허용값이고 미분류가 아니면 가점
question_subtype이 허용값이고 미분류가 아니면 가점
core_concept가 일반어가 아니라 구체적인 역사 용어이면 가점
core_concept와 era_reference 기준 시대가 일치하면 가점
```

이 방식은 해설지 결과가 더 정확할 때는 반영하지만, 해설지 결과가 너무 일반적이거나 흔들릴 때는 기존 문제지 결과를 유지하기 위한 안전장치다.

### 4. 검수 리포트 추가

`ml_label_review.csv`를 새로 생성하도록 추가했다.

경로:

```text
test/CJ/test_ml/output/ml_label_review.csv
```

이 파일은 자동 분류 결과 중 사람이 다시 봐야 할 문항만 모은다.

검수 대상으로 잡는 조건은 다음과 같다.

```text
era가 미분류이거나 허용값 밖인 경우
topic이 미분류이거나 허용값 밖인 경우
question_type이 미분류이거나 허용값 밖인 경우
question_subtype이 미분류이거나 허용값 밖인 경우
core_concept가 비어 있거나 너무 일반적인 경우
core_concept 기준 시대와 최종 era가 충돌하는 경우
```

단, 사람이 이미 검수해서 `ROUND_QUESTION_OVERRIDES`에 넣은 문항은 의도된 보정으로 보고 충돌 리포트에서 제외한다.

### 5. 현재 47회 테스트 결과

현재 `ml_raw_data.csv` 기준으로 검수 리포트를 생성한 결과:

```text
ml_label_review.csv 검수 후보: 0건
```

즉, 47회 테스트 데이터 기준으로는 자동 검수 규칙에 걸리는 문항이 없는 상태다.

## 실행 명령어

### 현재 CSV 기준 검수 리포트만 다시 생성

OpenAI API 호출 없이 로컬 CSV만 확인한다.

```powershell
uv --cache-dir .uv-cache run python -c "from test.CJ.test_ml.ML_feature_data import OUTPUT_CSV, OUTPUT_REVIEW_CSV, write_review_report; write_review_report(OUTPUT_CSV, OUTPUT_REVIEW_CSV); print(OUTPUT_REVIEW_CSV)"
```

### 문제지 Vision + 해설지 Vision 기반 재전처리

OpenAI API 비용이 발생한다.

```powershell
uv --cache-dir .uv-cache run python test/CJ/test_ml/ML_feature_data.py --rounds 47 --force --source vision --repair-missing --use-explanations
```

### 전체 기출로 확장할 때 권장 흐름

```powershell
uv --cache-dir .uv-cache run python test/CJ/test_ml/ML_feature_data.py --rounds 47 48 49 50 --force --source vision --repair-missing --use-explanations
```

처음부터 47~78 전체를 한 번에 돌리기보다, 3~5개 회차 단위로 실행하고 `ml_label_review.csv`를 확인하는 방식을 권장한다.

## 다음 단계

1. 47회 결과를 기준 샘플로 확정한다.
2. 48~50회 정도를 추가 실행한다.
3. `ml_label_review.csv`에 남는 문항을 검수한다.
4. 반복적으로 틀리는 패턴은 프롬프트 또는 `ROUND_QUESTION_OVERRIDES`에 반영한다.
5. 검수 후보가 충분히 줄면 76회, 77회 실제 테스트 데이터 전처리로 넘어간다.

## 판단

현재 방향은 유지하는 것이 좋다.

문제지 Vision만 사용하는 것보다 해설지 Vision과 검수 리포트를 함께 사용하는 방식이 더 안정적이다. 다만 최종 목표는 특정 문항의 정답을 맞히는 것이 아니라, 모든 기출 문제를 같은 기준으로 전처리하는 것이다.

따라서 앞으로도 전처리 개선은 아래 원칙을 유지한다.

```text
자동 추출
-> 해설지 기반 보정
-> 검수 리포트 생성
-> 사람이 확인한 override 누적
-> 다음 회차로 확장
```

# ML feature v1/v2 전처리 비교 및 신뢰도 정리

작성일: 2026-07-14

## 결론

현재 `ml_han_features_v2` 기반 KLUE/RoBERTa 결과가 크게 좋아진 이유는 단순히 모델 학습 조건이 좋아져서가 아니라, **학습 라벨과 보조 feature의 품질이 크게 바뀌었기 때문**이다.

다만 이 결과를 해석할 때는 주의가 필요하다. v2 전처리에서는 GPT가 `era`, `topic`, `topic_train`, `core_concept`를 다시 만들 때 **정답 선택지(`answer_choice`)도 참고했다.** 그리고 이후 학습 입력인 `text_with_core`에는 GPT가 만든 `core_concept`가 포함된다.

따라서 이번 결과는 다음처럼 해석하는 것이 가장 정확하다.

```text
v1의 라벨 노이즈가 컸고,
v2에서 GPT 상위 모델이 정답 선택지까지 참고해 더 일관된 라벨/핵심개념을 만들었으며,
그 결과 KLUE/RoBERTa가 훨씬 잘 학습했다.
```

즉 “정답 라벨을 test 학습에 직접 넣어서 좋아진 결과”는 아니지만, **정답 선택지에서 유래한 정보가 feature 생성에 반영되었을 가능성은 있다.**

## 사용 파일 흐름

### v1 흐름

```text
ML_han_v1.json
-> build_ml_han_features_v1.py
-> ml_han_features_v1.json/csv
-> split_v1
-> split_topic_merged_v1
-> eval_splits_with_core_v1
-> KLUE/RoBERTa 학습
```

### v2 흐름

```text
ML_han_v1.json
-> gpt_relabel_ml_han_features_v2_standard.py
-> ml_han_features_v2.json/csv
-> split_v2
-> split_topic_merged_v2
-> eval_splits_with_core_v2
-> KLUE/RoBERTa 학습
```

## v1 전처리 방식

v1은 GPT를 사용하지 않고, 대부분 **규칙 기반 추론**으로 feature를 만들었다.

주요 특징:

- `era_reference.json`, `ml_keyword_era_overrides.json` 등의 키워드 사전을 사용했다.
- 시대(`era`)는 키워드, 인물 등장 정보, 수동 override, 문제 번호 fallback 등을 조합해 추론했다.
- 주제(`topic`)는 기존 `topic_type` 매핑 또는 텍스트/기존 topic 필드의 키워드 매칭으로 추론했다.
- `core_concept`는 기존 topic이 짧고 명확하면 그대로 쓰고, 아니면 키워드 리스트에서 추출했다.
- `topic_train`은 원본 `topic`을 사후 규칙으로 통합해서 만들었다.

v1의 장점:

- 재현 가능하고 비용이 들지 않는다.
- 규칙이 명시되어 있어 동작을 추적하기 쉽다.
- 정답 데이터를 직접 학습 입력에 넣지는 않는다.

v1의 한계:

- 키워드가 없거나 문맥이 복합적인 문항에서 라벨이 흔들린다.
- `사건/정치/제도/인물`처럼 경계가 애매한 주제를 문맥 기준으로 구분하기 어렵다.
- `topic_train`이 원본 `topic`의 품질에 강하게 의존한다.
- 과거 오분류 검토에서 라벨 자체가 수정 후보인 사례가 많았다.

## v2 전처리 방식

v2는 `gpt-5.6-terra`를 사용해 1600개 전체 문항을 다시 라벨링했다.

생성된 주요 컬럼:

- `era`
- `topic`
- `topic_train`
- `topic_train_v1`
- `topic_train_v2`
- `question_type`
- `question_subtype`
- `core_concept`
- `label_confidence`
- `ambiguous_flag`
- `label_reason`
- `review_model`

v2 GPT 프롬프트에는 다음 정보가 들어갔다.

- 기존 `topic_type`
- 기존 `topic` 후보
- `major_type`, `minor_type`
- `question_task`
- 지문
- 질문
- 선택지
- 정답 선택지

그리고 GPT 출력은 JSON schema로 제한했다. 즉 `era`, `topic`, `topic_train_v2` 등이 허용 라벨 목록 안에서만 나오도록 했다.

## v1과 v2의 실제 차이

1600건을 `problem_id` 기준으로 비교했을 때 변경량은 다음과 같다.

| 항목 | 변경 건수 | 비율 |
|---|---:|---:|
| `era` | 520 | 32.5% |
| `topic` | 652 | 40.8% |
| `topic_train` | 667 | 41.7% |
| `core_concept` | 1559 | 97.4% |

특히 `topic_train`이 약 42% 바뀌었다. 기존 학습 결과가 낮았던 핵심 원인이 모델 구조보다 **라벨 품질과 라벨 일관성 문제**였을 가능성이 크다.

### topic 분포 변화

`topic`은 10개 세부 주제 기준 라벨이다. v1과 v2 모두 `topic`을 가지고 있으며, v2에서는 GPT가 문항 문맥을 보고 세부 주제를 다시 판단했다.

| topic | v1 | v2 |
|---|---:|---:|
| 사건 | 417 | 564 |
| 인물 | 383 | 332 |
| 정치 | 282 | 134 |
| 문화 | 175 | 186 |
| 제도 | 164 | 196 |
| 사회 | 52 | 25 |
| 외교 | 37 | 36 |
| 경제 | 33 | 64 |
| 군사 | 31 | 37 |
| 사상·종교 | 26 | 26 |

v1에서는 `정치`로 넓게 잡힌 문항이 많았고, v2에서는 일부가 `사건`, `제도`, `경제`, `군사` 등 더 구체적인 topic으로 이동했다. 이 때문에 v2의 원본 `topic` 기준으로는 `정치`가 282건에서 134건으로 줄고, `사건`은 417건에서 564건으로 늘었다.

### topic_train 분포 변화

`topic_train`은 모델 학습용 5개 통합 주제 라벨이다. v1에서는 `topic`을 만든 뒤 규칙으로 통합했고, v2에서는 GPT가 `topic_train_v2`를 직접 추천한 값을 최종 `topic_train`으로 사용했다.

| topic_train | v1 | v2 |
|---|---:|---:|
| 사건 | 417 | 568 |
| 인물 | 383 | 332 |
| 정치 | 435 | 292 |
| 문화 | 201 | 212 |
| 제도 | 164 | 196 |

여기서 v1의 `정치`는 원본 `정치/경제/사회/군사/외교`가 통합된 값이고, v1의 `문화`는 원본 `문화/사상·종교`가 통합된 값이다. v2는 GPT가 문항별 중심 개념을 기준으로 `topic_train_v2`를 직접 판단했기 때문에, 단순 사후 매핑보다 학습 목적에 더 맞는 라벨이 되었다.

주의할 점은 v2의 `topic_train`이 `topic`을 기계적으로 통합한 값이 아니라는 것이다. v2에서는 GPT가 `topic`과 `topic_train_v2`를 각각 판단했기 때문에, 일부 문항은 `topic` 기준 통합값과 최종 `topic_train`이 다르다.

예를 들어 v2에서 `topic=사건`은 564건이지만 `topic_train=사건`은 568건이다. 차이 4건은 다음처럼 세부 topic은 `군사` 또는 `사회`이지만, 문항의 중심 판단이 특정 전쟁/항쟁/봉기 사건이라 GPT가 학습용 통합 라벨을 `사건`으로 둔 경우다.

| problem_id | round | question | topic | topic_train | core_concept |
|---|---:|---:|---|---|---|
| cj_v41_0727 | 62 | 17 | 군사 | 사건 | 삼별초의 개경 환도 반대 항쟁 |
| cj_v41_0864 | 65 | 12 | 군사 | 사건 | 거란의 침입에 대한 고려의 군사·방어 대응 |
| cj_v41_1058 | 69 | 13 | 군사 | 사건 | 윤관의 여진 정벌과 동북 9성 설치 |
| cj_v41_1157 | 71 | 14 | 사회 | 사건 | 무신 정권기 하층민 봉기 |

따라서 `topic_train`을 반드시 `topic`의 strict merge로 정의한다면 이 4건은 보정 대상이다. 반대로 `topic_train`을 "학습을 위한 GPT 추천 통합 주제"로 정의한다면 숫자 차이는 오류가 아니라 설계상 발생 가능한 차이다.

### 시대 분포 변화

| era | v1 | v2 |
|---|---:|---:|
| 조선 | 500 | 362 |
| 고려 | 236 | 273 |
| 일제 강점기 | 138 | 249 |
| 개항기 | 163 | 231 |
| 현대 | 147 | 183 |
| 삼국 시대 | 178 | 124 |
| 남북국 시대 | 112 | 102 |
| 선사 시대 | 32 | 32 |
| 초기 국가 | 67 | 31 |
| 고조선 | 27 | 13 |

v1에서는 조선/삼국/초기 국가 쪽으로 과하게 몰린 라벨이 있었고, v2에서는 개항기/일제 강점기/현대 쪽이 더 많이 보정되었다.

## 성능 변화

같은 계열의 `topic_train + split_era_topic_train_stratified_v1 + text_with_core` 결과를 비교하면 다음과 같다.

| 실험 | accuracy | macro F1 | weighted F1 |
|---|---:|---:|---:|
| 기존 최고권 `with_core_v3` | 0.6608 | 0.6569 | 0.6507 |
| v2 라벨 기반 `with_core_v5` | 0.8603 | 0.8541 | 0.8621 |

향상폭:

| 지표 | 향상 |
|---|---:|
| accuracy | +0.1995 |
| macro F1 | +0.1972 |
| weighted F1 | +0.2114 |

클래스별 F1은 다음과 같다.

| 라벨 | F1 |
|---|---:|
| 문화 | 0.9259 |
| 사건 | 0.9065 |
| 인물 | 0.8917 |
| 제도 | 0.8155 |
| 정치 | 0.7308 |

아직 가장 약한 축은 `정치`이며, 주요 오분류는 `사건 -> 정치`, `정치 -> 제도`, `제도 -> 정치`이다.

## 왜 결과가 좋아졌나

### 1. 라벨 노이즈가 줄었다

v1은 키워드/규칙 기반이라 문맥보다 단어 매칭의 영향이 컸다. v2는 GPT가 문항 전체, 선택지, 정답 선택지를 보고 중심 개념을 정리했기 때문에 라벨이 더 일관되게 만들어졌다.

특히 기존에는 다음 같은 문제가 있었다.

- 같은 성격의 문항이 `정치`, `사건`, `제도`로 흩어짐
- 인물 중심 문항과 사건 중심 문항의 경계가 불안정함
- 원본 `topic`이 짧거나 부정확할 때 `topic_train`까지 같이 흔들림

v2에서는 이 부분이 많이 정리되었다.

### 2. `topic_train`이 학습 목적에 맞게 직접 생성됐다

v1은 먼저 `topic`을 만들고, 그 뒤 규칙으로 `topic_train`을 통합했다.

```text
v1: topic 추론 -> topic_train 규칙 통합
```

v2는 GPT가 `topic`과 `topic_train_v2`를 함께 생성했다.

```text
v2: 문항 문맥 기준 topic + topic_train_v2 동시 판단
```

그래서 `topic_train`이 단순 사후 매핑이 아니라, 학습용 통합 라벨 목적에 더 맞게 만들어졌다.

### 3. `core_concept`가 훨씬 강한 feature가 됐다

v1의 `core_concept`는 대부분 키워드 추출이나 기존 topic 기반이었다.

v2의 `core_concept`는 GPT가 문항의 핵심 개념을 요약한 값이다. 실제 비교에서 `core_concept`는 1600건 중 1559건이 바뀌었다.

이후 `add_core_text_to_eval_splits_v1.py`에서 `text_with_core`를 만들 때 다음 형식으로 입력에 붙는다.

```text
문항 지문/질문

[Core Concept]
GPT가 만든 핵심 개념
```

이 feature가 모델에게 강한 힌트가 되었을 가능성이 높다.

## 정답 데이터 누수 여부

### 학습/평가 split 관점

현재 학습 절차에서 `test.json`의 정답 라벨을 train에 넣어 학습한 흔적은 없다.

```text
train.json -> 학습
test.json -> 최종 평가
```

따라서 일반적인 의미의 “test 정답을 학습에 직접 사용한 데이터 누수”는 아니다.

### feature 생성 관점

하지만 v2 전처리 단계에서는 GPT 라벨링 프롬프트에 `정답 선택지(answer_choice)`가 포함되었다. 그리고 그 GPT가 만든 `core_concept`를 모델 입력인 `text_with_core`에 넣었다.

따라서 다음 가능성은 인정해야 한다.

```text
정답 선택지를 참고해 만들어진 core_concept가
모델 입력에 포함되면서
분류를 쉽게 만드는 힌트로 작동했을 수 있다.
```

이것은 `target` 라벨 자체를 입력에 넣은 직접 누수는 아니지만, **정답 기반 feature engineering**에 가깝다.

## 이 데이터를 믿을 수 있는가

### 믿을 수 있는 부분

다음 목적이라면 현재 데이터는 충분히 의미가 있다.

- 기존 v1 라벨이 불안정했다는 진단
- GPT 상위 모델을 사용하면 라벨 일관성이 크게 좋아진다는 검증
- KLUE/RoBERTa가 v2 라벨 체계를 잘 학습할 수 있다는 확인
- 문제별 시대/주제/핵심개념이 정리된 학습용 데이터셋 구축

또한 v2 결과의 자체 품질 지표도 나쁘지 않다.

| 항목 | 값 |
|---|---:|
| 전체 건수 | 1600 |
| GPT confidence high | 1592 |
| GPT confidence medium | 8 |
| ambiguous False | 1589 |
| ambiguous True | 11 |

### 조심해야 하는 부분

다음 목적이라면 현재 결과만으로는 부족하다.

- 실제 신규 문항에서 정답 선택지 없이 주제를 예측하는 서비스
- 미래 회차의 출제 트렌드를 문제 지문만 보고 예측하는 서비스
- 사람이 만든 gold label 기준의 객관적 성능 주장

이유는 다음과 같다.

- v2의 test label도 사람이 검수한 gold label이 아니라 GPT가 만든 label이다.
- `core_concept`가 정답 선택지를 본 GPT에서 생성되었다.
- `split_era_topic_train_stratified_v1`은 라벨 분포를 균형 있게 나눈 일반 성능 평가이지, 최신 회차 일반화 평가는 아니다.

## 최종 판단

현재 결과는 “모델이 갑자기 좋아졌다”기보다, **데이터 라벨과 핵심개념 feature가 좋아져서 모델이 제대로 학습할 수 있게 된 결과**로 보는 것이 맞다.

다만 성능 수치 `accuracy 0.8603`, `macro F1 0.8541`은 다음 조건 아래에서의 성능이다.

```text
GPT가 만든 v2 라벨 기준
+ GPT가 만든 core_concept 포함
+ stratified split 평가
```

따라서 보고서에서는 다음 표현이 가장 안전하다.

```text
v2 전처리로 라벨 일관성이 크게 개선되었고,
동일한 KLUE/RoBERTa 조건에서 topic_train 분류 성능이 크게 향상되었다.
다만 v2 라벨과 core_concept는 GPT가 정답 선택지를 참고해 생성했으므로,
실제 신규 문항 예측 성능은 split_time_v1 및 answer_choice 제외 실험으로 추가 검증이 필요하다.
```

## 다음 검증 제안

### 1. split_time_v1 실행

`split_time_v1`은 71~78회차를 test로 두는 최신 회차 평가다.

```text
split_era_topic_train_stratified_v1
= 라벨 체계 학습 가능성 확인

split_time_v1
= 최신 회차 일반화 확인
```

stratified 결과와 time split 결과가 모두 좋으면 v2 전처리 성공이라고 더 강하게 말할 수 있다.

### 2. input_text only 실험

현재는 `text_with_core`를 사용했다. 정답 선택지 기반 core_concept 영향도를 확인하려면 아래 조건도 비교해야 한다.

```text
TARGET = topic_train
INPUT_TEXT_FIELD = text
```

또는 `input_text + keywords`만 사용한 결과와 비교한다.

### 3. answer_choice 제외 v2.1 생성

가장 엄밀한 검증은 GPT 전처리에서 `answer_choice`를 빼고 다시 만드는 것이다.

```text
지문 + 질문 + 선택지
-> GPT era/topic/topic_train/core_concept 생성
-> KLUE/RoBERTa 재학습
```

이 결과도 좋으면 실제 신규 문제 예측용으로 더 신뢰할 수 있다.

### 4. 오답 56건 수작업 검토

현재 오답은 56건이며, 가장 큰 혼동은 다음이다.

| 오분류 | 건수 |
|---|---:|
| 사건 -> 정치 | 13 |
| 정치 -> 제도 | 6 |
| 제도 -> 정치 | 5 |
| 정치 -> 사건 | 4 |
| 정치 -> 문화 | 4 |
| 인물 -> 사건 | 4 |
| 인물 -> 정치 | 4 |

특히 `사건/정치/제도` 경계는 라벨 기준을 더 명확히 정해야 한다.

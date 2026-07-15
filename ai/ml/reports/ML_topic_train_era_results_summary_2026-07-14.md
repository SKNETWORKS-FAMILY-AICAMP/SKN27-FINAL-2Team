# ML topic_train + era 결과 요약

작성일: 2026-07-14

## 목적

`ml_han_features_v2`와 `eval_splits_with_core_v2`를 사용해 KLUE/RoBERTa가 한국사 문항의 `topic_train`과 `era`를 얼마나 잘 분류하는지 확인했다.

공통 실험 조건은 다음과 같다.

| 항목 | 값 |
|---|---|
| model | `klue/roberta-base` |
| input field | `text_with_core` |
| max length | 512 |
| batch size | 16 |
| learning rate | `5e-6` |
| max epochs | 30 |
| patience | 3 |
| class weight | True |
| CV | 3-fold |

`text_with_core`는 문항 지문/질문에 GPT가 만든 `core_concept`를 붙인 입력이다.

## 실험 결과 요약

| target | split | train | test | accuracy | macro F1 | weighted F1 | CV macro F1 mean | best epoch mean | wrong |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `topic_train` | `split_era_topic_train_stratified_v1` | 1199 | 401 | 0.8603 | 0.8541 | 0.8621 | 0.8035 | 7.67 | 56 |
| `topic_train` | `split_time_v1` | 1200 | 400 | 0.8175 | 0.8089 | 0.8164 | 0.8152 | 6.33 | 73 |
| `era` | `split_era_topic_train_stratified_v1` | 1199 | 401 | 0.9302 | 0.9266 | 0.9301 | 0.9080 | 15.00 | 28 |

## 결과 해석

### topic_train stratified

`split_era_topic_train_stratified_v1`은 `era + topic_train` 조합 분포를 train/test에 비슷하게 유지한 split이다. 따라서 특정 라벨만 많이 맞춰서 accuracy가 높아지는 착시를 줄이고, 모델이 라벨 체계를 전반적으로 학습했는지 확인하는 데 적합하다.

결과:

```text
accuracy: 0.8603
macro F1: 0.8541
weighted F1: 0.8621
wrong: 56 / 401
```

`macro F1`도 높기 때문에 단순히 다수 클래스만 맞춘 결과는 아니다. `topic_train` 5개 라벨 전반을 비교적 안정적으로 학습했다고 볼 수 있다.

주요 병목은 `정치` 라벨이다. 가장 큰 오분류 축은 다음이었다.

| 오분류 | 건수 |
|---|---:|
| 사건 -> 정치 | 13 |
| 정치 -> 제도 | 6 |
| 제도 -> 정치 | 5 |
| 정치 -> 사건 | 4 |
| 정치 -> 문화 | 4 |
| 인물 -> 사건 | 4 |
| 인물 -> 정치 | 4 |

### topic_train time split

`split_time_v1`은 과거 회차를 train으로, 71~78회차를 test로 사용한다. 최신 회차 일반화 성능을 확인하기 위한 split이다.

결과:

```text
accuracy: 0.8175
macro F1: 0.8089
weighted F1: 0.8164
wrong: 73 / 400
```

stratified 결과보다 낮지만, 최신 회차에서도 macro F1이 0.8 이상 유지된다. 따라서 v2 전처리 결과가 랜덤/층화 평가에서만 좋은 것이 아니라 최신 회차에도 어느 정도 일반화된다고 볼 수 있다.

최신 회차 예측 TOP5는 다음과 같다.

| 순위 | pred topic_train | 건수 | 비율 |
|---:|---|---:|---:|
| 1 | 사건 | 135 | 33.75% |
| 2 | 정치 | 84 | 21.00% |
| 3 | 인물 | 73 | 18.25% |
| 4 | 문화 | 60 | 15.00% |
| 5 | 제도 | 48 | 12.00% |

실제 라벨 분포도 `사건 > 정치 > 인물 > 문화 > 제도` 순서라, 최신 트렌드 추정 방향은 실제 분포와 거의 일치했다.

### era stratified

`era`는 10개 시대 라벨을 예측하는 실험이다. `topic_train`보다 라벨 수가 많지만, 시대 정보는 지문/핵심개념에 비교적 명확하게 드러나는 경우가 많아 성능이 높게 나왔다.

결과:

```text
accuracy: 0.9302
macro F1: 0.9266
weighted F1: 0.9301
wrong: 28 / 401
```

CV macro F1 평균도 0.9080으로 높다. 최종 test 성능과 CV 성능 차이가 크지 않으므로 심한 오버피팅으로 보기는 어렵다.

다만 fold별 validation loss를 보면 best epoch 이후 train loss는 계속 내려가고 validation loss는 정체 또는 소폭 상승했다. 따라서 후반부에는 약한 과적합 징후가 있지만, `patience=3`과 `final_epochs=15` 설정으로 크게 악화되기 전에 제어된 상태다.

주요 오분류는 다음과 같다.

| 오분류 | 건수 |
|---|---:|
| 조선 -> 개항기 | 5 |
| 개항기 -> 일제 강점기 | 2 |
| 개항기 -> 조선 | 2 |
| 삼국 시대 -> 고려 | 2 |
| 삼국 시대 -> 초기 국가 | 2 |
| 조선 -> 삼국 시대 | 2 |
| 현대 -> 일제 강점기 | 2 |

대부분 인접 시대 또는 문항 맥락상 경계가 생길 수 있는 오분류다.

## 오버피팅/언더피팅 판단

### topic_train

`topic_train`은 stratified와 time split 모두에서 0.8 이상의 macro F1을 보였다. time split에서 성능이 약간 떨어졌지만 급락하지 않았다.

따라서 현재 기준으로는 다음처럼 판단한다.

```text
언더피팅: 아님
심한 오버피팅: 아님
정치/사건/제도 경계에서 추가 개선 여지 있음
```

### era

`era`는 test macro F1 0.9266, CV macro F1 mean 0.9080으로 매우 높다.

학습 그래프에서 후반부 validation loss가 정체되는 구간이 있으므로 약한 과적합 징후는 있다. 하지만 test 성능과 CV 성능이 함께 높고, confusion matrix도 대각선이 강하므로 현재 결과는 안정적으로 볼 수 있다.

```text
언더피팅: 아님
심한 오버피팅: 아님
후반부 약한 과적합은 있으나 early stopping으로 제어됨
```

## 현재까지의 결론

1. `topic_train`은 v2 전처리 이후 성능이 크게 개선되었다.
2. `split_time_v1`에서도 macro F1 0.8089를 유지해 최신 회차 일반화 가능성을 확인했다.
3. `era`는 stratified 기준 macro F1 0.9266으로 매우 안정적이다.
4. 현재 병목은 `topic_train`의 `정치/사건/제도` 경계다.
5. 결과 해석 시 `text_with_core`가 GPT 기반 `core_concept`를 포함한다는 점은 계속 명시해야 한다.

## 다음 실험: TARGET=topic

다음은 원본 세부 주제 10개 라벨인 `topic` 실험이다.

추천 설정:

```python
SPLIT_NAME = 'split_era_topic_train_stratified_v1'
TARGET = 'topic'
INPUT_TEXT_FIELD = 'text_with_core'

MODEL_NAME = 'klue/roberta-base'
MAX_LENGTH = 512
MAX_EPOCHS = 30
BATCH_SIZE = 16
LEARNING_RATE = 5e-6
PATIENCE = 3
MIN_DELTA = 0.0
N_SPLITS = 3
VALID_SIZE = 0.2
RANDOM_STATE = 42
USE_CLASS_WEIGHT = True
RUN_CV = True
SAVE_MODEL = True
```

주의:

- `topic`은 10개 라벨이라 `topic_train`보다 어렵다.
- `사회`, `외교`, `군사`, `사상·종교`처럼 표본이 작은 라벨이 있어 macro F1이 낮아질 수 있다.
- 이 실험의 목적은 최고 성능 확보보다, 세부 topic 분류가 가능한지와 어떤 라벨이 통합 대상인지 확인하는 것이다.

예상:

```text
topic_train보다 accuracy/macro F1은 낮게 나올 가능성이 높다.
하지만 세부 topic 오분류를 보면 통합 주제 설계가 타당한지 확인할 수 있다.
```


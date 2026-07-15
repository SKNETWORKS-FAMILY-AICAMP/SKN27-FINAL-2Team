# 7/14 New Feature 실험 정리

작성일: 2026-07-14

## 목적

기존 `ml_han_features_v1` 기반 학습 결과가 낮게 나왔던 원인을 확인하고, GPT 상위 모델로 재전처리한 `ml_han_features_v2`가 실제 ML 성능을 개선하는지 검증했다.

이번 실험의 핵심 변화는 다음이다.

```text
기존 v1:
ML_han_v1.json
-> 규칙/키워드 기반 feature 생성
-> ml_han_features_v1

현재 v2:
ML_han_v1.json
-> gpt-5.6-terra 기반 재라벨링
-> ml_han_features_v2
-> text_with_core 기반 KLUE/RoBERTa 학습
```

## 공통 학습 조건

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

`text_with_core`는 문항 텍스트 뒤에 GPT가 만든 핵심 개념 `core_concept`를 붙인 입력이다.

```text
문항 지문/질문

[Core Concept]
핵심 개념
```

## 기존 feature와 새 feature의 차이

### v1 feature

v1은 GPT를 사용하지 않고 규칙 기반으로 feature를 만들었다.

- 시대(`era`): 키워드 사전, 인물-시대 매핑, 수동 override, 문제 번호 fallback 사용
- 주제(`topic`): 기존 `topic_type`, 기존 topic 후보, 주제 키워드 매칭 사용
- 통합 주제(`topic_train`): `topic`을 만든 뒤 규칙으로 통합
- 핵심 개념(`core_concept`): 기존 topic 또는 키워드 기반 추출

장점은 재현성과 비용이지만, 문맥이 복합적인 문항에서 라벨이 흔들리는 문제가 있었다.

### v2 feature

v2는 `gpt-5.6-terra`로 1600개 전체 문항을 다시 라벨링했다.

생성된 주요 feature:

- `era`
- `topic`
- `topic_train_v1`
- `topic_train_v2`
- 최종 `topic_train`
- `question_type`
- `question_subtype`
- `core_concept`
- `label_confidence`
- `ambiguous_flag`
- `label_reason`

v2에서는 GPT가 문항의 지문, 질문, 선택지, 정답 선택지, 기존 topic 후보를 함께 보고 시대/주제/통합 주제를 판단했다.

## v1 -> v2 변경량

1600건 기준으로 v1과 v2를 비교하면 다음과 같다.

| 항목 | 변경 건수 | 비율 |
|---|---:|---:|
| `era` | 520 | 32.5% |
| `topic` | 652 | 40.8% |
| `topic_train` | 667 | 41.7% |
| `core_concept` | 1559 | 97.4% |

`topic_train`이 41.7% 바뀌었고, `core_concept`는 거의 전체가 바뀌었다. 이는 기존 성능 저하의 주요 원인이 모델 자체보다 **라벨 품질과 feature 품질**이었을 가능성을 보여준다.

## 성능 비교

### 전체 비교표

| target | split | 기존 기준 | 기존 accuracy | 기존 macro F1 | 기존 weighted F1 | 새 accuracy | 새 macro F1 | 새 weighted F1 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| `topic_train` | stratified | `with_core_v3` | 0.6608 | 0.6569 | 0.6507 | 0.8603 | 0.8541 | 0.8621 |
| `topic_train` | time | `klue_eval_v7` | 0.4750 | 0.4659 | 0.4704 | 0.8175 | 0.8089 | 0.8164 |
| `era` | stratified | `klue_eval_v5` | 0.7656 | 0.7127 | 0.7604 | 0.9302 | 0.9266 | 0.9301 |
| `topic` | stratified | `klue_eval_v4` | 0.5810 | 0.3348 | 0.5496 | 0.8130 | 0.7056 | 0.8194 |

### 향상폭

| target | split | accuracy 향상 | macro F1 향상 | weighted F1 향상 |
|---|---|---:|---:|---:|
| `topic_train` | stratified | +0.1995 | +0.1972 | +0.2114 |
| `topic_train` | time | +0.3425 | +0.3430 | +0.3460 |
| `era` | stratified | +0.1646 | +0.2139 | +0.1697 |
| `topic` | stratified | +0.2320 | +0.3708 | +0.2698 |

모든 target에서 성능이 크게 올랐다. 특히 `topic`의 macro F1은 0.3348에서 0.7056으로 올라, 기존 세부 주제 라벨의 노이즈가 매우 컸음을 시사한다.

## target별 결과 해석

### 1. topic_train

`topic_train`은 5개 통합 주제 라벨이다.

```text
사건 / 인물 / 정치 / 문화 / 제도
```

#### stratified 결과

| 지표 | 값 |
|---|---:|
| accuracy | 0.8603 |
| macro F1 | 0.8541 |
| weighted F1 | 0.8621 |
| wrong | 56 / 401 |

`macro F1`과 `weighted F1`이 모두 높으므로, 단순히 데이터가 많은 라벨만 맞춘 결과는 아니다. 5개 통합 라벨 전반을 비교적 안정적으로 학습했다.

#### split_time_v1 결과

| 지표 | 값 |
|---|---:|
| accuracy | 0.8175 |
| macro F1 | 0.8089 |
| weighted F1 | 0.8164 |
| wrong | 73 / 400 |

71~78회차 최신 회차에서도 macro F1 0.8089를 유지했다. stratified보다 낮지만, 최신 회차 일반화가 무너진 결과는 아니다.

#### 최신 회차 예측 TOP5

`split_time_v1`의 `pred_label` 기준 최신 회차 topic_train 분포는 다음과 같다.

| 순위 | topic_train | 건수 | 비율 |
|---:|---|---:|---:|
| 1 | 사건 | 135 | 33.75% |
| 2 | 정치 | 84 | 21.00% |
| 3 | 인물 | 73 | 18.25% |
| 4 | 문화 | 60 | 15.00% |
| 5 | 제도 | 48 | 12.00% |

최신 회차에서는 `사건` 비중이 가장 높고, 그 다음 `정치`, `인물`, `문화`, `제도` 순서로 나타났다.

### 2. era

`era`는 10개 시대 라벨이다.

#### stratified 결과

| 지표 | 값 |
|---|---:|
| accuracy | 0.9302 |
| macro F1 | 0.9266 |
| weighted F1 | 0.9301 |
| wrong | 28 / 401 |
| CV macro F1 mean | 0.9080 |

시대 분류는 매우 안정적이다. `macro F1`과 `weighted F1` 차이가 작아, 다수 시대만 맞춘 결과로 보기 어렵다.

다만 소수 클래스는 support가 작아 점수를 과신하면 안 된다.

```text
고조선: 4개
초기 국가: 7개
선사 시대: 9개
```

#### 오버피팅/언더피팅 판단

언더피팅은 아니다. train loss와 validation loss가 충분히 내려갔고, test 성능도 높다.

심한 오버피팅도 아니다. CV macro F1 mean 0.9080, test macro F1 0.9266으로 차이가 크지 않다.

다만 후반 epoch에서는 train loss가 계속 내려가는 동안 validation loss가 정체 또는 소폭 상승했다. 따라서 **약한 과적합 징후는 있으나 early stopping으로 제어된 상태**로 판단한다.

### 3. topic

`topic`은 10개 세부 주제 라벨이다.

```text
경제 / 군사 / 문화 / 사건 / 사상·종교 / 사회 / 외교 / 인물 / 정치 / 제도
```

#### stratified 결과

| 지표 | 값 |
|---|---:|
| accuracy | 0.8130 |
| macro F1 | 0.7056 |
| weighted F1 | 0.8194 |
| wrong | 75 / 401 |
| CV macro F1 mean | 0.7452 |

`topic_train`보다 성능이 낮다. 하지만 기존 `topic` 결과와 비교하면 큰 폭으로 개선되었다.

```text
기존 topic macro F1: 0.3348
새 topic macro F1: 0.7056
```

즉 세부 topic도 v2 전처리 이후 훨씬 좋아졌지만, 최종 서비스용으로는 5개 통합 라벨인 `topic_train`이 더 안정적이다.

## macro F1과 weighted F1 차이가 나는 이유

`topic` 결과에서 다음 차이가 보인다.

```text
macro F1: 0.7056
weighted F1: 0.8194
```

이는 이상이라기보다 클래스 불균형 영향이다.

test 데이터의 topic 분포:

| topic | support |
|---|---:|
| 사건 | 142 |
| 인물 | 81 |
| 제도 | 51 |
| 문화 | 49 |
| 정치 | 31 |
| 경제 | 19 |
| 외교 | 10 |
| 군사 | 8 |
| 사회 | 6 |
| 사상·종교 | 4 |

`weighted F1`은 데이터가 많은 클래스의 영향을 크게 받는다. `사건`, `인물`, `문화`, `제도`는 성능이 높기 때문에 weighted F1이 높게 나온다.

반면 `macro F1`은 각 라벨을 같은 비중으로 평균낸다. support가 4개인 `사상·종교`도 support가 142개인 `사건`과 같은 비중으로 계산된다.

클래스별 F1:

| topic | F1 |
|---|---:|
| 인물 | 0.8846 |
| 사건 | 0.8764 |
| 문화 | 0.8571 |
| 경제 | 0.8293 |
| 제도 | 0.8235 |
| 군사 | 0.6957 |
| 사상·종교 | 0.6667 |
| 정치 | 0.5588 |
| 외교 | 0.5000 |
| 사회 | 0.3636 |

따라서 `topic` 실험의 핵심 해석은 다음이다.

```text
큰 클래스와 주요 세부 topic은 잘 맞춘다.
하지만 사회/외교/정치 같은 소수 또는 경계가 애매한 topic이 약하다.
그래서 weighted F1은 높고 macro F1은 낮다.
```

## 왜 성능이 향상됐나

### 1. 라벨 노이즈 감소

v1은 키워드/규칙 기반이라 문항의 중심 개념보다 표면 단어에 끌릴 수 있었다. v2는 GPT가 문항 전체 문맥을 보고 라벨을 다시 판단했다.

특히 `topic`과 `topic_train`이 각각 약 40% 이상 변경되었다.

```text
topic 변경: 652 / 1600
topic_train 변경: 667 / 1600
```

이는 기존 라벨이 모델 학습에 충분히 안정적이지 않았다는 뜻이다.

### 2. core_concept 품질 향상

v1의 `core_concept`는 기존 topic 또는 키워드 추출에 가까웠다. v2의 `core_concept`는 GPT가 문항 중심 개념을 요약한 값이다.

`core_concept`는 1559건이 바뀌었다. 이후 `text_with_core`에 포함되어 모델 입력으로 들어갔다.

이 feature는 모델에게 강한 문맥 힌트로 작동했을 가능성이 높다.

### 3. topic_train 기준이 더 일관됨

v1은 다음 구조였다.

```text
topic 추론
-> 규칙으로 topic_train 통합
```

v2는 GPT가 `topic`과 `topic_train_v2`를 함께 판단했다.

```text
문항 문맥
-> topic 판단
-> 학습용 topic_train_v2 추천
```

그래서 `topic_train`이 단순 사후 매핑이 아니라, 문항의 중심 판단 기준에 맞게 정리되었다.

### 4. learning rate를 낮춰 안정화

이번 주요 실험은 `learning_rate=5e-6`을 사용했다.

`2e-5`보다 4배 작은 값이라 학습 속도는 느리지만, 작은 데이터셋에서 사전학습된 KLUE/RoBERTa 표현을 덜 흔들고 안정적으로 fine-tuning할 수 있다.

현재 데이터는 train 약 1200건으로 크지 않고, 일부 라벨 경계가 애매하다. 이런 조건에서는 `5e-6`이 더 안정적인 선택이다.

## 왜 topic 성능은 topic_train보다 떨어지나

`topic_train`은 5개 통합 라벨이다.

```text
사건 / 인물 / 정치 / 문화 / 제도
```

반면 `topic`은 10개 세부 라벨이다.

```text
경제 / 군사 / 문화 / 사건 / 사상·종교 / 사회 / 외교 / 인물 / 정치 / 제도
```

세부 라벨은 다음 문제가 있다.

1. 라벨 수가 많다.
2. 소수 클래스가 많다.
3. `정치/사회/외교/군사/경제`는 문항에 따라 경계가 흐리다.
4. `사건`과 `군사`, `정치`와 `제도`, `문화`와 `사상·종교`는 서로 섞일 수 있다.

실제 주요 오분류도 이 경계에서 발생했다.

| 오분류 | 건수 |
|---|---:|
| 사건 -> 정치 | 8 |
| 사건 -> 외교 | 6 |
| 사건 -> 군사 | 6 |
| 정치 -> 제도 | 5 |
| 인물 -> 정치 | 4 |
| 제도 -> 정치 | 4 |
| 문화 -> 사상·종교 | 3 |

따라서 `topic`은 세부 분석용으로는 의미가 있지만, 최종 추천/서비스용 주요 모델은 `topic_train`이 더 안정적이다.

## Cross Validation 그래프 해석

CV 그래프는 fold별 train loss와 validation loss를 보여준다.

```text
train loss
= 학습 데이터에서의 오차

validation loss
= 학습에 직접 쓰지 않은 검증 fold에서의 오차
```

좋은 패턴:

```text
train loss와 validation loss가 함께 내려간다.
어느 시점 이후 validation loss가 정체되거나 상승한다.
그 직전 또는 최소 validation loss 지점이 best epoch이다.
```

`topic` 실험의 fold별 best epoch:

| fold | best val loss epoch | best val loss | last val loss |
|---|---:|---:|---:|
| fold 1 | 9 | 1.0703 | 1.1234 |
| fold 2 | 12 | 0.8024 | 0.8852 |
| fold 3 | 11 | 0.7664 | 0.7860 |

best epoch 이후에는 train loss가 계속 내려가지만 validation loss는 올라간다. 이는 약한 과적합 징후다.

하지만 early stopping이 적용되어 크게 무너지기 전에 멈췄고, final epoch도 평균 best epoch에 가까운 11로 설정되었다.

```text
best_epoch_mean: 10.67
final_epochs: 11
```

따라서 `topic` 실험은 심한 오버피팅은 아니지만, 세부 라벨 수와 클래스 불균형 때문에 더 어려운 문제로 판단한다.

## 최종 판단

### 성공한 부분

- v2 전처리로 `topic_train`, `era`, `topic` 모두 기존보다 성능이 크게 향상되었다.
- `topic_train`은 stratified와 time split 모두에서 안정적이다.
- `era`는 매우 높은 성능을 보이며, 시대 분류 모델로 사용 가능성이 높다.
- `topic`도 기존보다 크게 좋아졌지만, 세부 라벨 특성상 불안정한 클래스가 남아 있다.

### 주의할 부분

- v2 전처리와 `core_concept`는 GPT 기반이며, 전처리 단계에서 정답 선택지를 참고했다.
- 따라서 현재 성능은 "GPT가 정리한 v2 라벨 체계와 core_concept를 사용하는 조건"에서의 성능이다.
- 실제 신규 문항에서 정답 선택지 없이 예측해야 한다면, `answer_choice` 제외 전처리 또는 `input_text only` 실험이 추가로 필요하다.

### 추천 사용 방식

| 목적 | 추천 모델 |
|---|---|
| 통합 주제 예측 | `topic_train` 모델 |
| 시대 예측 | `era` 모델 |
| 세부 주제 분석 | `topic` 모델 |
| 최종 서비스/추천 기준 | `topic_train + era` 조합 |

현재 기준 최종 추천 구조는 다음이 가장 안정적이다.

```text
문항 입력
-> era 모델로 시대 예측
-> topic_train 모델로 통합 주제 예측
-> topic 모델은 세부 분석/보조 정보로 사용
```


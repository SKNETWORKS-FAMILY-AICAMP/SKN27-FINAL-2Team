# 선지 오류 2차 검수 모델 사용 가이드

## 1. 목적

이 모델은 문제 생성 팀원이 만든 한국사 문항을 2차로 검수하기 위한 모델이다.

목표는 정답 번호를 맞히는 것이 아니다.  
목표는 **각 선지 1개가 검수상 정상인지, 이상이 있는지 분류하는 것**이다.

즉, 하나의 문항에 선지가 5개 있다면 모델은 문항 1개를 한 번에 평가하는 것이 아니라, 선지 5개를 각각 나누어 평가한다.

```text
문항 1개
├─ 선지 1번 검사
├─ 선지 2번 검사
├─ 선지 3번 검사
├─ 선지 4번 검사
└─ 선지 5번 검사
```

모델의 최종 역할은 사람이 모든 선지를 처음부터 다 보는 부담을 줄이고, **검수 우선순위가 높은 선지를 먼저 보여주는 것**이다.

## 2. 입력 데이터

모델이 직접 학습하고 판단하는 입력은 다음 4가지다.

```text
지문
질문
선지 1개
해당 선지가 정답인지 여부
```

예시는 다음과 같다.

```text
[지문]
1446년 세종 28년에 새로 만들어진 글자를 풀이한 한문 해설서이다...

[질문]
다음 자료에 대한 설명으로 옳은 것은?

[선지]
간송미술관 소장본이 대표적이다.

[정답 여부]
정답 선지
```

주의할 점은, 모델이 **선지 5개 중 정답을 맞히는 구조가 아니라는 것**이다.  
정답 여부는 모델이 맞혀야 하는 값이 아니라, 입력 정보로 제공되는 값이다.

## 3. y값

학습 y값은 이진 분류 라벨이다.

```text
label=0: 선지 오류 있음
label=1: 선지 오류 없음
```

예를 들어 정답 선지가 지문에 그대로 노출되어 있거나, 정답 선지만 유독 짧거나, 선지 문장 자체가 이상하면 `label=0`이다.

반대로 기출문제처럼 검증된 정상 선지는 `label=1`로 사용한다.

## 4. 오류 코드

`error_codes`는 모델이 직접 학습하는 y값이 아니라, 사람이 결과를 해석하기 위한 보조 설명값이다.

현재 사용하는 주요 오류 코드는 다음과 같다.

```text
ANSWER_IN_PASSAGE
정답 선지가 지문/질문에 노출됨

ANSWER_LENGTH_BIAS
정답 선지가 다른 선지에 비해 유독 길거나 짧음

CHOICE_FORMAT_ERROR
선지 문장 또는 형식이 이상함

QUESTION_MARKER_MISMATCH
지문에 없는 밑줄, 표식, (가), (나) 등을 선지에서 참조함

WEIRD_CHOICE
문제 맥락상 너무 이상하거나 부적절한 선지

DUPLICATE_OR_SIMILAR_CHOICE
중복되거나 거의 같은 선지가 있음

NO_OR_MULTI_ANSWER
정답이 없거나 2개 이상임
```

여기서 `DUPLICATE_OR_SIMILAR_CHOICE`, `NO_OR_MULTI_ANSWER`는 선지 1개만 보고 판단하기 어렵기 때문에 BERT 모델이 직접 학습하기보다는 규칙 검사로 처리한다.

## 5. 전처리 과정

전처리는 문항 데이터를 모델이 학습할 수 있는 선지 단위 데이터로 바꾸는 과정이다.

원본 문항은 보통 다음처럼 구성되어 있다.

```json
{
  "material": "지문",
  "question": "질문",
  "choices": [
    {"number": 1, "text": "선지 1", "is_answer": false},
    {"number": 2, "text": "선지 2", "is_answer": false},
    {"number": 3, "text": "선지 3", "is_answer": true},
    {"number": 4, "text": "선지 4", "is_answer": false},
    {"number": 5, "text": "선지 5", "is_answer": false}
  ]
}
```

전처리 후에는 선지 1개가 row 1개가 된다.

```json
{
  "passage": "지문",
  "question": "질문",
  "choice_no": 3,
  "choice": "선지 3",
  "is_answer": 1,
  "label": 0,
  "error_codes": ["ANSWER_IN_PASSAGE"]
}
```

문항 1개에 선지가 5개라면 전처리 후 row는 5개가 된다.

```text
문항 1개 x 선지 5개 = 학습 row 5개
```

## 6. 사용 데이터

현재 v8 기준으로 사용하는 데이터는 다음과 같다.

```text
기출문제 데이터
- 정상 선지 데이터로 사용
- 기출문제는 이미 검증된 문항이므로 label=1로 사용

팀원 생성 문제 API 전수검사 데이터
- 이상 선지 데이터로 사용
- 오류 유형이 확인된 선지를 label=0으로 사용
```

v8 데이터 요약은 다음과 같다.

```text
전체 row: 10,790개
train: 8,725개
test: 2,065개

정상 선지: 10,159개
이상 선지: 631개
```

학습 파일은 다음 2개다.

```text
choice_quality_train_v8.json
choice_quality_test_v8.json
```

## 7. RunPod 학습 파일 구조

RunPod에서는 `/workspace/common` 폴더에 학습 데이터를 넣는다.

```text
/workspace/
├─ train_choice_quality_runpod_v8.ipynb
└─ common/
   ├─ choice_quality_train_v8.json
   └─ choice_quality_test_v8.json
```

`common` 폴더는 계속 공통 데이터 폴더로 사용한다.  
버전이 바뀌면 폴더를 바꾸는 것이 아니라, 안에 들어가는 데이터 파일명만 바뀐다.

## 8. 학습 방식

모델은 BERT 계열인 `klue/roberta-base`를 사용한다.

학습 방식은 다음과 같다.

```text
1. train 데이터를 불러온다.
2. train 내부에서 validation 데이터를 나눈다.
3. 지문 + 질문 + 선지 1개 + 정답 여부를 하나의 text로 만든다.
4. BERT 모델이 label=0/1을 분류하도록 학습한다.
5. validation 성능을 보면서 early stopping을 적용한다.
6. test 데이터로 최종 평가한다.
7. 모델과 결과 CSV를 저장한다.
```

validation은 학습 중 성능 확인용이고, test는 최종 평가용이다.

## 9. 학습 후 생성되는 결과

학습이 끝나면 `/workspace/choice_quality_output_v8`에 결과가 저장된다.

주요 파일은 다음과 같다.

```text
results.json
전체 평가 결과와 성능 지표

test_predictions.csv
test 데이터에 대한 선지별 상세 예측 결과

test_review.csv
팀원이 실제 검수할 때 보는 간단 결과 파일

valid_predictions.csv
validation 데이터에 대한 선지별 상세 예측 결과

valid_review.csv
validation 데이터에 대한 간단 검수 결과 파일

threshold_report.csv
threshold별 성능 비교

test_question_rule_report.csv
문항 단위 규칙 검사 결과

model/
학습된 모델 파일
```

## 10. 결과 컬럼 해석

팀원이 주로 확인해야 하는 파일은 `test_review.csv` 또는 실제 운영 데이터의 review CSV다.

`test_predictions.csv`는 개발자/분석용 상세 파일이고, 컬럼이 많기 때문에 일반 검수자가 매번 볼 필요는 없다.

`test_review.csv`의 주요 컬럼은 다음과 같다.

```text
검수상태
검수필요, 참고검수, 통과 중 하나다.

우선순위
HIGH, MEDIUM, LOW 중 하나다.

오류확률
모델이 이 선지를 이상하다고 본 확률이다.

판단근거
model, rule, model+rule, none 중 하나다.

오류코드
왜 이상하다고 봤는지 나타낸다.

지문, 질문, 선지
사람이 직접 확인할 원문 정보다.
```

검수 기준은 다음처럼 보면 된다.

```text
검수상태=검수필요
반드시 먼저 확인한다.

검수상태=참고검수
규칙 검사에 걸린 항목이다. 오탐 가능성이 있으므로 참고용으로 확인한다.

검수상태=통과
2차 검수 기준에서 이상 가능성이 낮다.
```

상세 분석이 필요할 때만 `test_predictions.csv`를 본다.

중요 컬럼은 다음과 같다.

```text
true_label
실제 라벨이다. 평가 데이터에서만 존재한다.
0이면 실제 이상 선지, 1이면 실제 정상 선지다.

model_pred_label
BERT 모델 단독 판단이다.
0이면 모델이 이상 선지라고 판단한 것, 1이면 정상이라고 판단한 것이다.

rule_label
규칙 검사 결과다.
0이면 규칙상 이상이 발견된 것, 1이면 규칙상 이상이 없는 것이다.

final_label
최종 판단이다.
모델 또는 규칙 중 하나라도 이상이면 0이다.

error_prob
모델이 이 선지를 이상하다고 본 확률이다.

주의할 점은 `error_prob >= 0.8`이 에러 판정 기준이라는 뜻은 아니라는 것이다.  
실제 모델 판정 기준은 validation에서 찾은 `best_threshold`를 사용한다. v8 결과에서는 `best_threshold=0.2`였다.

```text
error_prob >= best_threshold
모델이 이상 선지로 판단

error_prob >= 0.8
모델이 매우 강하게 이상하다고 본 고확신 후보
```

따라서 `0.8`은 사람이 먼저 볼 선지를 정하기 위한 우선순위 기준이다.

decision_source
어디에서 이상을 잡았는지 나타낸다.
model, rule, model+rule, none 중 하나다.

final_error_codes
최종 오류 코드다.

review_priority
검수 우선순위다.
HIGH, MEDIUM, LOW 중 하나다.
```

## 11. 팀원이 실제로 사용하는 방법

팀원이 문제를 생성한 뒤에는 다음 순서로 사용하면 된다.

```text
1. 생성한 문제 JSON을 준비한다.
2. 문제를 선지 단위 데이터로 전처리한다.
3. 학습된 모델로 선지별 오류 확률을 계산한다.
4. 규칙 검사 결과와 모델 결과를 합친다.
5. final_label=0인 선지를 먼저 검수한다.
6. final_error_codes를 보고 어떤 오류인지 확인한다.
7. 문제가 있으면 해당 선지를 수정하거나 문항을 다시 생성한다.
```

팀원이 가장 먼저 보면 되는 기준은 다음과 같다.

```text
final_label=0
또는
review_priority=HIGH
```

이 선지는 바로 통과시키지 말고 사람이 확인해야 한다.

## 12. 예시 해석

예시 1.

```text
final_label=0
decision_source=rule
final_error_codes=NO_OR_MULTI_ANSWER
```

의미:

```text
모델이 아니라 규칙 검사에서 이상을 잡았다.
정답이 없거나 2개 이상일 가능성이 있다.
문항 전체를 확인해야 한다.
```

예시 2.

```text
final_label=0
decision_source=model
final_error_codes=WEIRD_CHOICE
```

의미:

```text
BERT 모델이 선지 맥락이 이상하다고 판단했다.
명확한 규칙 오류는 아니므로 사람이 직접 확인해야 한다.
```

예시 3.

```text
final_label=0
decision_source=model+rule
final_error_codes=ANSWER_IN_PASSAGE
```

의미:

```text
모델과 규칙이 모두 이상 가능성을 잡았다.
정답 선지가 지문이나 질문에 노출되어 있을 가능성이 높다.
우선순위 높게 검수해야 한다.
```

## 13. 현재 모델이 잘 잡는 오류

현재 구조에서 비교적 잘 잡을 수 있는 오류는 다음과 같다.

```text
정답 선지가 지문/질문에 노출된 경우
정답 선지가 다른 선지보다 유독 짧거나 긴 경우
선지 문장 형식이 이상한 경우
메타 표현이나 풀이 문장이 선지에 들어간 경우
중복 선지 또는 정답 개수 오류
```

## 14. 현재 모델의 한계

이 모델은 2차 보안용 검수 모델이므로 모든 오류를 완벽히 잡는 용도는 아니다.

현재 한계는 다음과 같다.

```text
역사적 사실 자체가 맞는지 검증하는 기능은 약하다.
외부 역사 DB나 LLM 검증 없이 사실성 오류를 안정적으로 잡기는 어렵다.

WEIRD_CHOICE는 명확한 오류 코드라기보다 재검토 후보에 가깝다.

오류 유형별 데이터 수가 적은 항목은 성능이 낮을 수 있다.

기출문제와 팀원 생성 문제의 문체가 다르기 때문에 실제 운영 데이터가 더 필요하다.

final_label=1이어도 100% 정상이라는 의미는 아니다.
2차 검수 기준에서 이상 가능성이 낮다는 의미다.
```

## 15. 팀원에게 전달할 핵심 요약

```text
이 모델은 문제의 정답을 맞히는 모델이 아니다.
문항의 선지 1개씩을 보고 이상 여부를 판단하는 2차 검수 모델이다.

생성한 문제를 선지 단위로 전처리한 뒤 모델에 넣으면,
각 선지마다 오류 확률과 최종 검수 여부가 나온다.

팀원은 final_label=0 또는 review_priority=HIGH인 선지를 우선 확인하면 된다.

단, 역사적 사실성 검증은 이 모델의 주 목적이 아니므로 별도 검증이 필요하다.
```

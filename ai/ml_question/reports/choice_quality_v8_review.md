# 선지 오류 검수 모델 v8 개선 및 허점 검수

## 목적

문제 생성 팀원이 만든 한국사 문항을 2차 보안 목적으로 검수한다.  
현재 모델의 역할은 정답 번호를 맞히는 것이 아니라, `지문 + 질문 + 선지 1개 + 정답 여부`를 입력받아 해당 선지가 검수상 정상인지 이상인지 이진 분류하는 것이다.

## y값

- `label=0`: 선지 오류 있음
- `label=1`: 선지 오류 없음

`error_codes`는 학습 y가 아니라 사람이 결과를 해석하기 위한 보조 설명값이다.

## v7에서 확인된 허점

- 모델이 이상 선지라고 잡아도 후처리 규칙에 걸리지 않으면 `UNKNOWN_ERROR_TYPE`으로 남았다.
- 중복 선지, 정답 0개/2개 이상 같은 문항 단위 오류는 선지 1개 입력만으로 확인하기 어렵다.
- 정답 선지가 다른 선지보다 유독 짧거나 긴 오류가 너무 적게 라벨링되어 있었다.
- `ANSWER_IN_PASSAGE`는 단순 문자열 포함만 보면 놓치는 경우가 있고, 핵심어 겹침만 보면 과탐 가능성이 있다.
- 역사적 사실성 검증은 외부 역사 지식/검색/LLM 평가가 필요한 영역이라 BERT 단독 검사 범위에서 제외해야 한다.

## v8 개선 내용

- `choice_quality_train_v8.json`, `choice_quality_test_v8.json` 생성
- 각 row에 `context` 추가
  - `all_choices`: 같은 문항의 5개 선지 요약
  - `answer_numbers`: 정답 번호 목록
  - `answer_count`: 정답 개수
  - `question_rule_codes`: 문항 단위 규칙 검사 코드
- 정답 선지 길이 편향 기준 개선
  - 절대적으로 짧은 경우
  - 다른 선지 중앙 길이 대비 유독 짧거나 긴 경우
- 완전 중복뿐 아니라 거의 포함 관계인 약한 중복도 규칙 후보로 검출
- 결과 CSV에 모델/규칙/최종 판정을 분리
  - `model_pred_label`: BERT 모델 단독 판단
  - `rule_label`: 규칙 검사 판단
  - `final_label`: 모델 또는 규칙 중 하나라도 이상이면 최종 이상
  - `decision_source`: `model`, `rule`, `model+rule`, `none`
  - `final_error_codes`: 최종 오류 코드
- 모델이 이상이라고 봤지만 규칙으로 특정되지 않는 경우 `WEIRD_CHOICE` 후보로 남김

## v8 데이터 요약

- 전체 row: 10,790개
- train: 8,725개
- test: 2,065개
- 정상 선지: 10,159개
- 이상 선지: 631개

오류 코드별 데이터 수:

- `ANSWER_IN_PASSAGE`: 368개
- `ANSWER_LENGTH_BIAS`: 153개
- `CHOICE_FORMAT_ERROR`: 184개
- `QUESTION_MARKER_MISMATCH`: 2개

## 현재 남은 허점

- `QUESTION_MARKER_MISMATCH`는 데이터가 2개뿐이라 학습 성능을 기대하기 어렵다.
- `WEIRD_CHOICE`는 명확한 규칙형 오류가 아니라 모델이 이상하다고 본 재검토 후보에 가깝다.
- 역사적 사실 오류는 현재 BERT 모델의 목적에서 제외되어 있으므로 별도 LLM/API 검증 또는 역사 DB 검증이 필요하다.
- 팀원 생성 문제 문체와 기출 문제 문체가 달라서, 실제 운영 성능은 팀원 생성 문제 라벨을 더 추가해야 안정화된다.
- `ANSWER_IN_PASSAGE`는 핵심어 기반 보조 규칙을 추가했지만 과탐/미탐 가능성이 있으므로 사람이 샘플을 검수하며 기준을 조정해야 한다.

## RunPod 사용 파일

```text
/workspace/
├─ train_choice_quality_runpod_v8.ipynb
└─ common/
   ├─ choice_quality_train_v8.json
   └─ choice_quality_test_v8.json
```

## 운영 판단 방식

1. BERT가 선지 오류 확률을 계산한다.
2. threshold 이상이면 `model_pred_label=0`으로 이상 선지 후보 처리한다.
3. 중복 선지, 정답 개수, 길이 편향, 지문 노출 등은 규칙으로 추가 확인한다.
4. 모델 또는 규칙 중 하나라도 이상이면 `final_label=0`으로 최종 검수 대상이 된다.
5. 사람이 `final_error_codes`, `decision_source`, `review_priority`를 보고 우선 검수한다.

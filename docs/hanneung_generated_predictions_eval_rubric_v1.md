# 한능검 generated_predictions 평가 루브릭 v1

## 1. 목적

이 문서는 LLaMAFactory `generated_predictions*.jsonl` 결과를 LLM judge 또는 사람 검수자가 평가하기 위한 기준이다.

평가 대상은 완성된 5지선다 문항이 아니라, 모델이 생성한 아래 3요소이다.

- 사료/자료 조건을 제대로 사용했는가
- 발문(question)이 한능검 심화 문항으로 성립하는가
- 정답 선지(answer_choice)가 입력 근거와 역사 사실에 맞는가

현재 기준은 오답 선지, 해설, 정답 번호, 배점을 평가하지 않는다.

## 2. 입력 구조

`generated_predictions*.jsonl` 한 줄을 하나의 평가 단위로 본다.

일반적인 입력 구조는 다음과 같다.

```json
{
  "prompt": "system/user chat prompt containing input JSON",
  "predict": "{\"question\":\"...\",\"answer_choice\":\"...\"}",
  "label": "{\"question\":\"...\",\"answer_choice\":\"...\"}"
}
```

`prompt` 안의 사용자 입력 JSON에서 다음 필드를 추출해 평가한다.

| 필드 | 용도 |
|---|---|
| material | 모델이 참조해야 하는 사료/자료 지문 |
| answer_fact_basis | 정답 선지를 만들 때 반드시 근거로 삼아야 하는 검증 사실 |
| topic_type | 출제 대상 유형 |
| topic | 출제 대상 |
| material_type | 자료 제시 방식 |
| major_type | 대분류 |
| minor_type | 세부분류 |
| question_task | 문항 유형 |
| question_task_instruction | 해당 유형의 생성 지시 |
| difficulty_label | 목표 난이도 참고 |

`label`은 의미 비교용 참고값이다. 문자열 exact match 기준으로 평가하지 않는다.

## 3. 운영 원칙

- 한 번에 `generated_predictions` 1행만 평가한다.
- `predict`가 생성한 `question`, `answer_choice`만 모델 출력으로 본다.
- `material`은 입력 자료이며, 모델이 새로 만들거나 요약해야 하는 출력물이 아니다.
- `answer_choice`는 반드시 `answer_fact_basis`의 사실에 근거해야 한다.
- `label`과 표현이 달라도 역사적 의미가 같고 발문에 적합하면 정답으로 인정한다.
- `label`과 다르더라도 `answer_fact_basis`가 더 명확하게 지지하면 허용 가능하다.
- 입력 근거 자체가 부족하거나 잘못된 경우 모델 오류와 데이터 오류를 분리해서 기록한다.
- 역사 사실성 `uncertain`은 PASS가 아니며 `needs_verification`으로 기록한다.
- 형식이 그럴듯하다는 이유로 사료-질문-정답 연결 검증을 생략하지 않는다.

## 4. 평가 결과 상태

| 상태 | 의미 |
|---|---|
| PASS | 평가자가 충분히 판단 가능하고 핵심 조건을 만족 |
| FAIL | 명백한 오류 |
| UNCERTAIN | 역사 사실 또는 입력 근거 문제 때문에 확정 불가 |
| INPUT_ISSUE | 모델 출력보다 입력 데이터의 근거 부족/오류가 핵심 문제 |

## 5. Gate

Gate는 최소 성립 조건이다. 하나라도 FAIL이면 총점과 관계없이 `regenerate` 또는 `needs_data_fix`로 처리한다.

| Gate | 항목 | FAIL 기준 |
|---|---|---|
| G1 | 예측 JSON 형식 | `predict`가 JSON 객체가 아니거나 파싱 불가 |
| G2 | 출력 키 제한 | 출력에 `question`, `answer_choice` 외의 핵심 키가 포함됨 |
| G3 | 필수 출력 존재 | `question` 또는 `answer_choice`가 비어 있음 |
| G4 | 금지 출력 누출 | 오답 선지 목록, 정답 번호, 해설, choice_explanations, material 재생성 등이 출력됨 |
| G5 | 발문 의미 판단 가능성 | 발문이 문장 파손, 누락, 비문 때문에 평가 불가 |
| G6 | 사료 지시어 정합성 | 발문이 `(가)`, 밑줄, 지도, 순서 등 material에 없는 지시어를 요구함 |
| G7 | question_task 적합성 | 생성 발문이 입력 `question_task` 또는 `question_task_instruction`과 명백히 다름 |
| G8 | 사료-발문 연결 | 발문이 material에서 추론 가능한 대상·시대·사건을 묻지 않음 |
| G9 | 정답 근거 충실성 | `answer_choice`가 `answer_fact_basis`에 없는 사실을 핵심 정답으로 생성함 |
| G10 | 정답 역사 사실성 | `answer_choice`가 한국사 사실과 명백히 충돌함 |
| G11 | 사료-정답 대상 일치 | material이 가리키는 대상과 answer_choice의 주체·시대·사건이 다름 |
| G12 | 직접 복붙 정답 | answer_choice가 material의 핵심 문장을 그대로 반복해 사료 매칭만으로 정답이 노출됨 |

## 6. INPUT_ISSUE 처리

아래 상황은 모델 단독 실패로 보지 말고 `input_data_issue`에 기록한다.

| 상황 | 처리 |
|---|---|
| `answer_fact_basis`가 너무 짧아 정답 사실을 판단하기 어려움 | INPUT_ISSUE, 필요 시 모델 평가는 보류 |
| `answer_fact_basis`가 label 정답과 다른 사실을 지지함 | INPUT_ISSUE 또는 label_mismatch |
| material 자체가 OCR 파손으로 대상 추론이 어려움 | INPUT_ISSUE |
| question_task가 material 구조와 맞지 않음 | INPUT_ISSUE |
| 지도/이미지형인데 material에 위치 판단 정보가 없음 | INPUT_ISSUE |

입력 문제가 있더라도 모델이 입력에 충실하게 답했다면 `model_error=false`로 기록할 수 있다.

## 7. 유형별 판정 규칙

### standard_select

- 발문은 material의 대상·시대·사건을 추론하게 해야 한다.
- answer_choice는 그 대상에 대한 옳은 설명이어야 한다.
- label과 문장이 달라도 같은 역사 사실이면 semantic match로 인정한다.

### negative_select

- 발문은 "적절하지 않은 것", "옳지 않은 것"처럼 부정 선택 조건을 명확히 가져야 한다.
- answer_choice는 material의 대상·시대·사건에 맞지 않는 선지여야 한다.
- 오답 선지가 역사적으로 참인 다른 시대/대상 사실인 것은 허용된다.
- 실제 역사에서 성립하지 않는 허위 서술이면 역사 사실성 FAIL로 본다.

### order

- 발문은 사건·자료의 순서 배열을 요구해야 한다.
- answer_choice는 `(가)-(나)-(다)`처럼 순서 답변 형식이어야 한다.
- 순서 중 하나라도 역사적으로 틀리면 FAIL이다.

### timeline_position / period_between

- 발문은 연표의 위치 또는 두 사건 사이 시기를 묻는 구조여야 한다.
- answer_choice는 위치 기호 또는 해당 시기 사실이어야 한다.
- material/label에 위치 기호가 있는데 다른 기호를 출력하면 FAIL이다.

### map_location

- 발문은 지도 위치를 찾는 형태여야 한다.
- answer_choice는 위치 기호를 명확히 제시해야 한다.
- material 조건과 위치 기호가 불일치하면 FAIL이다.
- 위치 기호 대신 관련 설명만 출력하면 `question_task` 부적합 또는 부분점수로 처리한다.

## 8. 사료 평가 5점

Gate 통과 후 평가한다. 각 항목은 yes=1점, no/uncertain=0점이다.

| 항목 | 1점 기준 |
|---|---|
| 자료 대상 식별 가능 | material을 통해 topic 또는 출제 대상이 자연스럽게 추론된다 |
| 발문 지시어 일치 | 발문의 `(가)`, 밑줄, 이 국가, 이 인물 등이 material 구조와 일치한다 |
| 자료 왜곡 없음 | 발문이나 정답이 material의 주체·시기·사건을 바꾸지 않는다 |
| 직접 정답 노출 조절 | material이 answer_choice 핵심 문장을 그대로 노출하지 않는다 |
| 자료 활용성 | question을 풀 때 material을 실제로 사용해야 한다 |

## 9. 발문 평가 5점

| 항목 | 1점 기준 |
|---|---|
| 한능검 문체 | 실제 한능검 심화 발문처럼 간결하고 자연스럽다 |
| 유형 적합성 | `question_task_instruction`과 발문 구조가 맞다 |
| 답변 가능성 | answer_choice가 발문에 대한 답으로 문법적·의미적으로 들어맞는다 |
| 범위 명확성 | 어떤 대상·시기·사건에 대해 묻는지 모호하지 않다 |
| 난이도 적합성 | `difficulty_label` 대비 너무 직접적이거나 과도하게 난해하지 않다 |

## 10. 정답 평가 5점

| 항목 | 1점 기준 |
|---|---|
| 근거 충실성 | answer_choice 핵심 사실이 `answer_fact_basis`에서 지지된다 |
| 역사 사실성 | answer_choice가 한국사 사실과 충돌하지 않는다 |
| 사료 대상 일치 | material이 가리키는 대상과 answer_choice의 주체가 일치한다 |
| label 의미 일치 | label과 같거나, 같은 의미의 역사 사실로 인정 가능하다 |
| 표현 품질 | 선지 문장이 한능검 선택지처럼 짧고 명확하다 |

## 11. 의미 일치 판정

`label` 비교는 exact match가 아니라 semantic match로 판정한다.

| 판정 | 기준 |
|---|---|
| match | label과 같은 역사 사실을 다른 표현으로 말함 |
| partial | 핵심 방향은 맞지만 주체, 시기, 결과, 범위 중 일부가 약함 |
| mismatch | label 또는 answer_fact_basis와 다른 역사 사실을 말함 |
| not_applicable | label이 비어 있거나 입력 근거가 label보다 우선되는 특수 상황 |

예시:

| label | predict | 판정 |
|---|---|---|
| 공산 전투에서 고려군에 대승을 거두었다 | 공산 전투에서 고려군을 상대로 대승을 거두었다 | match |
| 거칠부가 국사를 편찬하였다 | 진흥왕이 거칠부 등에게 명하여 신라의 역사서를 편찬케 하였다 | match |
| (다) | 가 - 임병찬 순지비가 세워진 지역 | mismatch |

## 12. 최종 판정

| 조건 | 최종 처리 |
|---|---|
| Gate FAIL | regenerate |
| INPUT_ISSUE가 핵심 원인 | needs_data_fix |
| Gate UNCERTAIN | needs_verification |
| Gate PASS, 13~15점 | accept |
| Gate PASS, 11~12점 | accept_with_warning |
| Gate PASS, 8~10점 | revise |
| Gate PASS, 7점 이하 | regenerate |

## 13. Judge 출력 JSON 스키마

LLM judge는 아래 JSON만 출력한다.

```json
{
  "row_id": "",
  "predict_json_valid": true,
  "output_keys_valid": true,
  "gate_status": "PASS",
  "failed_gates": [],
  "uncertain_gates": [],
  "input_data_issue": [],
  "scores": {
    "material": 0,
    "question": 0,
    "answer": 0,
    "total": 0
  },
  "semantic_match_to_label": "match",
  "basis_faithfulness": "supported",
  "historical_accuracy": "pass",
  "decision": "accept",
  "model_error": false,
  "short_reason": "",
  "fix_suggestion": ""
}
```

허용값은 다음과 같다.

| 필드 | 허용값 |
|---|---|
| gate_status | PASS, FAIL, UNCERTAIN, INPUT_ISSUE |
| semantic_match_to_label | match, partial, mismatch, not_applicable |
| basis_faithfulness | supported, partially_supported, unsupported, input_insufficient |
| historical_accuracy | pass, fail, uncertain |
| decision | accept, accept_with_warning, revise, regenerate, needs_verification, needs_data_fix |

## 14. 평가 절차

| 단계 | 작업 |
|---|---|
| 1 | `prompt`에서 입력 JSON을 추출한다 |
| 2 | `predict`를 JSON으로 파싱한다 |
| 3 | 출력 키가 `question`, `answer_choice`만 있는지 확인한다 |
| 4 | material, answer_fact_basis, question_task_instruction을 추출한다 |
| 5 | question이 material 구조와 question_task에 맞는지 확인한다 |
| 6 | answer_choice의 핵심 역사 명제를 추출한다 |
| 7 | 핵심 명제가 answer_fact_basis로 지지되는지 확인한다 |
| 8 | material이 가리키는 대상과 answer_choice 주체가 일치하는지 확인한다 |
| 9 | label과 의미 일치 여부를 판정하되 exact match를 요구하지 않는다 |
| 10 | Gate, 점수, 최종 decision을 JSON으로 작성한다 |

## 15. 주의할 오판

- BLEU/ROUGE가 낮아도 의미가 같으면 accept 가능하다.
- label과 다르다는 이유만으로 fail 처리하지 않는다.
- answer_fact_basis가 틀렸는데 모델이 그 근거를 따른 경우는 모델 오류와 데이터 오류를 분리한다.
- 사료에 나온 문장을 정답 선지가 그대로 반복하면 좋은 성능이 아니라 직접 노출 문제로 본다.
- map_location, timeline_position은 의미가 비슷해도 기호가 틀리면 대체로 FAIL이다.
- 부정형 문항은 정답 선지가 "역사적으로 틀린 말"이 아니라 "조건에 맞지 않는 말"일 수 있다.

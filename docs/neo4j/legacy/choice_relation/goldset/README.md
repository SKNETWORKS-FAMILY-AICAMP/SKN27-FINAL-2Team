# 정답–오답 관계 seed 기준표

> 레거시 문서: 별도 오답 관계 추출 실험용 seed 설명이다.

`seed_expected_relations.csv`는 관계 추출 파이프라인을 처음 검증하기 위한
5문항·20개 정답–오답 쌍의 초기 기준표다.

- 자동 생성된 정답이 아니다.
- 한국사 전문가 검수를 마친 공식 goldset도 아니다.
- 실제 LLM 결과의 형식과 관계 분류가 상식적인 방향인지 확인하는 seed다.
- `review_status=INITIAL_SEED`인 동안 공식 정확도 주장에 사용하지 않는다.

## 검수할 열

| 열 | 의미 |
|---|---|
| `gold_primary_relation_type` | 오답을 만드는 가장 직접적인 관계 |
| `gold_secondary_relation_types_json` | 동시에 작동하는 보조 관계 |
| `gold_reason` | 관계를 선택한 근거 |
| `review_status` | `INITIAL_SEED` 또는 추후 `REVIEWED` |

모델 결과가 생기면 `problem_id`, `answer_choice_id`,
`distractor_choice_id`를 복합 키로 비교한다.

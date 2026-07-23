# CSV 검수 방법

두 CSV에서 `gold_case_order`가 1~10인 행부터 작성한다. ID·원천 문맥 컬럼은 수정하지 않는다.

## `human_review_candidates.csv`

각 원천 후보에 대해 마지막 7개 컬럼만 작성한다.

| 컬럼 | 입력 |
|---|---|
| `gold_candidate_role` | `IDENTITY_MEMBER`, `EVIDENCE_ONLY`, `REJECTED`, `AMBIGUOUS` |
| `gold_alternative_key` | 맞는 동일 실체끼리 `ALT_001`, 다른 실체는 `ALT_002`; 나머지 역할은 빈칸 |
| `gold_display_name` | `고종(조선)`처럼 구분되는 이름; identity member만 입력 |
| `gold_entity_type` | `Person`, `Event`, `Institution`, `Heritage`, `Work`, `Organization`, `Place`, `Polity`, `Concept` |
| `gold_reason` | 이름·한자·시대·생몰년·유형을 근거로 한 짧은 이유 |
| `reviewer` | 검수자 이름 또는 ID |
| `candidate_review_status` | 작성 중 `IN_PROGRESS`, 완료 `COMPLETE`, 논의 필요 `NEEDS_DISCUSSION` |

## `human_review_cases.csv`

같은 case의 candidate를 모두 작성한 뒤 마지막 5개 컬럼을 작성한다.

| 컬럼 | 입력 |
|---|---|
| `gold_link_status` | `ACCEPTED`, `AMBIGUOUS`, `UNRESOLVED`, `REJECTED` |
| `requires_problem_review` | 복수 실체를 문항별로 골라야 하면 `YES`, 아니면 `NO` |
| `gold_decision_reason` | case 전체 결론의 짧은 이유 |
| `reviewer` | 검수자 이름 또는 ID |
| `case_review_status` | 작성 중 `IN_PROGRESS`, 완료 `COMPLETE`, 논의 필요 `NEEDS_DISCUSSION` |

CSV 안의 JSON 문맥에는 쉼표와 줄바꿈이 들어 있으므로, 셀 안의 따옴표 구조를 임의로
수정하지 않는다. 가능하면 Cursor의 CSV 편집 확장 또는 표 형태 편집기를 사용한다.

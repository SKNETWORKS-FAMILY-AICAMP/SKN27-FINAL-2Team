# 사실 관계 검증셋

`human_review_csv/fact_relationship_review.csv`만 사람이 작성한다.

- `gold_relation_judgment`: `CORRECT`, `INCORRECT`,
  `PARTIALLY_CORRECT`, `INSUFFICIENT_EVIDENCE` 중 하나
- 관계·방향·endpoint가 틀렸다면 `gold_correct_*`와
  `gold_error_types_json`을 작성한다.
- 근거 URL과 판단 이유를 작성한 뒤 `review_status`를 `COMPLETE`로 바꾼다.
- 검수가 시작된 파일은 생성기가 자동으로 덮어쓰지 않는다.

`internal/source` 파일은 표본 재현성과 평가용 snapshot이므로 수정하지 않는다.

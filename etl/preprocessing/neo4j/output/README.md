# Neo4j 전처리 산출물 안내

사용자가 직접 확인할 결과와 재실행용 중간 산출물을 분리한다. 업무 순서를 파일시스템에
`01`~`05`로 반복하지 않고, 확인 대상은 `review`와 `final_identity` 두 곳에서 찾는다.

## 사용자가 확인하는 폴더

| 폴더 | 의미 | 대표 파일 |
|---|---|---|
| `review` | 추출 용어·원천 커버리지·Entity Resolution 검토 대상 | `unique_exam_terms.csv`, `source_coverage_report.json`, `cases_requiring_review.csv` |
| `final_identity` | 승인 후 Neo4j identity import 파일 | `canonical_entity_registry.csv`, `neo4j_*` |
| `test_run/review` | 운영 데이터와 분리된 소량 테스트 검토 결과 | 운영 `review`와 같은 파일명 |
| `test_run/final_identity` | 소량 테스트의 최종 identity 결과 | 운영 `final_identity`와 같은 구조 |

`review`의 파일은 용도가 다르다.

- `unique_exam_terms.csv`: 기출문제에서 추출·정규화한 용어 목록
- `source_coverage_report.json`: AKS·시소러스 기준 후보 검색 커버리지
- `cases_requiring_review.csv`: 아직 사람 또는 LLM 판정이 필요한 Entity Resolution case

## 자동 생성되는 `internal`

`internal`은 사람이 직접 작성하지 않는 재실행·감사용 영역이다.

| 폴더 | 내용 |
|---|---|
| `internal/term_extraction` | 문항별 추출 결과, 추출 checkpoint, 정규화 시소러스 |
| `internal/candidate_retrieval` | 이름·definition·본문 언급 검색의 원천 후보 JSON |
| `internal/entity_resolution` | case, SourceRecord 후보, 비교 신호, 제안 실체 그룹, 문항 배정 초안 |
| `internal/model_review` | 모델 입력 task, checkpoint, 판정 및 검증 중간 결과 |

`internal/candidate_retrieval`의 세 후보 JSON은 모두 `PROPOSED`다. 이 후보가
`internal/entity_resolution`에서 SourceRecord 단위로 합쳐지고, 최종 검토 대상만
`review/cases_requiring_review.csv`에 나온다.

사람이 작성하는 골든셋은 runtime output이 아니므로 인접한 `../goldset`에서 관리한다.
골든셋에서 파생한 관련 엔티티 최종 identity CSV는 `../goldset/final_identity`에 생성한다.

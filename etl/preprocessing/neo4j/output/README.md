# Neo4j 전처리 산출물 안내

이 폴더는 확장자가 아니라 실제 업무 진행 순서로 나뉜다. 각 단계 폴더의 루트에는 사람이
직접 확인할 결과만 두고, 체크포인트·후속 계산 입력·상세 후보표는 그 단계의 `internal`에
저장한다. 소량 테스트 결과는 `test_run` 아래에 같은 구조로 저장한다.

## 단계별 폴더

| 폴더 | 의미 | 대표 파일 |
|---|---|---|
| `01_term_extraction` | 기출문제에서 역사 용어를 추출하고 정규화 | `unique_exam_terms.csv`; 체크포인트는 `internal` |
| `02_candidate_retrieval` | AKS·시소러스·ITKC 원천 후보 검색 | `source_coverage_report.json`; 후보 원본은 `internal` |
| `03_entity_resolution` | 후보 비교, 동일 실체 그룹 제안, 검토 대상 구성 | `cases_requiring_review.csv`; 상세 계산표는 `internal` |
| `04_llm_review` | 용어 단위·문항 단위 LLM 판정과 검증 | task·판정·검증 결과 모두 `internal` |
| `05_final_identity` | 승인 후 Neo4j identity import 파일 | `canonical_entity_registry.csv`, `neo4j_*` |
| `test_run` | 운영과 분리된 소량 테스트 결과 | 내부 구조는 01~05와 동일 |

## `03_entity_resolution` 파일

| 파일 | 내용 |
|---|---|
| `cases_requiring_review.csv` | 아직 사람 또는 LLM 판정이 필요한 case |
| `internal/entity_cases.csv` | 정규화된 용어·분류·문항 묶음 |
| `internal/candidate_source_records.csv` | 각 용어에 검색된 원천 레코드 후보 |
| `internal/candidate_comparison_features.csv` | 이름·한자·시대·생몰년·유형 비교값 |
| `internal/candidate_pair_merge_signals.csv` | 후보 두 개를 합칠 근거와 충돌 신호 |
| `internal/proposed_entity_groups.csv` | 코드가 제안한 동일 실체 그룹 |
| `internal/proposed_entity_group_members.csv` | 각 제안 그룹에 속한 원천 후보 |
| `internal/exam_problem_contexts.csv` | 기출문제 원문 |
| `internal/exam_problem_entity_assignments_draft.csv` | 문항별 용어와 실체 후보의 임시 연결 |

사람이 작성하는 골든셋은 runtime output이 아니므로 인접한 `../goldset`에서 별도로 관리한다.

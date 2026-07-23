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
| `internal/shared` | 운영·소량 테스트가 함께 사용하는 정규화 시소러스 |
| `internal/term_extraction` | 문항별 추출 결과와 추출 checkpoint |
| `internal/candidate_retrieval` | 중단 복구용 원천 후보 JSON. ER staging 생성 성공 후 자동 정리 |
| `internal/entity_resolution` | case, SourceRecord 후보, 비교 신호, 제안 실체 그룹, 문항 배정 초안 |
| `internal/model_review` | 모델 입력 task, checkpoint, 판정 및 검증 중간 결과 |

`internal/candidate_retrieval`의 세 후보 JSON은 모두 `PROPOSED`다. 이 후보가
`internal/entity_resolution`에서 SourceRecord 단위로 합쳐지고, 최종 검토 대상만
`review/cases_requiring_review.csv`에 나온다. 세 후보 JSON은 ER staging에서 다시
생성할 수 있으므로 staging 저장이 완료되면 장기 보존하지 않는다.

문항 텍스트 검증 정보는 별도 감사 파일을 만들지 않고
`internal/entity_resolution/exam_problem_contexts.csv`에 기록한다. 정상 문항은 상태와
정책 버전만 남기고, 원본·재구성 stem은 충돌 문항과 실제 중복 그룹에만 기록한다.

## Windows PowerShell에서 JSON 확인

JSON 산출물은 UTF-8로 저장한다. Windows PowerShell에서는 기본 인코딩으로 읽으면
한글이 깨지고 `ConvertFrom-Json`이 실패할 수 있으므로 `-Encoding UTF8`을 지정한다.

```powershell
$path = "etl/preprocessing/neo4j/output/test_run/internal/term_extraction/exam_terms_by_problem.json"
(Get-Content -Raw -Encoding UTF8 $path | ConvertFrom-Json).Count
```

사람이 작성하는 골든셋은 runtime output이 아니므로 인접한 `../goldset`에서 관리한다.
골든셋에서 파생한 관련 엔티티 최종 identity CSV는 `../goldset/final_identity`에 생성한다.

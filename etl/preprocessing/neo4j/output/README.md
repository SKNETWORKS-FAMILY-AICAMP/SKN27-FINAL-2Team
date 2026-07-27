# Neo4j 전처리 산출물 안내

현재 운영·신규 Fact 그래프에 필요한 산출물만 유지한다. 파일럿, 소량 테스트,
구형 오답 관계 단계, 재현 가능한 일회성 EDA 결과는 장기 보존하지 않는다.

## 사용자가 확인하는 폴더

| 폴더 | 의미 | 대표 파일 |
|---|---|---|
| `review` | 추출 용어·원천 커버리지·Entity Resolution 검토 대상 | `unique_exam_terms.csv`, `source_coverage_report.json`, `cases_requiring_review.csv` |
| `final_identity` | 기출 용어와 승인된 Neo4j identity import 파일 | `canonical_entity_registry.csv`, `neo4j_*` |
| `fact_graph_eda` | 우선순위 endpoint·안정 endpoint 관계 검토와 보류 관계 | `entity_resolution_human_review.csv`, `nlp_relation_human_review.csv`, `deferred_relation_candidates.csv` |
| `fact_graph_load` | 신규 DB 적재 직전 VERIFIED·PROVISIONAL Entity·Fact·EvidenceSpan | `fact_graph_nodes.csv`, `fact_graph_facts.csv`, `fact_graph_evidence.csv` |

`neo4j_exam_term_nodes.csv`에는 원천 연결이 보류된 기출 용어도 남는다.
`neo4j_exam_term_to_entity_relationships.csv`에는 검증된 canonical 연결만 기록한다.

신규 Fact 그래프 적재 계획의 기본 모드는 다음과 같다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_fact_graph_load_pipeline.py `
  --mode trusted_and_provisional
```

이 명령은 DB를 변경하지 않는다. `fact_graph_facts.csv`의
`default_retrieval_eligible`이 `True`인 Fact만 기본 RAG 검색에 사용한다.
실제 DB 적재는 별도 승인 후 `--execute-neo4j`를 붙여 실행한다.

`run_full_neo4j_pipeline.py --execute`가 끝나면 output 루트에
`full_pipeline_manifest.json`이 생성된다. `--load-neo4j`까지 실행한 경우
`final_identity/neo4j_load_manifest.json`에서 실제 DB upsert 건수를 확인한다.

`review`의 파일은 용도가 다르다.

- `unique_exam_terms.csv`: 기출문제에서 추출·정규화한 용어 목록
- `source_coverage_report.json`: AKS·시소러스 기준 후보 검색 커버리지
- `cases_requiring_review.csv`: 아직 사람 또는 LLM 판정이 필요한 Entity Resolution case

## 재실행과 감사에 필요한 중간 폴더

| 폴더 | 내용 |
|---|---|
| `internal` | 용어 추출·Entity Resolution·모델 판정 checkpoint |
| `source_relationships` | 원천 관계와 canonical 사실 관계 |
| `fact_retrieval` | 기출 anchor 중심 사실 검색 결과 |
| `exam_term_nlp_relations_full` | source별 NLP 결과를 합친 재게이트용 입력 |
| `exam_term_nlp_relation_gate` | 코드 게이트 결과와 공식 문장 근거 |
| `exam_anchor_fact_graph` | identity·구조화 관계·NLP 관계를 합친 후보 |

`internal`은 사람이 직접 작성하지 않는 재실행·감사용 영역이다.

| 폴더 | 내용 |
|---|---|
| `internal/shared` | 운영·소량 테스트가 함께 사용하는 정규화 시소러스 |
| `internal/term_extraction` | 문항별 추출 결과와 추출 checkpoint |
| `internal/entity_resolution` | case, SourceRecord 후보, 비교 신호, 제안 실체 그룹, 문항 배정 초안 |
| `internal/model_review` | 모델 입력 task, checkpoint, 판정 및 검증 중간 결과 |

`internal/candidate_retrieval`은 ER staging 생성 후 삭제한다. 최종 검토 대상은
`review/cases_requiring_review.csv`에 남는다.

문항 텍스트 검증 정보는 별도 감사 파일을 만들지 않고
`internal/entity_resolution/exam_problem_contexts.csv`에 기록한다. 정상 문항은 상태와
정책 버전만 남기고, 원본·재구성 stem은 충돌 문항과 실제 중복 그룹에만 기록한다.

## Windows PowerShell에서 JSON 확인

JSON 산출물은 UTF-8로 저장한다. Windows PowerShell에서는 기본 인코딩으로 읽으면
한글이 깨지고 `ConvertFrom-Json`이 실패할 수 있으므로 `-Encoding UTF8`을 지정한다.

```powershell
$path = "etl/preprocessing/neo4j/output/internal/term_extraction/exam_terms_by_problem.json"
(Get-Content -Raw -Encoding UTF8 $path | ConvertFrom-Json).Count
```

사람이 작성하는 골든셋은 runtime output이 아니므로 인접한 `../goldset`에서 관리한다.
골든셋에서 파생한 관련 엔티티 최종 identity CSV는 `../goldset/final_identity`에 생성한다.

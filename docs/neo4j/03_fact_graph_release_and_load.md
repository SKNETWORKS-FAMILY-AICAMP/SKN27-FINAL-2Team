# Fact Graph release와 적재 운영

> 상태: `CURRENT-RUNBOOK`
> 기준일: 2026-07-28
> release: `korean-history-fact-graph-2026-07-28-contextual-v7`

## 1. 두 Neo4j

| 구분 | Main Neo4j | Fact Neo4j |
|---|---|---|
| 컨테이너 | `skn27-neo4j` | `skn27-fact-neo4j` |
| 목적 | 용어·Canonical identity | 사실·근거·관계 탐색 |
| 파이프라인 | `run_full_neo4j_pipeline.py` | `run_fact_graph_load_pipeline.py` |
| 적재 방식 | final identity upsert | release 패키지 적재 |
| 기본 Bolt | `.env`의 기존 포트 | `bolt://localhost:7688` |

## 2. 팀원 최초 적재

프로젝트 루트에서 실행한다.

```powershell
docker compose --env-file .env -f storage\fact_neo4j\docker-compose.yml up -d

.\.venv\Scripts\python.exe `
  etl\preprocessing\neo4j\run_fact_graph_load_pipeline.py `
  --load-only
```

`--load-only`는 저장소에 포함된 `output/fact_graph_release`의 최종 CSV와
`manifest.json`을 사용한다. 기존 Fact DB를 현재 release로 교체할 때만
`--load-only --replace`를 사용한다.

## 3. release 재생성·적재

다음 상위 입력을 모두 가진 환경에서만 `--load-only` 없이 실행한다.

```text
output/final_identity/
output/fact_graph_load/
output/exam_anchor_fact_graph/all_fact_graph_candidates.csv
output/internal/model_review/relation_review/relation_review_tasks.jsonl
output/internal/model_review/relation_review/relation_review_final_decisions.csv
```

```powershell
.\.venv\Scripts\python.exe `
  etl\preprocessing\neo4j\run_fact_graph_load_pipeline.py
```

실행 순서:

1. 입력 검사
2. 승인 Fact 선택
3. 승인된 identity 충돌 격리·리다이렉트
4. 인물 문맥의 동일 이름 endpoint 국소 병합
5. 안전한 후보 endpoint를 CanonicalEntity로 투영
6. 대칭 관계의 양방향 중복을 하나의 의미 관계로 집계
7. exact search 충돌 차단
8. 최종 CSV와 manifest 생성
9. Fact Neo4j 적재
10. 예상·실제 수치 검증

## 4. 최종 패키지

경로:

```text
etl/preprocessing/neo4j/output/fact_graph_release/
```

필수 파일은 CSV 20개와 `manifest.json`이다.

```text
entities.csv
facts.csv
semantic_relations.csv
evidence.csv
source_records.csv
identity_conflicts.csv
quarantined_facts.csv
entity_names.csv
exam_terms.csv
topics.csv
eras.csv
fact_evidence_links.csv
evidence_source_links.csv
provisional_source_links.csv
entity_name_links.csv
exam_term_links.csv
source_resolution_links.csv
entity_topic_links.csv
entity_era_links.csv
entity_type_links.csv
manifest.json
```

이 최종 패키지는 Git 포함 대상이다. 다른 runtime output은 제외한다.

## 5. 현재 스키마

주요 노드:

```text
GraphEntity:CanonicalEntity
GraphEntity:ProvisionalEntity
Fact
EvidenceSpan
SourceRecord
EntityName
ExamTerm
ResolvedSearchTerm
Topic
Era
EntityType
```

Fact 감사 경로:

```text
(Fact)-[:SUBJECT]->(GraphEntity)
(Fact)-[:OBJECT]->(GraphEntity)
(Fact)-[:SUPPORTED_BY]->(EvidenceSpan)
(EvidenceSpan)-[:FROM_SOURCE]->(SourceRecord)
```

직접 의미 관계:

```text
(GraphEntity)-[:ESTABLISHED|BUILT|LOCATED_IN|...]->(GraphEntity)
```

`Fact`는 개별 assertion과 근거를 보존한다. 동일한
`(subject, predicate, object)`의 직접 관계만 하나로 합치고
`fact_ids`, `fact_count`, `assertion_count`, `evidence_ids`를 누적한다.

## 6. 국소 endpoint 병합

다음 조건을 모두 만족할 때만 미정규화 노드를 특정 인물 문맥 안에서 합친다.

1. 같은 canonical `Person`
2. 같은 정규화 이름
3. 같은 EntityType
4. 서로 다른 원본 endpoint 2개 이상
5. 각 원본 endpoint의 모든 출현이 해당 문맥 그룹에만 속함

Predicate와 방향은 노드 병합 키에서 제외한다. 따라서 같은 대상과 이름을 가리키는
노드는 하나로 표현하면서 `APPOINTED_AS`, `SERVED_AS` 같은 서로 다른 관계와
각 Fact·Evidence는 그대로 유지한다. 이는 전역 동일성 선언이 아니라
anchor 안에서만 유효한 국소 그룹이다. 병합 결과도 `ProvisionalEntity`이며
검색 시작점으로 사용하지 않는다.

```text
merge_scope = ANCHOR_LOCAL
retrieval_eligible = false
anchor_eligible = false
multi_hop_eligible = false
```

세종–집현전 사례는 endpoint 7개와 `ESTABLISHED` 관계 7개를 국소 노드와
직접 관계 하나로 정리했으며 Fact 7건과 Evidence 7건은 보존한다.

같은 원본 SourceRecord가 여러 문맥 그룹에 동시에 들어가는 경우에는 병합하지
않는다. 따라서 하나의 원본 endpoint는 최대 하나의 `GraphEntity`로만 표현된다.

### Canonical 투영 중복 정리

같은 근거와 predicate가 SourceRecord endpoint와 이미 확인된 CanonicalEntity
endpoint에 중복 표현된 경우, 다음 조건을 모두 만족할 때만 canonical endpoint로
투영한다.

1. 정규화 이름과 EntityType이 같다.
2. 같은 EvidenceSpan을 공유한다.
3. 대응 가능한 canonical endpoint 쌍이 하나뿐이다.

동명이인 가능성이 있거나 canonical 대표가 유일하지 않으면 합치지 않는다.
Fact ID, 원본 endpoint ID, EvidenceSpan은 그대로 보존한다.

### 후보 endpoint의 Canonical 해소

`EXAM_TERM_CANDIDATE`, `OPEN_ENTITY_CANDIDATE`는 다음 조건에서만
CanonicalEntity로 투영한다.

1. EntityType이 확정되어 있다.
2. 비인물은 공식 출처 이름·별칭과 EntityType의 canonical 후보가 하나뿐이다.
3. 인물은 `고려 태조`처럼 공식 출처의 시대·국가 한정 별칭이 유일하거나,
   근거 문장에 그 한정 별칭이 있고 후보 하나만 선택될 때만 허용한다.
4. `태조`, `고종` 같은 단독 인물명은 현재 레지스트리 후보가 하나여도
   자동 투영하지 않는다.

이름별 예외 목록은 사용하지 않는다. 공식 출처 metadata의 이름·별칭·한정어와
근거 문장을 일반 규칙으로 결합한다. 원본 endpoint ID와 EvidenceSpan은 Fact에
그대로 남는다.

## 7. 현재 검증 수치

| 항목 | 수치 |
|---|---:|
| GraphEntity | 19,186 |
| CanonicalEntity | 4,786 |
| ProvisionalEntity | 14,400 |
| Fact assertion | 39,836 |
| 직접 의미 관계 | 35,064 |
| EvidenceSpan | 39,945 |
| VERIFIED Fact | 38,158 |
| 검토 승인 Fact | 1,678 |
| 양 endpoint 해소 Fact | 1,083 |
| 기본 검색 허용 Fact | 920 |
| 한쪽 이상 canonical인 terminal Fact | 7,235 |
| 미해소 endpoint 포함 Fact | 38,753 |
| 국소 병합 엔티티 | 30 |
| 병합 참여 원본 endpoint | 101 |
| 여러 Predicate를 유지하는 국소 엔티티 | 4 |
| canonical endpoint 투영 Fact | 326 |
| 후보 endpoint Canonical 해소 Fact | 288 |
| 후보 endpoint Canonical 해소 원본 노드 | 276 |
| identity conflict 격리 SourceRecord | 1 |
| identity redirect SourceRecord | 1 |
| 격리 Fact assertion | 16 |
| 자동 보강 기출 용어 링크 | 15 |
| 기본 사실 연결 기출 용어 | 364 |
| terminal 사실 연결 기출 용어 | 713 |

```text
searchable_provisional_count = 0
unsafe_retrieval_relationship_count = 0
unsafe_terminal_relationship_count = 0
unsafe_provisional_traversal_count = 0
duplicate_semantic_relation_id_count = 0
duplicate_exact_search_canonical_name_count = 0
multi_entity_source_count = 0
duplicate_evidence_predicate_endpoint_group_count = 0
direct_fact_reference_count = 39,836
unsafe_candidate_endpoint_resolution_count = 0
unit tests = 15 passed
CSV reproducibility = 20/20 identical
load verification = NOT_RUN
```

Canonical 동일 이름과 시대 보고서:

```powershell
.\.venv\Scripts\python.exe `
  etl\preprocessing\neo4j\runners\report_canonical_duplicate_names.py
```

결과는 `output/fact_graph_eda/canonical_duplicate_name_eras.csv`에 저장된다.
동일 이름 Canonical 2,330개를 807개 그룹으로 나누고 각 Entity의 검증된 시대를
별도 행으로 기록한다.

## 8. 검색 원칙과 한계

- exact search는 `exact_search_eligible = true`인 canonical 또는
  `ResolvedSearchTerm`에서만 시작한다.
- 동일 정규화 이름의 canonical 후보가 여러 개면 기본적으로 모두 차단한다.
  승인된 기출 용어가 그중 하나만 가리킬 때만 해당 canonical을 대표로 허용한다.
- 여러 canonical을 가리키는 기출 용어 27개는 검색 시작점에서 제외한다.
- `ProvisionalEntity`는 이름 검색·Anchor·자동 다중 hop에서 제외한다.
- 기본 검색에 바로 사용할 수 있는 Fact는 920건이다.
- `terminal_retrieval_eligible = true`인 7,235개 Fact는 한쪽 canonical에서
  미확정 반대편 후보를 한 번 조회할 수 있다. 미확정 노드에서 다음 hop으로는
  진행하지 않는다.
- 이 정책으로 안전한 사실 연결 기출 용어는 기본 364개, terminal 713개다.
- 대칭 관계는 직접 의미 관계에서 한 방향만 저장한다. 원본 Fact와 Evidence는
  그대로 보존한다.
- `SemanticClass`, `RoleAssignment`, `Polity`, `Region` 전체 구조는 후속 구현이다.
- `Topic`, `Era`, `EntityType`은 경로가 아니라 후보 필터·랭킹 신호로 사용한다.
  실제 hop 수는 사실 관계만 세며 후보 랭킹·난이도·오답 유형은
  RAG·문제 생성 계층이 담당한다.

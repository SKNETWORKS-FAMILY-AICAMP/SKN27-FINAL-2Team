# Neo4j 전처리 산출물

> 기준일: 2026-07-27

## 주요 폴더

| 폴더 | 역할 |
|---|---|
| `review` | 용어·커버리지·ER 검토 |
| `final_identity` | Main Neo4j용 canonical identity |
| `source_relationships` | 원천 관계 staging |
| `fact_retrieval` | 기출 anchor 사실·교체 후보 |
| `exam_term_nlp_relations_full` | 공식 문서 NLP 관계 전체 |
| `exam_term_nlp_relation_gate` | 코드 gate 결과 |
| `exam_anchor_fact_graph` | 구조화·canonical·NLP 통합 후보 |
| `fact_graph_eda` | endpoint·관계 검토 큐 |
| `fact_graph_load` | release 생성 전 중간 패키지 |
| `fact_graph_release` | 팀원 적재용 최종 portable 패키지 |
| `internal` | checkpoint·모델 판정·감사 자료 |

## Main Neo4j

```powershell
.\.venv\Scripts\python.exe `
  etl\preprocessing\neo4j\run_full_neo4j_pipeline.py `
  --execute `
  --load-neo4j
```

Main DB 입력은 `final_identity`에 생성된다.

## Fact Graph EDA

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_fact_graph_eda_pipeline.py
```

EDA는 검토 파일을 만들며 DB에 적재하지 않는다.

## Fact Graph 최종 패키지

`fact_graph_release`에는 CSV 20개와 `manifest.json`이 있다. 이 파일만 최종
공유·Git 포함 대상이며 `fact_graph_load`는 중간 산출물이다.

현재 release는 친생 자녀·부친·모친을 각각 `HAS_CHILD`, `HAS_FATHER`,
`HAS_MOTHER`로 통합한다. 친생 여부는 `relation_qualifiers_json`과 Neo4j의
`kinship_kind` 속성에 보존된다.

팀원 적재:

```powershell
docker compose --env-file .env -f storage\fact_neo4j\docker-compose.yml up -d
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_fact_graph_load_pipeline.py --load-only
```

기존 Fact DB 교체:

```powershell
.\.venv\Scripts\python.exe `
  etl\preprocessing\neo4j\run_fact_graph_load_pipeline.py `
  --load-only `
  --replace
```

release 재생성·적재는 상위 input을 모두 가진 환경에서 `--load-only` 없이 실행한다.

```text
release = korean-history-fact-graph-2026-07-27-contextual-v5
GraphEntity = 19,437
CanonicalEntity = 4,786
ProvisionalEntity = 14,651
Fact = 39,836
direct semantic relation = 35,193
EvidenceSpan = 39,945
canonical endpoint projected Fact = 326
terminal retrieval Fact = 7,103
default fact-covered exam term = 363
terminal fact-covered exam term = 699
multi-entity source = 0
duplicate evidence-predicate endpoint group = 0
load verification = NOT_RUN
```

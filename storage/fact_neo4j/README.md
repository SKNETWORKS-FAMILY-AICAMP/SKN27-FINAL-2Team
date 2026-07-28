# Fact Graph Neo4j

> 기준일: 2026-07-28

기존 `skn27-neo4j`와 분리된 사실·근거 그래프다.

## 실행과 팀원 적재

```powershell
docker compose --env-file .env -f storage\fact_neo4j\docker-compose.yml up -d

.\.venv\Scripts\python.exe `
  etl\preprocessing\neo4j\run_fact_graph_load_pipeline.py `
  --load-only
```

기존 Fact DB를 교체할 때만 `--load-only --replace`를 사용한다.

```text
HTTP: http://localhost:7475
Bolt: bolt://localhost:7688
Container: skn27-fact-neo4j
```

`--load-only`는 `etl/preprocessing/neo4j/output/fact_graph_release`의 최종
CSV 20개와 `manifest.json`을 사용한다.

상위 전처리 output을 모두 보유하고 release를 재생성할 때:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_fact_graph_load_pipeline.py
```

## 검색 정책

- canonical과 승인된 `ResolvedSearchTerm`만 이름 검색 시작점으로 사용한다.
- `ProvisionalEntity`는 이름 검색·Anchor·자동 다중 hop에서 제외한다.
- 직접 의미 관계를 합쳐도 모든 Fact ID와 Evidence를 보존한다.

```cypher
MATCH (anchor:CanonicalEntity {entity_id: $entity_id})-[relation]->(target)
WHERE relation.semantic_relation_id IS NOT NULL
  AND relation.candidate_retrieval_eligible = true
RETURN type(relation), target, relation.fact_count, relation.evidence_ids
```

## 현재 release

```text
graph_release_id = korean-history-fact-graph-2026-07-28-contextual-v7
GraphEntity = 19,186
Fact = 39,836
direct semantic relation = 35,064
EvidenceSpan = 39,945
load verification = NOT_RUN
```

상세 문서: `docs/neo4j/03_fact_graph_release_and_load.md`

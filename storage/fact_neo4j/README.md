# 한국사 사실 그래프 Neo4j

기존 `skn27-neo4j`와 분리된 신규 사실 그래프 컨테이너입니다.

```powershell
docker compose --env-file .env -f storage\fact_neo4j\docker-compose.yml up -d
.\.venv\Scripts\python.exe storage\fact_neo4j\load_fact_graph.py
```

- HTTP: `http://localhost:7475`
- Bolt: `bolt://localhost:7688`
- 미정규화 끝점은 `ProvisionalEntity`로 저장하며 검색 인덱스에서 제외합니다.
- `SourceRecord`는 엔티티 끝점과 별도 노드로 저장합니다.
- 검증된 직접 사실 관계와 `Fact`/`EvidenceSpan` 근거 구조를 함께 적재합니다.
- 기본 이름 검색은 `normalized_search_text` 정확 일치를 먼저 사용합니다.

```cypher
CALL () {
    MATCH (entity:CanonicalEntity {
        normalized_search_text: $normalized_name
    })
    RETURN entity
    UNION
    MATCH (term:ResolvedSearchTerm {
        normalized_search_text: $normalized_name
    })-[:REFERS_TO]->(entity:CanonicalEntity)
    RETURN entity
}
WITH DISTINCT entity
WHERE entity.retrieval_eligible = true
RETURN entity
```

`ProvisionalEntity`는 이 검색 경로와 인덱스에 포함하지 않습니다.
# Fact graph 적재 파이프라인

권장 실행 명령은 다음과 같습니다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_fact_graph_load_pipeline.py
```

이 명령은 release CSV 생성, 별도 fact Neo4j 적재, 적재 결과 검증을
순서대로 실행합니다. 기존 파생 release를 교체해야 할 때만 다음 옵션을
명시합니다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_fact_graph_load_pipeline.py --replace
```

실행 결과는 `etl/preprocessing/neo4j/output/fact_graph_release` 아래의
`manifest.json`, `neo4j_load_manifest.json`, `pipeline_manifest.json`에서
확인할 수 있습니다.

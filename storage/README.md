# 저장소를 정의하는 공간

- postgre





- neo4j

```bash
docker compose --env-file .env -f storage\neo4j\docker-compose.yml up -d
docker compose --env-file .env -f storage\neo4j\docker-compose.yml down -v

docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
docker compose --env-file .env -f storage\postgresql\docker-compose.yml down -v

python storage\neo4j\load_schema.py
```

## 문제 생성용 Fact Neo4j

기존 Neo4j와 분리된 인스턴스다.

```powershell
docker compose -p skn27-fact-neo4j --env-file .env `
  -f storage/fact_neo4j/docker-compose.yml up -d
```

- Browser: `http://localhost:7475`
- Bolt: `bolt://localhost:7688`
- 컨테이너: `skn27-fact-neo4j`
- 볼륨: `skn27-fact-neo4j-data`

일반 종료에는 `down -v`를 사용하지 않는다.

적재 계획만 생성할 때는 다음 명령을 사용한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_fact_graph_load_pipeline.py `
  --mode trusted_and_provisional
```

`PROVISIONAL` Fact도 손실 방지를 위해 저장되지만
`default_retrieval_eligible = false`이므로 기본 RAG 조회에서는 제외해야 한다.


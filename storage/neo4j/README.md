# docker-compose

```bash
docker compose --env-file .env -f storage\neo4j\docker-compose.yml up -d
docker compose --env-file .env -f storage\neo4j\docker-compose.yml down -v

docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
docker compose --env-file .env -f storage\postgresql\docker-compose.yml down -v

python storage\neo4j\load_schema.py
```

# import

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_full_neo4j_pipeline.py

.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_full_neo4j_pipeline.py `
  --execute `
  --load-neo4j
```

기본 실행은 dry-run이다. `--execute --load-neo4j`를 함께 지정해야 검증된 final identity를
실제 DB에 upsert한다. 이 경로는 `load_schema.py`처럼 DB 전체를 초기화하지 않는다.



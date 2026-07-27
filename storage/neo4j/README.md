# Main Neo4j

> 기준일: 2026-07-27

기존 서비스용 용어·Canonical identity DB다. Fact DB와 분리한다.

## 컨테이너

```powershell
docker compose --env-file .env -f storage\neo4j\docker-compose.yml up -d
```

## 현재 권장 파이프라인

기본 dry-run:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\neo4j\run_full_neo4j_pipeline.py
```

전처리·final identity 생성:

```powershell
.\.venv\Scripts\python.exe `
  etl\preprocessing\neo4j\run_full_neo4j_pipeline.py `
  --execute
```

검증된 final identity를 Main Neo4j에 upsert:

```powershell
.\.venv\Scripts\python.exe `
  etl\preprocessing\neo4j\run_full_neo4j_pipeline.py `
  --execute `
  --load-neo4j
```

결과:

```text
etl/preprocessing/neo4j/output/full_pipeline_manifest.json
etl/preprocessing/neo4j/output/final_identity/neo4j_load_manifest.json
```

`storage/neo4j/load_schema.py`는 reset 방식의 레거시 적재기다. 현재 schema/import
패키지가 없으므로 실행하지 않는다.

Fact·Evidence·직접 역사 관계는 `storage/fact_neo4j/README.md`를 따른다.

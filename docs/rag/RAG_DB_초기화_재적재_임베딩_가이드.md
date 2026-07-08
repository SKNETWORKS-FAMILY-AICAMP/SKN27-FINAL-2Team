# RAG DB 초기화·재적재·임베딩 가이드

DB를 완전히 내렸다가 PostgreSQL/pgvector를 새로 올리고, 현재 전처리 기준으로 RAG 데이터를 다시 적재·임베딩하는 순서이다.

## 1. 프로젝트 루트 이동

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
```

## 2. 환경변수 확인

`.env`가 없으면 생성한다.

```powershell
copy .env.example .env
```

필수 값:

```env
POSTGRES_DB=history_rag
POSTGRES_HOST=localhost
POSTGRES_USER=himate
POSTGRES_PASSWORD=himate1234
POSTGRES_PORT=5432

OPENAI_API_KEY=your_openai_api_key
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

## 3. PostgreSQL 완전 초기화 후 실행

이 프로젝트의 PostgreSQL은 Docker named volume이 아니라 `storage/postgresql/postgres_data` 폴더를 bind mount로 사용한다. 그래서 `down -v`만으로는 데이터가 삭제되지 않는다.

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml down -v
Remove-Item -LiteralPath storage\postgresql\postgres_data -Recurse -Force
docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
```

처음 실행하는 사람은 삭제할 기존 데이터가 없으므로 아래만 실행해도 된다.

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
```

상태 확인:

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml ps
```

## 4. Neo4j 실행

관계망 기능을 같이 사용할 때만 실행한다.

```powershell
docker compose --env-file .env -f storage\neo4j\docker-compose.yml up -d
```

## 5. 전처리 실행

현재 전처리는 다음을 반영한다.

- `category_path` 계층 구조 통일
- `period`, `periods` 시대 정보 통일
- 제목/상위 경로의 `(1)`, `1)`, `가.`, `Ⅰ.` 같은 번호 제거
- 청크 중복 제거
- 이미지 자료는 URL/metadata만 유지

실행:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_historical_sources.py
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_new_history.py
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_image_materials.py
```

결과 파일:

```text
etl/preprocessing/history/processed/historical_sources.chunks.jsonl
etl/preprocessing/history/processed/new_history.chunks.jsonl
etl/preprocessing/history/processed/image_materials.chunks.jsonl
```

## 6. PostgreSQL 적재 + 임베딩

DB가 비어 있는 상태에서는 아래 명령 하나로 적재, 임베딩, HNSW 인덱스 생성을 진행한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --limit 50000 --create-index
```

주의:

- `image_material` 자료는 DB에는 적재된다.
- 이미지 자료는 임베딩 대상에서 제외되어 `embedding`이 `NULL`로 남는다.
- 일반 개념 RAG 검색은 `embedding IS NOT NULL` 조건을 사용하므로 이미지 자료가 벡터 검색에 섞이지 않는다.
- 이미지 조회는 제목/메타데이터 기반 별도 검색 흐름에서 사용한다.

중간에 rate limit이 나면 batch size가 자동으로 줄고 대기 후 계속 진행된다.

## 7. 이어서 임베딩

중간에 멈췄다면 같은 명령을 다시 실행한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --skip-upsert --limit 50000 --create-index
```

이미 임베딩된 chunk는 건너뛰고, `embedding IS NULL`인 chunk부터 이어서 처리한다.

## 8. DBeaver 확인 쿼리

전체 적재 수:

```sql
SELECT source_type, COUNT(*) AS count
FROM rag.document_chunks
GROUP BY source_type
ORDER BY source_type;
```

임베딩 수:

```sql
SELECT
  source_type,
  COUNT(*) AS total_count,
  COUNT(embedding) AS embedded_count
FROM rag.document_chunks
GROUP BY source_type
ORDER BY source_type;
```

이미지 자료는 `embedded_count = 0`이어야 정상이다.

메타데이터 확인:

```sql
SELECT
  title,
  metadata->>'period' AS period,
  metadata->'periods' AS periods,
  metadata->'category_path' AS category_path
FROM rag.document_chunks
LIMIT 20;
```

HNSW 인덱스 확인:

```sql
SELECT indexname
FROM pg_indexes
WHERE schemaname = 'rag'
  AND tablename = 'document_chunks'
ORDER BY indexname;
```

키워드 검색 인덱스는 아래 항목이 있으면 된다.

```text
document_chunks_text_trgm_idx
document_chunks_title_trgm_idx
```

기존 DB에 제목 trigram 인덱스만 수동 추가할 때:

```sql
CREATE INDEX IF NOT EXISTS document_chunks_title_trgm_idx
ON rag.document_chunks
USING GIN (title gin_trgm_ops);

ANALYZE rag.document_chunks;
```

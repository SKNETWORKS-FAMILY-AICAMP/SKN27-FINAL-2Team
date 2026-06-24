# 히스토리 RAG 전처리·적재 가이드

이 문서는 현재 프로젝트 구조 기준으로 한국사 RAG 데이터를 전처리하고 PostgreSQL pgvector에 적재·임베딩하는 순서를 정리한다.

## 1. 전체 순서

```text
Docker 컨테이너 생성
→ .env 확인
→ 원천 데이터 확인
→ 자료별 전처리
→ processed JSONL 생성
→ PostgreSQL upsert
→ OpenAI embedding 생성
→ pgvector 인덱스 생성
→ DBeaver 검증
→ RAG 테스트
```

## 2. 프로젝트 경로

프로젝트 루트:

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
```

주요 경로:

```text
storage/postgresql/docker-compose.yml
storage/neo4j/docker-compose.yml
etl/raw_data/
etl/preprocessing/history/
etl/preprocessing/history/processed/
etl/preprocessing/history/embedding/embed_chunks_to_pgvector.py
```

## 3. Docker 컨테이너 생성

현재 프로젝트는 PostgreSQL과 Neo4j compose 파일이 분리되어 있다.

### 3.1 PostgreSQL + pgvector 실행

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
```

상태 확인:

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml ps
```

로그 확인:

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml logs postgres
```

컨테이너 이름:

```text
skn27-postgres
```

### 3.2 Neo4j 실행

인물 관계망이나 사건 관계망을 그래프 DB로 사용할 때 실행한다.

```powershell
docker compose --env-file .env -f storage\neo4j\docker-compose.yml up -d
```

상태 확인:

```powershell
docker compose --env-file .env -f storage\neo4j\docker-compose.yml ps
```

브라우저 접속:

```text
http://localhost:7474
```

### 3.3 중지와 재시작

중지:

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml stop
docker compose --env-file .env -f storage\neo4j\docker-compose.yml stop
```

재시작:

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml start
docker compose --env-file .env -f storage\neo4j\docker-compose.yml start
```

주의: `down -v`는 DB 볼륨을 삭제할 수 있으므로 일반 작업에서는 사용하지 않는다.

## 4. `.env` 확인

`.env`가 없으면 `.env.example`을 복사한다.

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

Neo4j 사용 시:

```env
NEO4J_USER=neo4j
NEO4J_PASSWORD=change_me
NEO4J_HTTP_PORT=7474
NEO4J_BOLT_PORT=7687
```

## 5. 원천 데이터 위치

기본 RAG 원천 데이터:

```text
etl/raw_data/
  사료로 본 한국사/
  신편 한국사 csv/
  한국사 이미지 자료/
```

관계망 수집 데이터:

```text
etl/raw_data/한국고전종합DB_관계망/
  itkc_people.csv
  itkc_person_relations.csv
  itkc_events.csv
  itkc_event_relations.csv
```

관계망 데이터는 Neo4j 적재용이며, 현재 pgvector 기본 임베딩 대상은 아니다.

## 6. 자료별 전처리

프로젝트 루트에서 실행한다.

### 6.1 사료로 본 한국사

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_historical_sources.py
```

### 6.2 신편 한국사

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_new_history.py
```

### 6.3 한국사 이미지 자료

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_image_materials.py
```

## 7. 전처리 결과 파일

생성 위치:

```text
etl/preprocessing/history/processed/
```

생성 파일:

```text
historical_sources.documents.jsonl
historical_sources.chunks.jsonl
new_history.documents.jsonl
new_history.chunks.jsonl
image_materials.documents.jsonl
image_materials.chunks.jsonl
```

임베딩 스크립트가 기본으로 읽는 chunk 파일:

```text
historical_sources.chunks.jsonl
new_history.chunks.jsonl
image_materials.chunks.jsonl
```

## 8. 이미지 자료 필터링

이미지 자료는 파일을 서버에 저장하지 않고 URL만 metadata에 유지한다.

필요 시 라이선스 필터를 먼저 실행한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\filter_image_materials_by_license.py
```

정부/공공 출처만 남겨야 할 때:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\remove_non_government_image_sources.py
```

이후 `preprocess_image_materials.py`를 다시 실행한다.

## 9. PostgreSQL 적재 및 임베딩

적재와 임베딩은 같은 스크립트에서 처리한다.

```text
etl/preprocessing/history/embedding/embed_chunks_to_pgvector.py
```

이 스크립트가 하는 일:

1. `CREATE EXTENSION IF NOT EXISTS vector`
2. `CREATE SCHEMA IF NOT EXISTS rag`
3. `rag.document_chunks` 테이블 생성
4. JSONL chunk upsert
5. OpenAI embedding 생성
6. embedding 저장
7. 필요 시 ivfflat 인덱스 생성

## 10. 소량 테스트

처음에는 10개만 적재·임베딩한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --limit 10 --batch-size 5 --sleep 1
```

DBeaver 확인:

```sql
SELECT COUNT(*)
FROM rag.document_chunks;
```

## 11. 전체 적재 + 임베딩

전체 chunk를 upsert하고 임베딩한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --delete-missing --limit 40000 --batch-size 10 --sleep 2
```

옵션 의미:

| 옵션 | 의미 |
|---|---|
| `--delete-missing` | JSONL에 없는 chunk를 DB에서도 삭제 |
| `--limit 40000` | 이번 실행에서 최대 40,000개 임베딩 |
| `--batch-size 10` | OpenAI embedding 요청 배치 크기 |
| `--sleep 2` | 배치 사이 2초 대기 |

Rate limit이 나면 배치 크기를 줄이거나 대기 시간을 늘린다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --delete-missing --limit 40000 --batch-size 5 --sleep 5
```

## 12. 끊겼을 때 이어서 임베딩

이미 JSONL upsert는 끝났고 임베딩만 이어서 할 때:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --skip-upsert --limit 40000 --batch-size 10 --sleep 2
```

스크립트는 아래 조건의 row부터 다시 처리한다.

```sql
embedding IS NULL
OR embedding_model IS DISTINCT FROM 현재_EMBEDDING_MODEL
```

## 13. JSONL만 DB에 반영

임베딩을 새로 만들지 않고 전처리 결과만 DB에 반영할 때:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --delete-missing --limit 0
```

특정 chunk 파일만 반영할 때:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --chunk-file historical_sources.chunks.jsonl --delete-missing --limit 0
```

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --chunk-file new_history.chunks.jsonl --delete-missing --limit 0
```

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --chunk-file image_materials.chunks.jsonl --delete-missing --limit 0
```

## 14. 벡터 인덱스 생성

전체 임베딩 후 인덱스를 생성한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --skip-upsert --limit 0 --create-index
```

메모리 오류가 나면 DBeaver에서 먼저 실행한다.

```sql
SET maintenance_work_mem = '256MB';
```

그 다음 인덱스를 수동 생성할 수 있다.

```sql
CREATE INDEX IF NOT EXISTS document_chunks_embedding_cosine_idx
ON rag.document_chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100)
WHERE embedding IS NOT NULL;

ANALYZE rag.document_chunks;
```

## 15. DBeaver 연결 정보

PostgreSQL:

```text
Host: localhost
Port: 5432
Database: history_rag
User: himate
Password: .env의 POSTGRES_PASSWORD
```

## 16. 적재 결과 확인 SQL

전체 chunk 수:

```sql
SELECT COUNT(*)
FROM rag.document_chunks;
```

출처별 수:

```sql
SELECT source_type, source_name, COUNT(*) AS cnt
FROM rag.document_chunks
GROUP BY source_type, source_name
ORDER BY source_type, source_name;
```

임베딩 상태:

```sql
SELECT
    COUNT(*) AS total,
    COUNT(embedding) AS embedded,
    COUNT(*) - COUNT(embedding) AS missing_embedding
FROM rag.document_chunks;
```

인덱스 확인:

```sql
SELECT indexname
FROM pg_indexes
WHERE schemaname = 'rag'
  AND tablename = 'document_chunks'
ORDER BY indexname;
```

이미지 자료 확인:

```sql
SELECT
    chunk_id,
    title,
    source_name,
    metadata ->> 'source_url' AS source_url,
    metadata ->> 'thumbnail_url' AS thumbnail_url,
    metadata ->> 'original_image_url' AS original_image_url
FROM rag.document_chunks
WHERE source_type = 'image_material'
ORDER BY id
LIMIT 50;
```

특정 키워드 확인:

```sql
SELECT source_name, source_type, document_id, title, LEFT(chunk_text, 200) AS snippet
FROM rag.document_chunks
WHERE title ILIKE '%장영실%'
   OR chunk_text ILIKE '%장영실%'
   OR metadata::text ILIKE '%장영실%'
ORDER BY source_name, title
LIMIT 20;
```

## 17. RAG 테스트

CLI 테스트:

```powershell
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "훈민정음 알려줘"
```

구조화 답변:

```powershell
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "조선 전기 정치 정리해줘" --answer-format structured
```

이미지 조회:

```powershell
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "첨성대 사진 보여줘" --raw
```

## 18. 자주 생기는 문제

### PostgreSQL 인증 실패

```text
password authentication failed for user "himate"
```

확인할 것:

1. `.env`의 `POSTGRES_PASSWORD`
2. DBeaver 접속 비밀번호
3. 이미 생성된 Docker volume의 초기 비밀번호

이미 볼륨이 만들어진 뒤 `.env`만 바꾸면 DB 비밀번호는 자동으로 바뀌지 않는다.

### OpenAI rate limit

배치 크기를 줄이고 sleep을 늘린다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --skip-upsert --limit 40000 --batch-size 5 --sleep 5
```

### 벡터 인덱스 메모리 오류

DBeaver에서:

```sql
SET maintenance_work_mem = '256MB';
```

그 다음 인덱스를 다시 생성한다.

## 19. 운영 기준

- 특정 인물명이나 업적명을 코드에 하드코딩하지 않는다.
- 검색 품질 보강은 전처리 metadata, chunk 품질, Neo4j 관계 데이터로 해결한다.
- 이미지 파일은 저장하지 않고 URL만 metadata에 유지한다.
- 관계망 데이터는 PostgreSQL 임베딩 대상이 아니라 Neo4j 적재 대상으로 분리한다.


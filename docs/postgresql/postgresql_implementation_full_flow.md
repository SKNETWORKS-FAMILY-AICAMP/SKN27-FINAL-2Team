# PostgreSQL RAG 구현 전체 흐름

이 문서는 한국사 PostgreSQL RAG의 전처리, 적재, 임베딩, 검색, 평가 흐름을 한 번에 보기 위한 기준 문서다.

현재 기준은 다음과 같다.

- Docker 실행 파일은 `storage/postgresql/docker-compose.yml`이다.
- 초기 스키마는 `storage/postgresql/schema/init.sql`이다.
- RAG 전처리 산출물은 `etl/preprocessing/history/processed/`에 생성된다.
- pgvector 적재와 임베딩 시작 파일은 `etl/preprocessing/history/embedding/embed_chunks_to_pgvector.py`다.
- 검색 구현은 `app/chatbot/rag/pgvector_retriever.py`다.

---

## 1. 전체 파이프라인

```text
raw_data
  -> preprocess scripts
  -> processed JSONL
  -> rag.document_chunks upsert
  -> OpenAI embedding
  -> HNSW / trigram / JSONB index
  -> chatbot RAG search
```

---

## 2. Docker 실행

최초 실행:

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
```

완전 초기화가 필요할 때:

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml down -v
Remove-Item -LiteralPath storage\postgresql\postgres_data -Recurse -Force
docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
```

`postgres_data`는 bind mount이므로 완전 초기화 시 폴더 삭제가 필요하다.

---

## 3. 전처리 실행

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_historical_sources.py
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_new_history.py
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_image_materials.py
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_history_timeline.py
```

현재 산출물 기준:

| 자료 | 문서 수 | 청크 수 | 비고 |
|---|---:|---:|---|
| 사료로 본 한국사 | 1,146 | 6,237 | 참고문헌 제거, 중복 청크 제거 |
| 신편 한국사 | 6,442 | 24,864 | 본문 구조 기반 청킹 |
| 한국사 이미지 자료 | 1,417 | 1,417 | 임베딩 제외, URL/메타데이터 조회 |

---

## 4. 적재와 임베딩

전체 청크를 DB에 upsert하고, 텍스트 청크만 임베딩한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --limit 100000
```

이미지 자료는 `source_type = 'image_material'`로 저장되지만 임베딩 대상에서는 제외된다.

---

## 5. 검증 쿼리

```sql
SELECT source_type, COUNT(*) AS count
FROM rag.document_chunks
GROUP BY source_type
ORDER BY source_type;
```

```sql
SELECT
  COUNT(*) AS total_chunks,
  COUNT(embedding) AS embedded_chunks,
  COUNT(*) FILTER (WHERE source_type = 'image_material') AS image_chunks
FROM rag.document_chunks;
```

```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'rag'
  AND tablename = 'document_chunks'
ORDER BY indexname;
```

---

## 6. 평가 실행

검색 정확도와 응답 속도:

```powershell
.\.venv\Scripts\python.exe test\HS\evaluate_service_metrics.py
```

RAGAS 포함:

```powershell
.\.venv\Scripts\python.exe test\HS\evaluate_service_metrics.py --ragas
```

RAGAS 50개는 시간이 오래 걸리므로 개발 중에는 다음처럼 제한해서 본다.

```powershell
.\.venv\Scripts\python.exe test\HS\evaluate_service_metrics.py --ragas --ragas-limit 10
```


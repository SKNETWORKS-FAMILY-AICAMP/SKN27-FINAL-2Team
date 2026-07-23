# PostgreSQL RAG 구축 실행 순서

이 문서는 원천 데이터에서 PostgreSQL RAG를 구축하는 실행 순서입니다. 기존 `processed` 파일과 DB 데이터는 결과물이며, 원천 데이터와 전처리 스크립트를 기준으로 생성합니다.

## 0. 준비

프로젝트 루트에서 실행합니다.

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
```

`.env`에는 PostgreSQL 접속 정보와 `OPENAI_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`가 있어야 합니다. PostgreSQL을 먼저 실행합니다.

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
docker compose --env-file .env -f storage\postgresql\docker-compose.yml ps
```

DB를 완전히 새로 만들 때만 아래를 실행합니다. 기존 RAG 데이터가 삭제됩니다.

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml down -v
Remove-Item -LiteralPath storage\postgresql\postgres_data -Recurse -Force
docker compose --env-file .env -f storage\postgresql\docker-compose.yml up -d
```

## 1. 원천 데이터

| 출처 | 원천 경로 | 결과 source_type |
|---|---|---|
| 사료로 본 한국사 | `etl/raw_data/사료로 본 한국사` | `historical_source` |
| 신편 한국사 | `etl/raw_data/신편 한국사 csv` | `historical_overview` |
| 한국사 이미지 자료 | `etl/raw_data/한국사 이미지 자료` | `image_material` |
| 한국민족문화대백과사전 | `etl/raw_data/한국민족문화대백과사전/articles_detail.jsonl` | `aks_encyclopedia` |
| 한국사 연대기 | `etl/raw_data/한국사연대기_연표/history_timeline.csv` | 별도 연표 테이블 |

## 2. 전처리

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_historical_sources.py
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_new_history.py
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_image_materials.py
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_aks_encyclopedia.py
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_history_timeline.py
```

결과는 `etl/preprocessing/history/processed`에 생성됩니다. 백과사전의 `headword`, `origin`, `articleAliases`는 metadata와 모든 청크의 검색 텍스트에 함께 넣습니다.

## 3. 청크 적재와 임베딩

기본값은 백과사전 청크를 포함하지 않으므로 전체 구축 시에는 파일을 명시합니다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py `
  --chunk-file historical_sources.chunks.jsonl `
  --chunk-file new_history.chunks.jsonl `
  --chunk-file image_materials.chunks.jsonl `
  --chunk-file aks_encyclopedia.chunks.jsonl `
  --limit 1000000 `
  --create-index
```

- `image_material`은 DB에는 적재하지만 벡터 임베딩하지 않습니다.
- `chunk_text`가 바뀐 청크만 재임베딩합니다.
- 중단되면 같은 명령을 다시 실행합니다. 이미 임베딩된 청크는 건너뜁니다.
- 원천에서 삭제된 청크도 DB에서 없애야 할 때만 `--delete-missing`을 추가합니다.

## 4. 연표 적재

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\load_history_timeline_to_postgres.py
```

연표는 `rag.document_chunks`가 아니라 `rag.history_timeline`에 적재합니다.

## 5. 검증

```sql
SELECT source_type, COUNT(*) AS total, COUNT(embedding) AS embedded
FROM rag.document_chunks
GROUP BY source_type
ORDER BY source_type;

SELECT COUNT(*) AS timeline_rows FROM rag.history_timeline;

SELECT title, metadata->'aliases' AS aliases
FROM rag.document_chunks
WHERE source_type = 'aks_encyclopedia'
  AND chunk_text ILIKE '%왕건%'
LIMIT 10;
```

## 6. 구축 후 확인

챗봇에서 별칭·인물·비교·연표·이미지 질문을 각각 확인합니다. 검색·답변 평가는 [RAG 운영 기준 검증 가이드](../rag/RAG_운영기준_검증_가이드.md)와 [RAGAS 서비스 평가 가이드](../rag/RAGAS_서비스_평가_가이드.md)를 따릅니다.

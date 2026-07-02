# RAG pgvector 검색 SQL 개선 변경점

## 변경 목적

PostgreSQL pgvector의 HNSW 인덱스를 검색 쿼리에서 직접 사용할 수 있도록 `PgVectorHybridRetriever`의 SQL 구조를 수정했다.

기존 쿼리는 `WITH base AS (...)` 안에서 모든 후보에 대해 벡터 거리와 텍스트 유사도 점수를 먼저 계산한 뒤 정렬했다.
이 구조에서는 `ORDER BY embedding <=> query_vector LIMIT n` 형태가 테이블에 직접 걸리지 않아 HNSW 인덱스를 제대로 타기 어렵다.

## 변경 전 구조

```sql
WITH base AS (
  SELECT
    1 - (embedding <=> :query_vector) AS vector_score,
    similarity(title, :query) AS keyword_score
  FROM rag.document_chunks
  WHERE ...
),
vector_candidates AS (
  SELECT *
  FROM base
  ORDER BY vector_score DESC
  LIMIT :candidate_pool
)
```

문제점:

- `base` 단계에서 벡터 거리 계산과 `similarity()` 계산이 같이 수행됨
- HNSW 인덱스가 선호하는 `ORDER BY embedding <=> ... LIMIT ...` 패턴이 직접 드러나지 않음
- 후보 축소 전에 전체 row에 점수 계산이 발생할 수 있음

## 변경 후 구조

```sql
WITH vector_candidates AS (
  SELECT
    ...,
    1 - (embedding <=> :query_vector) AS vector_score,
    0.0 AS keyword_score
  FROM rag.document_chunks
  WHERE ...
  ORDER BY embedding <=> :query_vector
  LIMIT :candidate_pool
),
keyword_candidates AS (
  SELECT
    ...,
    0.0 AS vector_score,
    similarity(title, :query) * 3.0
      + similarity(chunk_text, :query) AS keyword_score
  FROM rag.document_chunks
  WHERE ...
    AND (title % :keyword OR chunk_text % :keyword OR title ILIKE :keyword_like OR chunk_text ILIKE :keyword_like)
  ORDER BY keyword_score DESC
  LIMIT :candidate_pool
),
merged_candidates AS (
  SELECT * FROM vector_candidates
  UNION ALL
  SELECT * FROM keyword_candidates
)
```

이후 `chunk_id` 기준으로 중복을 합치고 `max(vector_score)`, `max(keyword_score)`를 사용해 최종 점수를 계산한다.

## 기대 효과

- 벡터 후보 검색에서 HNSW 인덱스 사용 가능성이 높아짐
- 키워드 후보 검색에서 trigram 연산자 `%`를 활용할 수 있음
- 최종 점수 계산 대상이 전체 row가 아니라 후보 row로 줄어듦

## 함께 변경한 값

`candidate_pool` 기본값을 `50`에서 `30`으로 낮췄다.

```python
candidate_pool: int = 30
```

속도는 좋아질 수 있지만 검색 품질이 떨어지면 `40`으로 조정한다.

## 확인 방법

문법 확인:

```powershell
.\.venv\Scripts\python.exe -m py_compile app\chatbot\rag\pgvector_retriever.py
```

검색 동작 확인:

```powershell
.\.venv\Scripts\python.exe -c "from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever; rows=PgVectorHybridRetriever().search('환웅 알려줘',3); print(len(rows)); [print(i+1, round(r.score,3), r.title[:70]) for i,r in enumerate(rows)]"
```

실제 인덱스 사용 여부는 PostgreSQL에서 `EXPLAIN ANALYZE`로 확인한다.

```sql
EXPLAIN ANALYZE
SELECT chunk_id, title
FROM rag.document_chunks
WHERE embedding IS NOT NULL
ORDER BY embedding <=> '[질문_임베딩]'::vector
LIMIT 30;
```

`Index Scan using document_chunks_embedding_cosine_idx` 또는 HNSW 인덱스명이 보이면 벡터 인덱스를 타는 것이다.

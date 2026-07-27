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

## 이전 변경 기록: 후보 수 조정

초기 개선 시점에는 `candidate_pool` 기본값을 `50`에서 `30`으로 낮추는 실험을 기록했다.

```python
candidate_pool: int = 30
```

속도는 좋아질 수 있지만 검색 품질이 떨어지면 `40`으로 조정한다.

> 현재 운영 코드는 벡터, Trigram, BM25 채널별 후보 수를 `50`으로 사용한다. 이 절은 과거 실험 기록이다.

## 2026-07-15: MeCab BM25, RRF, Trigram 채널 분리 측정

이 문서를 RAG 검색 변경과 성능 실험의 누적 이력으로 사용한다.

### 적용 내용

- MeCab 명사 토큰을 `search_tokens`에 저장하고, PostgreSQL `search_vector` GIN 인덱스로 BM25 검색을 추가했다.
- 벡터 HNSW, Trigram, BM25 후보를 각각 수집해 RRF(Reciprocal Rank Fusion)로 병합한다.
- 벡터 검색 임베딩은 키워드 확장문이 아니라 사용자의 원문 질문을 사용한다.
- `RAG_TRIGRAM_ENABLED` 환경 변수로 Trigram 후보 채널을 켜거나 끌 수 있게 했다. 운영값은 `false`다.

### 단일 질문 지연시간 측정

측정 질문: `세종대왕 업적 알려줘`

| 구분 | Trigram 사용 | Trigram 미사용 |
|---|---:|---:|
| pgvector 검색 | 7.311초 | 2.039초 |
| DB 후보 검색 및 리랭킹 | 6.261초 | 0.545초 |
| 전체 응답 | 10.819초 | 5.197초 |
| 검색 결과 수 | 5건 | 5건 |
| 검색 실패 | false | false |

Trigram 채널을 끈 단일 실행에서는 검색 지연이 크게 줄었다. 다만 이 값만으로 기본값을 변경하지 않는다. 오타, 띄어쓰기 변형, RAGAS 품질 지표를 함께 비교한 뒤 Trigram을 기본 채널에서 제외하거나 BM25 실패 시 fallback으로 전환한다.

### 측정 명령

```powershell
# Trigram 사용
Remove-Item Env:RAG_TRIGRAM_ENABLED -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe test\HS\measure_rag_stage_latency.py "세종대왕 업적 알려줘"

# Trigram 미사용
$env:RAG_TRIGRAM_ENABLED="false"
.\.venv\Scripts\python.exe test\HS\measure_rag_stage_latency.py "세종대왕 업적 알려줘"

# RAGAS 품질 비교
.\.venv\Scripts\python.exe test\HS\evaluate_service_metrics.py --ragas --ragas-limit 15
```

측정 후 환경 변수는 제거한다.

```powershell
Remove-Item Env:RAG_TRIGRAM_ENABLED -ErrorAction SilentlyContinue
```

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

## 2026-07-15: 개요형 BGE 리랭킹 적용 및 평가

### 변경 내용

- 개요형 질문도 BGE 리랭커로 최종 순서를 정하도록 변경했다. 이전에는 RRF 결과를 그대로 반환했다.
- BGE 점수로 `PgSearchResult.score`를 덮어쓰면 `has_enough_evidence()`가 기존 RRF 근거 점수 대신 BGE 점수로 판단해, 문맥이 있어도 `검색 결과가 없습니다.`를 반환하는 회귀가 발생했다.
- 최종 구현은 BGE 점수를 정렬에만 사용하고, `score`는 RRF 점수를 유지한다. 따라서 근거 충족 판정은 기존 기준을 그대로 사용한다.

### 전체 평가 결과

| 평가 항목 | 결과 | 기준 | 상태 |
|---|---:|---:|---|
| 검색 속도 | 12.13초 | 2.0초 이내 | FAIL |
| LLM 답변 생성 속도 | 8.68초 | 5.0초 이내 | FAIL |
| 전체 응답 속도 | 20.79초 | 7.0초 이내 | FAIL |
| RAGAS Context Precision | 0.82 | 0.80 이상 | PASS |
| RAGAS Context Recall | 0.87 | 0.80 이상 | PASS |
| RAGAS Faithfulness | 0.91 | 0.80 이상 | PASS |
| RAGAS Answer Relevance | 0.81 | 0.80 이상 | PASS |

Precision은 리랭커 적용 전 0.67에서 0.82로 개선됐다. 반면 CPU에서 모든 개요형 질문에 BGE를 실행하면서 속도가 기준을 넘었다.

CPU 환경 측정에서는 `RAG_RERANKER_ENABLED=false` 전환을 실험했다. 이후 로컬 측정에서 리랭커 사용 시에도 검색 속도 기준을 충족해 운영값을 다시 `true`로 복원했다. 후보 수, Top-K, 임베딩, HNSW는 변경하지 않는다.

## 2026-07-21: 서비스 Top-5 전달 구성

RAGAS 서비스 평가에서는 `top_k=5`, 프롬프트 근거 길이 260자로 측정했다. 이때 리랭커가 켜진 일반 텍스트 질의의 실제 파이프라인은 다음과 같다.

```text
벡터 후보 최대 50개 + MeCab BM25 후보 최대 50개
→ RRF 병합·중복 제거
→ RRF 상위 25개 (= 최종 Top-K 5 × 5)
→ BGE CrossEncoder 리랭킹
→ 최종 Top-5를 LLM 근거로 전달
```

- `50`은 **채널별 후보 풀**(`RAG_RETRIEVAL_CANDIDATE_POOL`)이다. 중복 제거 전에는 두 채널 합계가 최대 100개일 수 있다.
- BGE는 기본적으로 이 전체 후보 풀이 아니라 RRF 상위 25개만 평가한다.
- `RAG_RERANK_CANDIDATE_POOL=50`을 설정하면 RRF 상위 50개를 BGE에 넣을 수 있으나, CPU 환경에서는 리랭킹 시간이 증가한다.

| 평가 항목 | Top-20 / 260자 | Top-5 / 260자 |
|---|---:|---:|
| 검색 속도 | 48.58초 | 19.88초 |
| 전체 응답 속도 | 53.04초 | 24.39초 |
| RAGAS Context Precision | 0.77 | 0.82 |
| RAGAS Context Recall | 0.88 | 0.85 |
| RAGAS Faithfulness | 0.94 | 0.93 |
| RAGAS Answer Relevance | 0.81 | 0.81 |

Top-5는 모든 RAGAS 기준(0.80)을 통과했다. 이 결과는 **최종 LLM 전달 수**의 선택 기준이며, 검색 후보 풀을 5개로 줄인다는 의미는 아니다.

## 2026-07-20: 골든셋 BGE 최종 Top-K 선정

`golden_saved_rerank_ab_results.csv`의 35개 strict 골든 질문에서 RRF와 BGE의 같은 Top-K를 비교했다. 검색 후보 수는 50개로 유지하고, BGE가 최종 순서만 조정했다.

이 평가는 RAGAS 기반 최종 답변 품질 평가가 아니라, 같은 RRF 후보에서 BGE가 문서 순서를 개선하는지와 최종 Top-K를 고르는 rerank 최적화 실험이다. 따라서 저장된 35개 strict 질문 표본으로 비교하며, 서비스 전체 품질은 별도 RAGAS 평가로 검증한다.

| 최종 Top-K | RRF Precision / Recall | BGE Precision / Recall |
|---:|---|---|
| 5 | 0.9079 / 0.8905 | 0.9761 / 0.9286 |
| 10 | 0.9055 / 0.9476 | 0.9627 / 0.9476 |
| 15 | 0.8866 / 0.9429 | 0.9553 / 0.9667 |
| 20 | 0.8783 / 0.9810 | 0.9567 / 0.9810 |
| 30 | 0.8499 / 0.9810 | 0.9282 / 0.9810 |

- BGE Top-20은 RRF Top-20과 Recall(0.9810)은 같고 Precision은 0.8783에서 0.9567로 개선됐다.
- Top-30 이상은 Recall 증가 없이 Precision만 하락했다.
- 따라서 평가 기준 권장값은 **후보 50개 → BGE rerank → 최종 Top-20**이다.
- API 기본 `top_k`는 20이고 최대 20이다. 후보 수나 reranker 모델을 바꾸면 같은 방식으로 다시 비교한다.

## 2026-07-15: Trigram 후보 채널 제거

- `keyword_candidates`/`keyword_ranked` CTE와 `RAG_TRIGRAM_ENABLED` 환경 변수를 제거했다.
- 검색은 pgvector HNSW와 MeCab BM25 후보를 RRF로 병합하고, BGE 리랭커가 최종 정렬한다.
- Trigram 인덱스는 기존 DB에 남아 있어도 검색 쿼리에서 사용하지 않는다.

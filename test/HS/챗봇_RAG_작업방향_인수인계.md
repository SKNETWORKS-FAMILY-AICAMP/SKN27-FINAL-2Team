# 챗봇 RAG 작업 방향 인수인계

작성일: 2026-07-15

이 문서는 챗봇과 한국사 RAG의 현재 상태, 실험 결과, 다음 작업 순서를 기록한다.

## 1. 현재 담당 범위

- 챗봇 화면/API: `app/chatbot/`
- RAG 서비스 흐름: `app/chatbot/rag_service.py`
- PostgreSQL 검색기: `app/chatbot/rag/pgvector_retriever.py`
- LLM 구조화 답변: `app/chatbot/rag/llm_answer_generator.py`
- Neo4j 관계 보강: `app/chatbot/graph_service.py`
- 평가/지연시간 측정: `test/HS/`

그래프 적재 스키마와 Neo4j ETL은 다른 담당 범위이므로, 챗봇에서는 `graph_service.py`의 조회 결과만 사용한다.

## 2. 현재 RAG 파이프라인

1. 질문 intent를 `concept`, `question`, `image`, `chat`, `casual`로 정규화한다.
2. 최근 대화는 최대 5턴을 보관하고, 지시어 또는 문제 문맥이 있는 후속 질문에만 검색어에 합친다.
3. 관계형/비교형/개념 질문은 필요할 때 Neo4j에서 1-hop 또는 2-hop 관계 키워드를 보강한다.
4. PostgreSQL에서 다음 후보를 검색한다.
   - OpenAI 임베딩 + pgvector HNSW 벡터 후보
   - Trigram 후보
   - MeCab 명사 토큰 + PostgreSQL FTS(BM25) 후보
5. 후보를 RRF(Reciprocal Rank Fusion)로 병합하고, 일반 질문은 BGE reranker로 최종 정렬한다.
6. 검색 근거를 LLM에 전달해 질문에 맞는 동적 섹션 JSON을 생성한다.

LLM 섹션 제목은 고정된 `정치와 제도`, `문화와 업적` 틀이 아니다. 관계/비교/개념 질문별로 프롬프트에서 2~4개 섹션 제목을 생성한다.

## 3. PostgreSQL 검색 인덱스

- `document_chunks_embedding_cosine_idx`: pgvector HNSW, cosine distance
- `document_chunks_search_vector_idx`: MeCab `search_tokens` 기반 FTS GIN
- `document_chunks_title_trgm_idx`, `document_chunks_text_trgm_idx`: Trigram GIN
- `document_chunks_metadata_gin_idx`: metadata GIN

MeCab BM25 구성과 DB 적용 방법은 `docs/rag/MeCab_BM25_검색_적용_가이드.md`를 따른다.
기존 임베딩과 HNSW 인덱스를 다시 만들 필요는 없다.

## 4. 현재 환경 변수

```env
RAG_BM25_ENABLED=true
RAG_RERANKER_ENABLED=true
RAG_TRIGRAM_ENABLED=true
```

- `RAG_TRIGRAM_ENABLED`는 2026-07-15에 추가했다.
- 기본값은 `true`이며, Trigram 후보 CTE와 RRF 병합 채널을 실제로 제외할 수 있다.
- 환경 변수 변경 후 웹 서버는 재시작한다. `test/HS` CLI는 매번 새 프로세스라 바로 반영된다.

## 5. 최근 성능 현황

최근 전체 RAGAS 평가 결과:

| 평가 항목 | 결과 | 기준 | 상태 |
|---|---:|---:|---|
| 연결성(Graph) | 100.0% | 95% 이상 | PASS |
| 검색 속도 | 7.00초 | 2.0초 이내 | FAIL |
| LLM 답변 생성 속도 | 4.01초 | 5.0초 이내 | PASS |
| 전체 응답 속도 | 11.01초 | 7.0초 이내 | FAIL |
| RAGAS Context Precision | 0.68 | 0.80 이상 | FAIL |
| RAGAS Context Recall | 0.92 | 0.80 이상 | PASS |
| RAGAS Faithfulness | 0.86 | 0.80 이상 | PASS |
| RAGAS Answer Relevance | 0.77 | 0.80 이상 | FAIL |

해석:

- Recall이 높으므로, 문서를 더 많이 전달하거나 후보군을 늘리는 방향은 우선순위가 낮다.
- Precision과 Answer Relevance가 낮으므로 상위 후보의 잡음을 줄이는 방향이 우선이다.

## 6. Trigram 분리 측정 결과

측정 질문: `세종대왕 업적 알려줘`

| 구분 | Trigram 사용 | Trigram 미사용 |
|---|---:|---:|
| pgvector 검색 | 7.311초 | 2.039초 |
| DB 후보 검색 및 리랭킹 | 6.261초 | 0.545초 |
| 전체 응답 | 10.819초 | 5.197초 |
| 검색 결과 수 | 5건 | 5건 |
| 검색 실패 | false | false |

단일 질문 기준으로는 Trigram이 가장 큰 검색 지연 원인이다. 다만 오타/표기 변형 검색과 RAGAS 품질을 아직 비교하지 않았으므로 기본값을 즉시 `false`로 바꾸지 않았다.

## 7. 바로 실행할 명령

### 단계별 지연시간

```powershell
.\.venv\Scripts\python.exe test\HS\measure_rag_stage_latency.py "세종대왕 업적 알려줘"
```

주요 출력:

- `embedding_sec`: 임베딩 API 호출 시간
- `graph_sec`: Neo4j 관계 보강 시간
- `pgvector_search_sec`: PostgreSQL 후보 검색과 reranker를 포함한 검색 시간
- `db_and_rerank_sec`: 위 검색 시간에서 임베딩 시간을 뺀 값
- `llm_generation_sec`: LLM 답변 생성 시간
- `total_sec`: 전체 RAG 호출 시간

### Trigram 품질/속도 비교

```powershell
$env:RAG_TRIGRAM_ENABLED="false"
.\.venv\Scripts\python.exe test\HS\measure_rag_stage_latency.py "세종대왕 업적 알려줘"
.\.venv\Scripts\python.exe test\HS\evaluate_service_metrics.py --ragas --ragas-limit 15
Remove-Item Env:RAG_TRIGRAM_ENABLED -ErrorAction SilentlyContinue
```

평가는 Trigram 사용/미사용을 같은 골든 질문, 같은 `--ragas-limit`으로 비교한다.

## 8. 다음 작업 우선순위

1. Trigram 미사용 상태로 RAGAS 평가를 실행해 Context Precision, Recall, Faithfulness, Answer Relevance를 비교한다.
2. 품질 저하가 작으면 Trigram을 기본 채널에서 제외한다.
3. 오타/띄어쓰기 변형에서만 품질 저하가 확인되면, Trigram을 항상 실행하지 않고 BM25 결과가 없을 때만 fallback으로 호출한다.
4. Trigram을 정리한 뒤에도 Precision이 낮으면 개요형 질문에만 상위 소수 후보 리랭킹을 적용하는 실험을 한다.

다음 단계 전에 하지 않을 것:

- 임베딩 재생성
- HNSW 재생성
- Top-K를 무조건 3개로 축소
- 핵심어가 없는 청크를 일괄 제거

위 방식들은 이전 실험에서 Context Recall과 Faithfulness를 떨어뜨릴 가능성이 확인됐다.

## 9. 변경 이력 문서

검색 SQL/인덱스 변경 및 측정 이력은 아래 문서에 계속 누적한다.

- `docs/rag/RAG_pgvector_검색_SQL_개선_변경점.md`
- `docs/rag/MeCab_BM25_검색_적용_가이드.md`
- `docs/rag/RAGAS_서비스_평가_가이드.md`

이 파일은 작업 재개를 위한 요약이고, 세부 변경 이력의 원본은 첫 번째 문서다.

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
```

- Trigram 후보 채널은 제거했다. 검색은 벡터와 BM25 후보를 RRF로 병합한 뒤 BGE 리랭커로 정렬한다.
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
.\.venv\Scripts\python.exe test\HS\service\measure_rag_stage_latency.py "세종대왕 업적 알려줘"
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
.\.venv\Scripts\python.exe test\HS\service\evaluate_service_metrics.py --ragas --ragas-limit 15
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

## 10. 2026-07-15 리랭커 적용 및 전체 평가

### 변경 내용

- 개요형 질문도 BGE 리랭커로 최종 순서를 정하도록 변경했다. 기존에는 개요형이 RRF 결과를 그대로 반환했다.
- 첫 적용에서는 BGE 점수로 `PgSearchResult.score`를 덮어썼다. 이 값은 이후 `has_enough_evidence()`의 근거 충족 판정에도 사용되므로, 문맥이 있어도 `검색 결과가 없습니다.`가 반환되는 회귀가 발생했다.
- 최종 수정에서는 BGE를 **정렬 순서에만** 사용하고, `score`에는 기존 RRF 점수를 유지한다. 따라서 근거 충족 판정은 기존 RRF 기준으로 유지된다.

### 전체 평가 결과

| 평가 항목 | 결과 | 기준 | 상태 |
|---|---:|---:|---|
| 연결성(Graph) | 100.0% | 95% 이상 | PASS |
| 검색 속도 | 12.13초 | 2.0초 이내 | FAIL |
| LLM 답변 생성 속도 | 8.68초 | 5.0초 이내 | FAIL |
| 전체 응답 속도 | 20.79초 | 7.0초 이내 | FAIL |
| RAGAS Context Precision | 0.82 | 0.80 이상 | PASS |
| RAGAS Context Recall | 0.87 | 0.80 이상 | PASS |
| RAGAS Faithfulness | 0.91 | 0.80 이상 | PASS |
| RAGAS Answer Relevance | 0.81 | 0.80 이상 | PASS |

해석:

- 품질 지표는 모두 기준을 통과했다. Precision은 리랭커 적용 전 0.67에서 0.82로 올랐다.
- 속도 저하는 CPU에서 모든 개요형 질문에 BGE 리랭커를 실행한 영향이다. 리랭커 CPU 점유가 답변 생성 시간에도 영향을 줬다.

### 다음 작업

속도 우선 운영을 위해 `RAG_RERANKER_ENABLED=false`로 전환했다. BGE는 CPU 환경에서 검색 시간을 크게 늘리므로, GPU 배포 또는 더 가벼운 리랭커를 검증하기 전까지 기본 경로에서 사용하지 않는다. 후보 수, Top-K, 임베딩, HNSW는 변경하지 않는다.

## 11. 2026-07-17 백과사전 적재 후 검색 보정

### 변경 전 기준

- `RAG_RERANKER_ENABLED=false`
- 검색 속도 4.10초, 전체 응답 7.88초
- Context Precision 0.68, Recall 0.88, Faithfulness 0.90, Answer Relevance 0.81
- `aks_encyclopedia` 131,278개 청크 적재 완료

### 확인된 문제와 최소 수정

- `임진왜란 전개 과정을 요약해줘`에서 `전개`, `과정`까지 BM25 AND 검색어에 포함돼 관련 문서가 밀렸다.
- FTS 결과에 다시 `chunk_text ILIKE`를 적용해 BM25 후보 검색만 약 2.62초 걸렸다.
- `전개`, `과정`, `요약해줘`, `정리해줘`를 BM25 불용어로 추가하고, 중복된 `ILIKE` 사전 필터를 제거했다. BGE 리랭커·Top-K·후보 수는 변경하지 않았다.
- 국가·왕조명(`고구려`, `신라`, `백제`, `고려`, `조선`, `발해`, `가야`)은 검색 주제이므로 `HISTORY_STOPWORDS`에서 제외했다.

### 복구 기준

품질 또는 Recall이 저하되면 이 절의 변경 두 가지를 되돌리고, 위의 변경 전 기준으로 복구한다.

## 12. 2026-07-18 근거 판정·이미지 응답·API 안정화

### 근거 판정

- `rag/evidence.py`의 `has_enough_evidence()`를 `(results, intent)`만 받도록 단순화했다.
- RRF `score`는 순위용 값(약 0.01~0.05)이라 임계값으로 쓰지 않는다. 이전의 `MIN_COMBINED_SCORE`, `FOLLOW_UP_MIN_COMBINED_SCORE`, `score >= 1.8` 분기는 제거했다.
- 텍스트 RAG는 `keyword_score >= 0.12` 또는 `vector_score >= 0.35`일 때만 근거가 충분하다고 본다.
- 연표는 답변 문맥에만 추가한다. 시대·분야만 일치하는 연표가 벡터/BM25 검색 실패를 통과시키지 않도록 `timeline_sources`를 근거 판정에서 제외했다.
- 이미지 RAG는 기존대로 이미지 URL이 있는 `image_material` 결과가 있어야 통과한다.

### 이미지·구조화 응답

- 이미지 질문은 LLM을 호출하지 않고 `build_image_answer()`로 자료명·출처·설명을 직접 구성한다. 응답의 `llm`은 `null`이다.
- 효과: 이미지 질문의 LLM 지연시간·비용을 제거하고, 개념형 교재 요약 프롬프트가 이미지 답변에 적용되던 문제를 없앴다.
- `exam_points`는 프롬프트에서 빈 배열을 요구하므로, 사용되지 않던 부분 문자열 필터(`부족`, `부인` 등)를 삭제했다. 이제 배열 여부만 정규화하며 정상 역사 용어를 임의로 제거하지 않는다.

### API 안정화

- 이미지 프록시에 로그인 요구를 추가했다.
- `top_k`는 정수가 아니면 400을 반환하고, 1~20 범위로 제한한다.
- RAG·이미지 프록시의 내부 예외 상세는 응답에 노출하지 않고 서버 로그에만 남긴다.
- 채팅 기록 저장이 실패해도 이미 생성된 RAG 답변은 정상 반환한다.

### 검증

```powershell
cd app
python manage.py test chatbot
```

- 이미지 경로가 LLM을 호출하지 않는지, 이미지 근거 없음이 `not_found`인지 확인했다.
- `exam_points` 배열 보존, `top_k` 검증·상한, 내부 오류 비노출, 저장 실패 시 응답 반환, 이미지 프록시 로그인 요구를 테스트했다.

## 13. 2026-07-18 리랭커 후보 수 실험 도구

- 벡터와 BM25에서 각각 수집하는 후보 수는 `RAG_RETRIEVAL_CANDIDATE_POOL`으로, RRF 뒤 BGE에 보내는 수는 `RAG_RERANK_CANDIDATE_POOL`으로 분리했다.
- 기본값은 `50`, `0`이며 `0`은 기존 자동 리랭커 후보 수(`top_k * 5`)를 유지한다. 따라서 환경변수를 설정하지 않으면 운영 동작은 바뀌지 않는다.
- 단일 질문 점수화 스크립트: `test/HS/measure_rerank_candidate_pool.py`. 벡터 1,000개·BM25 1,000개 수집 뒤 RRF 후보 점수만 기록하며 BGE는 호출하지 않는다.

```powershell
# 벡터 1,000 + BM25 1,000개를 모으고 RRF 상위 500개 점수만 기록
.\.venv\Scripts\python.exe test\HS\measure_rerank_candidate_pool.py "고구려 전성기 왕 알려줘"

# RRF 상위 100개 점수만 기록
.\.venv\Scripts\python.exe test\HS\measure_rerank_candidate_pool.py "고구려 전성기 왕 알려줘" --rrf-pool 100

# RAGAS를 별도로 볼 때는 BGE를 끈 채 같은 RRF 후보 수를 설정한다.
$env:RAG_RERANKER_ENABLED="false"
$env:RAG_RETRIEVAL_CANDIDATE_POOL="1000"
.\.venv\Scripts\python.exe test\HS\evaluate_service_metrics.py --ragas --ragas-limit 12
```

- RRF 후보별 점수는 `test/HS/rrf_candidate_scores.csv`에 누적된다. RRF 순위·점수와 벡터·BM25 점수를 확인할 수 있다.
- RRF 결과를 바로 BGE로 넘길 때는 `test/HS/rerank_rrf_candidates.py`를 실행한다. 이 스크립트는 CSV의 `chunk_text`를 사용하므로 DB를 다시 검색하지 않는다.

```powershell
.\.venv\Scripts\python.exe test\HS\rerank_rrf_candidates.py
```

- BGE 점수·순위와 최종 top 5 포함 여부는 `test/HS/bge_candidate_scores.csv`에 누적된다. 기본값은 RRF CSV의 가장 최근 실행 결과이며, 이전 실행을 지정하려면 `--measured-at`을 사용한다.
- 기본 `--rrf-pool`은 500이며, 비교 시 `15, 30, 50, 100, 200, 500, 1000` 순으로 실행한다.
- `test/HS/visualize_rerank_topk.py`는 한 번 점수화한 500개 BGE 결과에서 top 5·10·20·30·40·50의 점수 곡선과 평균·컷오프 점수를 SVG로 만든다. BGE를 다시 호출하지 않는다.

```powershell
.\.venv\Scripts\python.exe test\HS\visualize_rerank_topk.py
Start-Process test\HS\rerank_topk_comparison.svg
```

- `rerank_topk_comparison.svg`는 BGE 순위별 점수 곡선에 top 5·10·20·30·40·50 경계를 표시한다.
- `rerank_rank_comparison.svg`는 RRF 순위와 BGE 순위를 산점도로 비교하고, top-k별 리랭크 전후 동일 문서 비율을 보여준다. RRF와 BGE 점수는 스케일이 달라 점수 숫자를 직접 비교하지 않는다.
- `rerank_topk_summary.csv`에는 각 조건의 평균 BGE 점수, 마지막(컷오프) BGE 점수, RRF·BGE top-k 동일 문서 수·비율을 저장한다.
- 서비스 품질 A/B 그래프는 `test/HS/visualize_rerank_ab_evaluation.py`를 사용한다. 리랭크 전·후 `service_eval_results.csv`를 각각 복사해 입력하면 RAGAS와 속도 비교 SVG·요약 CSV를 생성한다.

```powershell
Copy-Item etl\preprocessing\history\embedding\service_eval_results.csv test\HS\service_eval_before_rerank.csv
# 리랭크를 켠 동일 조건 평가 후 다시 복사
Copy-Item etl\preprocessing\history\embedding\service_eval_results.csv test\HS\service_eval_after_rerank.csv
.\.venv\Scripts\python.exe test\HS\visualize_rerank_ab_evaluation.py --before test\HS\service_eval_before_rerank.csv --after test\HS\service_eval_after_rerank.csv
Start-Process test\HS\rerank_ab_evaluation.svg
```

- 저장된 한 질문의 후보만 재평가하려면 `test/HS/evaluate_saved_rerank_candidates.py`를 사용한다. RRF CSV와 BGE CSV의 같은 `measured_at`을 읽어 DB 재검색 없이 top 5·10·15·20·30·40·50의 RRF/BGE 문맥에 대해 RAGAS Context Precision·Recall만 평가하고 SVG로 비교한다. 기준 답변은 반드시 `--reference`로 제공해야 하며, 1건 평가이므로 전체 품질 대표값으로 사용하지 않는다.
- 코랩에서는 위 스크립트와 `rrf_candidate_scores.csv`, `bge_candidate_scores.csv` 세 파일만 업로드하고 RAGAS 관련 패키지를 설치하면 된다. PostgreSQL·Neo4j 연결은 사용하지 않는다.

## 14. 골든 질문 RRF/BGE top-k 비교

- `test/HS/evaluate_golden_rerank_topk.py`는 `golden_questions_strict_matched_444.jsonl`의 텍스트형 35문항만 대상으로 한다. 질문마다 벡터·BM25 후보 1,000개에서 RRF 상위 50개를 수집하고, BGE도 이 50개만 한 번 점수화한다.
- 각 질문의 RRF/BGE 결과를 top 5·10·15·20·30·40·50으로 잘라 Context Precision·Recall을 평가한다. 총 490개 RAGAS 행이며, 이미지 4건과 study_tip 1건은 제외한다.
- `test/HS/golden_rrf_candidate_scores.csv`, `test/HS/golden_bge_candidate_scores.csv`에는 각각 질문별 50개만 저장한다. 청크 텍스트와 RRF·벡터·BM25·BGE 점수가 포함된다.
- BGE 점수화 결과도 `test/HS/golden_bge_candidate_scores.csv`에 질문별 50개씩 저장한다. RAGAS 단계에서 실패해도 BGE 점수·순위는 남는다.
- RRF 점수만 먼저 저장할 때는 `collect_golden_rrf_candidates.py`를 사용한다. 질문별 RRF 상위 50개를 CSV로 저장하고 BGE 리랭크·RAGAS 평가는 수행하지 않는다.
- 저장된 골든 RRF CSV를 로컬에서 BGE로 점수화할 때는 `rerank_saved_golden_candidates.py`를 사용한다. PostgreSQL 재검색 없이 `golden_bge_candidate_scores.csv`를 만든다.

```powershell
.\.venv\Scripts\python.exe test\HS\rerank\golden\rerank_saved_golden_candidates.py
.\.venv\Scripts\python.exe test\HS\rerank\golden\evaluate_saved_golden_rerank_candidates.py
Start-Process test\HS\rerank\golden\golden_saved_rerank_ab_evaluation.svg
```

- `evaluate_saved_golden_rerank_candidates.py`는 텍스트형 골든 35문항을 대상으로 RRF/BGE top 5·10·15·20·30·40·50 문맥의 Context Precision·Recall을 평가한다. 총 490개 RAGAS 행이며, CSV와 SVG 그래프를 생성한다.

## 15. 최신 서비스 RAGAS 평가 결과 (2026-07-20)

평가 파일: `test/HS/rerank/golden/service_eval_results.json`  
대상: 이미지 4건·study_tip 1건을 제외한 텍스트형 골든 질문 35건, 서비스 RAG 전체 호출

| 평가 항목 | 측정 결과 | 기준 | 판정 |
|---|---:|---:|---|
| 연결성(Graph) | 100.0% | 95% 이상 | PASS |
| 검색 속도 | 23.34s | 2.0s 이내 | FAIL |
| LLM 답변 생성 속도 | 4.35s | 5.0s 이내 | PASS |
| 전체 응답 속도 | 27.69s | 7.0s 이내 | FAIL |
| RAGAS Context Precision | 0.85 | 0.80 이상 | PASS |
| RAGAS Context Recall | 0.81 | 0.80 이상 | PASS |
| RAGAS Faithfulness | 0.92 | 0.80 이상 | PASS |
| RAGAS Answer Relevance | 0.80* | 0.80 이상 | FAIL |

- 검색 품질 4개 지표 중 Context Precision·Recall·Faithfulness는 기준을 통과했다.
- Answer Relevance의 반올림 전 평균은 `0.796073352782`(유효 33건)이다. 화면 표기상 0.80이나 기준 미만이라 FAIL로 판정됐다. 반올림값만 보고 PASS로 바꾸지 않는다.
- 현재 병목은 생성(4.35초)이 아니라 검색(23.34초)이다. 품질 점수를 유지하면서 검색 경로·후보 수·DB 지연을 별도로 최적화한다.

## 16. 현재 검색 가산점 기준 상태 (2026-07-20)

구현 위치: `app/chatbot/rag/pgvector_retriever.py`의 `ranked` CTE

```text
RRF = 벡터·BM25 채널별 Σ 1 / (60 + 채널 순위)
최종 점수 = RRF + 출처 보정 + 핵심어 직접 포함 보정
```

| 조건 | 보정값 | 목적 |
|---|---:|---|
| 질문에 개요어(설명·알려·업적·정리 등)가 있고 `source_type = aks_encyclopedia` | +0.0005 | 한민족대백과 개요 설명 우선 |
| 같은 조건에서 `source_type = historical_source` | -0.00015 | 원사료 단독 상위 노출 완화 |
| 개요형이며 focus term이 제목 또는 청크 본문에 직접 포함 | +0.0018 | 질문 핵심어와 직접 맞는 청크 우선 |

- 이 판정은 API의 `intent == concept`이 아니라 질문 문자열의 개요어 존재 여부로 동작한다.
- 가산점은 BGE 리랭커 **전** 후보군 정렬·선별에만 영향을 준다. 리랭커가 켜진 경우 BGE가 후보군을 다시 점수화해 최종 top-k 순서를 결정한다.
- `aks_encyclopedia`(한민족대백과)에 +0.0005 출처 가산점을 적용한다. 기존 전체 RRF·BM25 후보 수집은 유지하며, 이후 35개 텍스트 골든 질문 RAGAS로 전후 비교한다.
- 출처 제한 A/B 테스트용 환경변수 `RAG_ALLOWED_SOURCE_TYPES`를 추가했다. 예를 들어 `aks_encyclopedia`로 설정하면 벡터·BM25 후보를 한민족대백과로만 제한하고, 연표 보조 문맥도 제외한다. 미설정 상태가 기본 전체 출처 검색이다.

```powershell
$env:RAG_ALLOWED_SOURCE_TYPES="aks_encyclopedia"
.\.venv\Scripts\python.exe test\HS\service\evaluate_service_metrics.py --golden-file test\HS\rerank\golden\dataset\golden_questions_strict_matched_444.jsonl --ragas --ragas-limit 35 --out-prefix test\HS\rerank\golden\evaluation\service_eval_aks_only
Remove-Item Env:RAG_ALLOWED_SOURCE_TYPES
```

## 17. 고전 검색 지표용 수동 정답 라벨 (테스트 전용)

- 자동 매칭 파일 `test/HS/rerank/golden/dataset/golden_matched_documents_444.json`은 40문항에 정답 후보 문서 499개(문항당 평균 12.5개)를 가진다. 이 상태는 Recall@K의 분모가 과도해 고전 검색 지표의 기준으로 쓰지 않는다.
- `test/HS/rerank/golden/dataset/golden_relevance_labels.csv`는 수동 확정 라벨 파일이다. `relevance=2`는 핵심 근거, `1`은 보조 근거, `0` 또는 빈 값은 비관련/미확정이다. 문항당 핵심 문서 1~3개를 확정한다.
- 라벨 파일은 테스트 산출물이며 챗봇·DB·기존 골든 질문을 변경하지 않는다.

```powershell
# 자동 후보 499개로 빈 라벨 템플릿 생성(이미 라벨이 있으면 덮어쓰지 않음)
.\.venv\Scripts\python.exe test\HS\rerank\golden\dataset\prepare_golden_relevance_labels.py

# golden_relevance_labels.csv에서 relevance를 수동 입력한 후 실행
.\.venv\Scripts\python.exe test\HS\rerank\golden\evaluation\evaluate_golden_classical_ir.py --top-ks 1,3,5
```

- 평가지표기는 `rerank/golden/candidates/golden_rrf_candidate_scores.csv`, `golden_bge_candidate_scores.csv`를 DB 재검색 없이 비교하며 Precision@K, Recall@K, Hit Rate@K, MRR@K, MAP@K, nDCG@K를 `evaluation/golden_classical_ir_summary.csv`에 저장한다.

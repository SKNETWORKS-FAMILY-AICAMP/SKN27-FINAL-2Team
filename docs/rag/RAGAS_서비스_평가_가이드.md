# RAGAS 서비스 평가 가이드

## 목적

실제 챗봇 RAG 경로를 호출해 검색 품질과 응답 시간을 함께 측정한다.
평가용 Qdrant 또는 별도 데모 리트리버는 사용하지 않고, 프로젝트의
`PgVectorHybridRetriever`, Neo4j 컨텍스트, 답변 생성 흐름을 그대로 사용한다.

## 평가 방식 변경 내역

### 변경 전

- 골든 질문의 `expected_keywords`, `expected_era`, `expected_source_type`를 기준으로 검색 결과가 맞는지 주로 확인했다.
- 평가는 검색 품질 중심이었다.
  - 상위 검색 결과에 기대 키워드가 포함되는지 확인
  - pgvector 검색 점수와 응답 속도 확인
  - 필요 시 RAGAS Faithfulness만 보조로 확인
- LLM이 실제로 만든 최종 답변의 문장 품질, 질문 적합도, 근거 충실도는 한 번에 평가하지 못했다.

### 변경 후

- 실제 챗봇 서비스 경로인 `build_history_rag_answer()`를 호출해 검색, Graph 보강, LLM 답변 생성까지 포함해 평가한다.
- RAGAS 입력 데이터는 실제 실행 결과에서 만든다.
  - `user_input`: 골든 질문
  - `response`: 실제 챗봇 답변
  - `retrieved_contexts`: 실제 검색 문맥
  - `reference`: 사람이 작성한 기준 답변 또는 기대 키워드
- RAGAS 4개 지표를 함께 본다.
  - Context Precision
  - Context Recall
  - Faithfulness
  - Answer Relevance
- 즉, 단순 검색 검증에서 실제 런타임 RAG 답변 검증으로 변경했다.

## 평가 데이터

기본 데이터셋은 다음 파일이다.

```text
etl/preprocessing/history/embedding/golden_questions.jsonl
```

각 질문은 다음 정보를 가진다.

| 필드 | 용도 |
|---|---|
| `id` | 질문 식별자 |
| `query` | 사용자 질문 |
| `intent` | 개념, 비교, 근거 등 질문 의도 |
| `reference_answer` | RAGAS 기준 답변 |
| `expected_keywords` | 검색 결과 점검용 핵심어 |
| `expected_era` | 기대 시대 |
| `expected_source_type` | 기대 자료 유형 |

`reference_answer`는 Context Recall과 Answer Relevance의 기준이다. 실제 원문
청크를 사람이 검증해 연결할 수 있을 때만 `reference_contexts`를 추가한다.
자동 생성된 검색 결과를 기준 근거로 재사용하지 않는다.

RAGAS 대상은 이미지 및 학습 팁을 제외한 `concept`, `summary`, `compare`, `evidence`다.
질문 유형별 편향을 막기 위해 각 유형을 최대 15건씩 라운드로빈 선택한다. 현재 최종
평가 표본은 네 유형 각 15건, 총 60건이다.

## 측정 흐름

1. 골든 질문을 읽는다.
2. 캐시를 비운 뒤 실제 `build_history_rag_answer()`를 한 번 호출한다.
3. 그 호출 안에서 LLM 생성 메서드의 시간을 직접 계측한다. 검색 속도는 전체 응답 시간에서 이 LLM 생성 시간을 뺀 값이다.
4. 검색 문맥과 답변을 저장한 뒤 RAGAS 평가를 수행한다.
5. 요약 지표와 질문별 상세 결과를 CSV/JSON으로 보관한다.

## 평가 지표

| 평가 항목 | 정의 | 기준 |
|---|---|---|
| 연결성(Graph) | Graph 컨텍스트 탐색 성공률 | 95% 이상 |
| 검색 속도 | LLM 생성 제외 검색 평균 시간 | 2.0초 이내 |
| LLM 답변 생성 속도 | 전체 응답 시간 - 검색 시간 | 5.0초 이내 |
| 전체 응답 속도 | LLM 생성 포함 실제 서비스 평균 시간 | 7.0초 이내 |
| Context Precision | 검색 문맥 중 질문과 관련 있는 문맥 비율 | 0.80 이상 |
| Context Recall | 기준 답변에 필요한 근거의 검색 포함 여부 | 0.80 이상 |
| Faithfulness | 답변이 검색 문맥에 근거하는 정도 | 0.80 이상 |
| Answer Relevance | 답변이 질문 의도에 직접 답하는 정도 | 0.80 이상 |

## 실행

빠른 변경 확인은 12개로 실행한다. 네 유형이 각 3건씩 포함된다.

```powershell
.\.venv\Scripts\python.exe test\HS\evaluate_service_metrics.py --ragas --ragas-limit 12
```

최종 보고용 평가는 60개로 실행한다. 네 유형이 각 15건씩 포함된다.

```powershell
.\.venv\Scripts\python.exe test\HS\evaluate_service_metrics.py --ragas --ragas-limit 60
```

RAGAS는 질문별로 여러 LLM 판정을 수행하므로 60개 평가는 오래 걸리고 API 비용이 발생한다.

## 결과 보관

| 파일 | 내용 |
|---|---|
| `service_eval_results.json` | 최신 요약 지표 |
| `service_eval_results.csv` | 최신 요약 지표 CSV |
| `service_eval_results_history.csv` | 실행별 요약 지표 누적 이력 |
| `service_eval_results_ragas_samples.csv` | 최신 질문별 답변, 검색 문맥, 4개 RAGAS 점수 |
| `service_eval_results_ragas_samples_history.csv` | 질문별 상세 결과 누적 이력 |

모든 파일은 `etl/preprocessing/history/embedding/`에 생성된다.

## 해석 순서

1. Context Precision이 낮으면 후보 문맥의 잡음, 필터, 리랭커를 확인한다.
2. Context Recall이 낮으면 청킹, 검색어 정규화, 후보군 크기, 기준 근거를 확인한다.
3. Faithfulness가 낮으면 LLM 프롬프트와 근거 부족 응답 처리를 확인한다.
4. Answer Relevance가 낮으면 질문 의도 분류, 후속 질문 문맥, 답변 형식을 확인한다.
5. 속도는 검색 속도와 LLM 생성 속도를 분리해 병목을 판단한다.

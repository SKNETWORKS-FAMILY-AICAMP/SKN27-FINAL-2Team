# RAG 운영 기준 검증 가이드

## 목적

운영 중인 챗봇 RAG 품질은 실제 런타임 검색기 기준으로 검증한다.

현재 챗봇 API는 다음 흐름을 사용한다.

```text
chatbot/views.py
→ rag_service.build_history_rag_answer()
→ PgVectorHybridRetriever
→ LLMAnswerGenerator
```

따라서 운영 기준 검증 대상은 `HybridRagRetriever`가 아니라 `PgVectorHybridRetriever`이다.

## 기존 평가와 차이

기존 골든질문세트 평가는 아래 스크립트로 실행한다.

```text
etl/preprocessing/history/embedding/evaluate_golden_questions.py
```

이 평가는 JSONL 기반 프로토타입 검색기인 `HybridRagRetriever`를 사용한다.

```text
평가 대상: app/chatbot/rag/rag_prototype/retriever.py
검색 데이터: processed JSONL
평가 목적: 오프라인 검색 품질 확인
```

운영 기준 평가는 실제 챗봇 검색기와 DB를 사용해야 한다.

```text
평가 대상: app/chatbot/rag/pgvector_retriever.py
검색 데이터: PostgreSQL rag.document_chunks
평가 목적: 운영 챗봇 검색 품질 확인
```

## 운영 검증 전제 조건

운영 기준 검증 전에는 아래 상태가 준비되어 있어야 한다.

```text
PostgreSQL 컨테이너 실행
rag.document_chunks 적재 완료
embedding 컬럼 생성 완료
pgvector 확장 활성화
OPENAI_API_KEY 설정
EMBEDDING_MODEL 설정
EMBEDDING_DIMENSIONS 설정
```

`PgVectorHybridRetriever`는 질문 임베딩을 생성한 뒤 DB의 `rag.document_chunks`에서 검색한다.

## 검증 데이터

골든질문세트는 기존 파일을 재사용할 수 있다.

```text
etl/preprocessing/history/embedding/golden_questions.jsonl
```

각 질문은 다음 기준값을 가진다.

```text
query                 사용자 질문
expected_keywords     기대 키워드
expected_era          기대 시대
expected_source_type  기대 자료 유형
requires_image        이미지 URL 필요 여부
```

## 검색 품질 평가 기준

운영 검색 결과 top-k에 대해 아래 항목을 평가한다.

```text
keyword_hit       기대 키워드가 검색 결과 제목/본문/metadata에 포함되는가
source_type_hit   기대 source_type이 검색 결과에 포함되는가
era_hit           기대 시대가 검색 결과 metadata 또는 텍스트에 포함되는가
image_hit         이미지 질문에서 thumbnail_url 또는 original_image_url이 존재하는가
```

최종 통과 조건은 다음과 같다.

```text
passed = keyword_hit and source_type_hit and era_hit and image_hit
```

## 운영 검색 지표

운영 검색 검증에서는 최소 아래 지표를 남긴다.

```text
recall@k
MRR
passed count
failed ids
top result title
top result source_type
matched keywords
```

`recall@k`는 전체 골든 질문 중 top-k 검색 결과 안에서 통과한 비율이다.

`MRR`은 맞는 근거가 몇 번째 순위에 등장했는지 보는 지표이다.

```text
1위에 등장: 1.0
2위에 등장: 0.5
5위에 등장: 0.2
없음: 0
```

## 답변 품질 평가 기준

검색 품질과 답변 품질은 분리해서 본다.

운영 기준에서 LLM 답변까지 검증하려면 골든질문세트에 `reference_answer`를 추가해야 한다.

```json
{
  "id": "GQ001",
  "query": "조선 전기 6조 직계제 설명해줘",
  "reference_answer": "6조 직계제는 왕이 의정부를 거치지 않고 6조를 직접 지휘한 제도이다. 태종 때 왕권 강화를 위해 실시되었다."
}
```

답변 품질은 다음 항목으로 평가한다.

```text
faithfulness       검색 근거에 없는 내용을 만들지 않았는가
answer_relevancy   질문에 맞는 답변인가
answer_correctness 기대 답안과 의미가 맞는가
style_consistency  한국사 튜터 문체와 맞는가
source_grounding   답변 내용이 제시된 출처와 연결되는가
```

RAGAS를 사용할 경우 `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`, `answer_correctness`를 사용할 수 있다.

단, 현재 프로젝트에 구현된 골든질문세트 평가는 RAGAS 기반이 아니다.

## 권장 검증 단계

1차 검증: 운영 검색기 검증

```text
PgVectorHybridRetriever
→ golden_questions.jsonl
→ recall@k / MRR / 실패 질문 확인
```

2차 검증: 챗봇 API 응답 검증

```text
rag_chat_api
→ 실제 답변 생성
→ 답변 포함 출처와 not_found 여부 확인
```

3차 검증: LLM 답변 품질 검증

```text
reference_answer 추가
→ RAGAS 또는 자체 LLM judge
→ faithfulness / correctness 평가
```

## 발표용 정리

```text
기존 평가는 골든 질문 세트 기반으로 오프라인 JSONL 검색기의 recall@k와 MRR을 측정했다.
운영 품질 검증은 실제 챗봇에서 사용하는 PostgreSQL pgvector 기반 PgVectorHybridRetriever를 대상으로 수행해야 한다.
검색 품질은 기대 키워드, 시대, 자료 유형, 이미지 URL 여부로 검증하고,
답변 품질과 환각 여부는 reference_answer를 추가한 뒤 RAGAS 또는 LLM-as-judge 방식으로 별도 평가한다.
```


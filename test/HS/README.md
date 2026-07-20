# HS RAG 테스트

운영 RAG 점검과 리랭커 실험을 역할별로 분리합니다.

```text
test/HS/
  service/              운영 RAG·단계별 지연시간 측정
  checks/               회귀 검증
  rerank/single_query/  단일 질문 후보·BGE 실험
  rerank/golden/
    dataset/            골든셋·정답 문서 생성 및 고정 입력
    candidates/         RRF 후보 수집 및 BGE 재정렬
    evaluation/         RRF/BGE 평가와 시각화 결과
    service/            골든셋 실행 중 생성된 서비스 평가 결과
  docs/                 인수인계 문서
```

## 운영 점검

```powershell
.\.venv\Scripts\python.exe test\HS\service\measure_rag_stage_latency.py "세종대왕 업적 알려줘"
.\.venv\Scripts\python.exe test\HS\service\evaluate_service_metrics.py --ragas --ragas-limit 20
.\.venv\Scripts\python.exe test\HS\checks\test_generic_overview_rerank.py
```

## 골든셋 리랭커 평가

```powershell
.\.venv\Scripts\python.exe test\HS\rerank\golden\candidates\collect_golden_rrf_candidates.py
.\.venv\Scripts\python.exe test\HS\rerank\golden\candidates\rerank_saved_golden_candidates.py
.\.venv\Scripts\python.exe test\HS\rerank\golden\evaluation\evaluate_saved_golden_rerank_candidates.py
```

`rerank/golden/candidates`의 CSV는 DB 검색 결과와 BGE 점수를 보관합니다. Colab에서는 이 CSV만 올려 reranker 추론 시간을 측정할 수 있습니다.

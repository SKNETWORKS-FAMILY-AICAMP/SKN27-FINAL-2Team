# HS RAG 프로토타입

이 폴더는 챗봇 RAG를 실제 앱에 붙이기 전에 검증하는 개인 작업 공간입니다.

현재 목표:

```text
개념 질문
  ↓
하이브리드 RAG 검색
  ↓
첫 질문: 교재 요약 노트형 답변
이후 질문: 설명형 답변
```

## 실행

DB 인프라를 먼저 올릴 때는 프로젝트 루트에서 실행합니다.

```powershell
docker compose -f storage\docker-compose.yml up -d
```

```powershell
.\.venv\Scripts\python.exe test\HS\run_concept_chat.py "조선 전기 정치 정리해줘"
```

이후 대화처럼 설명형 답변을 확인하려면:

```powershell
.\.venv\Scripts\python.exe test\HS\run_concept_chat.py "6조 직계제가 뭐야?" --follow-up
```

## 임베딩 적재

임베딩 ETL 파일은 `etl/history/embedding` 아래로 이동했습니다.

처음 테스트:

```powershell
.\.venv\Scripts\python.exe etl\history\embedding\embed_chunks_to_pgvector.py --limit 10
```

전체 임베딩:

```powershell
.\.venv\Scripts\python.exe etl\history\embedding\embed_chunks_to_pgvector.py --limit 40000 --batch-size 10 --sleep 2 --create-index
```

OpenAI rate limit이 발생하면 스크립트가 자동으로 기다린 뒤 배치 크기를 줄여 이어서 실행합니다. 중간에 중단되어도 다시 실행하면 `embedding IS NULL`인 chunk부터 이어서 처리합니다.

## 검색 품질평가

골든 질문 세트로 JSONL 기반 retriever의 검색 품질을 평가합니다.

```powershell
.\.venv\Scripts\python.exe etl\history\embedding\evaluate_golden_questions.py --top-k 5
```

결과 파일:

```text
etl/history/embedding/eval_results.csv
etl/history/embedding/eval_results.json
```

빠르게 일부만 볼 때:

```powershell
.\.venv\Scripts\python.exe etl\history\embedding\evaluate_golden_questions.py --top-k 5 --limit 10
```

이미지/사진 조회 질문은 `image_material`만 후보로 사용하고, 질문 핵심어 또는 동의어가 이미지 제목에 들어간 자료만 반환합니다.

## PostgreSQL RAG 실험

PostgreSQL `rag.document_chunks`의 pgvector 임베딩을 직접 조회합니다.

```powershell
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "팔만대장경 만든 이유 알려줘"
```

기본 실행은 `.env`의 `CHAT_LLM_PROVIDER`를 사용해 RAG 검색 결과를 LLM 답변으로 생성합니다.

```powershell
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "조선 전기 정치 정리해줘"
```

OpenAI API로 LLM 답변 생성:

```powershell
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "조선 전기 정치 정리해줘" --llm openai --llm-model gpt-4.1-mini
```

프론트에서 교재형 UI로 직접 렌더링할 구조화 JSON 생성:

```powershell
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "조선 전기 정치 정리해줘" --answer-format structured
```

규칙 기반 포맷터와 비교:

```powershell
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "조선 전기 정치 정리해줘" --llm none
```

Ollama 로컬 모델로 LLM 답변 생성:

```powershell
ollama pull gemma4:2b
ollama serve
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "조선 전기 정치 정리해줘" --llm ollama --llm-model gemma4:2b
```

후속 질문처럼 설명형으로 생성:

```powershell
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "6조 직계제가 뭐야?" --follow-up --llm openai
```

검색 결과만 볼 때:

```powershell
.\.venv\Scripts\python.exe test\HS\run_pgvector_rag.py "고인돌 사진 보여줘" --raw
```

## 경로 변경

전처리 결과 폴더를 바꾸고 싶으면 `--processed-dir` 옵션을 사용합니다.

```powershell
.\.venv\Scripts\python.exe test\HS\run_concept_chat.py "전시과 설명해줘" --processed-dir storage\postgre\processed
```

운영 RAG 모듈은 `app/chatbot/rag`에 있고, 이 폴더에는 실행 확인용 스크립트만 남깁니다. 검색 대상 경로는 `app/chatbot/rag/rag_prototype/config.py`의 `RagPaths`에서 관리합니다.

## 구성

```text
test/HS/
  run_concept_chat.py
  run_pgvector_rag.py

app/chatbot/rag/
  llm_answer_generator.py
  pgvector_retriever.py
  rag_prototype/
    config.py
    retriever.py
    concept_chat.py
    prompts.py
```

## 다음 단계

```text
1. PostgreSQL documents/document_chunks 테이블 적재
2. pgvector 임베딩 검색 연결
3. LLM 답변 생성기 연결
4. 문제풀이 기록 기반 context 추가
5. app/chatbot API 연동 상태 유지
```

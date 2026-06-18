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

## 경로 변경

전처리 결과 폴더를 바꾸고 싶으면 `--processed-dir` 옵션을 사용합니다.

```powershell
.\.venv\Scripts\python.exe test\HS\run_concept_chat.py "전시과 설명해줘" --processed-dir storage\postgre\processed
```

`rag_prototype/config.py`의 `RagPaths`만 바꾸면 나중에 `ai/rag` 또는 `app/chatbot`으로 옮겨도 검색 대상 경로를 쉽게 교체할 수 있습니다.

## 구성

```text
test/HS/
  run_concept_chat.py
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
5. app/chatbot API로 이동
```

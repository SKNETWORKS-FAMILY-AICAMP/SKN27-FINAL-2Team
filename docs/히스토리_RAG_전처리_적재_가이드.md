# 히스토리 RAG 전처리·적재 가이드

이 문서는 한국사 RAG 데이터의 CSV 정리, 전처리, PostgreSQL 적재, 임베딩 생성 절차를 현재 프로젝트 구조 기준으로 정리한 문서이다.

## 1. 데이터 위치

원천 CSV 위치:

```text
etl/raw_data/
  사료로 본 한국사/
    csv/*.csv
  신편 한국사 csv/
    *.csv
  한국사 이미지 자료/
    한국사_이미지_자료.csv
```

전처리 결과 위치:

```text
etl/preprocessing/history/processed/
  historical_sources.documents.jsonl
  historical_sources.chunks.jsonl
  new_history.documents.jsonl
  new_history.chunks.jsonl
  image_materials.documents.jsonl
  image_materials.chunks.jsonl
```

DB 적재 테이블:

```text
rag.document_chunks
```

## 2. CSV 컬럼 정리 기준

아래 컬럼은 전처리에서 사용하지 않도록 정리되어 있으므로 CSV에서 삭제해도 된다.

### 사료로 본 한국사

삭제 가능 컬럼:

```text
순번
시대코드
분야코드
```

전처리 유지 컬럼:

```text
자료ID
제목
시대
분야
목차경로
국문
원문
해설
참고자료
상세URL
Markdown파일
```

### 신편 한국사

삭제 가능 컬럼:

```text
페이지순서
이미지설명
이미지URL
이미지파일
```

전처리 유지 컬럼:

```text
권번호
권명
페이지ID
제목
본문
각주
원본URL
```

주의: `이미지설명`을 제거하면 본문량이 줄어들어 일부 마지막 청크가 줄어들 수 있다. 현재 기준으로 신편 한국사 청크는 24,902개에서 24,864개로 줄어든다.

### 한국사 이미지 자료

삭제 가능 컬럼:

```text
작성자
이용조건
관련콘텐츠
저장이미지파일
목록요약
```

전처리 유지 컬럼:

```text
순번
이미지ID
제목
설명
시대
유형
분야
이미지출처
키워드
목록분류
썸네일URL
원본이미지URL
상세요청URL
```

주의: 이미지 자료는 현재 국가기관/국립기관 계열 출처만 남긴 상태이며, 기준 건수는 1,696건이다.

## 3. 전처리 실행

프로젝트 루트에서 실행한다.

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
```

사료로 본 한국사:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_historical_sources.py
```

신편 한국사:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_new_history.py
```

한국사 이미지 자료:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\preprocess_image_materials.py
```

전처리 스크립트의 기본 입력 경로는 `etl/raw_data`이다.

## 4. 전처리 결과 기준 건수

현재 컬럼 정리 기준으로 예상되는 결과:

```text
사료로 본 한국사 documents: 1,146
사료로 본 한국사 chunks:    7,540

신편 한국사 documents:      6,442
신편 한국사 chunks:         24,864

한국사 이미지 자료 documents: 1,696
한국사 이미지 자료 chunks:    1,696
```

전체 기준:

```text
documents: 9,284
chunks:    34,100
```

## 5. Docker DB 실행

기존 컨테이너가 있으면 새로 만들지 말고 먼저 실행한다.

```powershell
docker start skn27-postgres
```

상태 확인:

```powershell
docker ps -a --filter "name=skn27-postgres"
```

컨테이너 이름 충돌이 날 때:

```text
Conflict. The container name "/skn27-postgres" is already in use
```

위 메시지는 같은 이름의 컨테이너가 이미 있다는 뜻이다. 보통은 아래 명령으로 기존 컨테이너를 켜면 된다.

```powershell
docker start skn27-postgres
docker ps
```

정말 새 컨테이너가 필요할 때만 삭제 후 재실행한다.

```powershell
docker rm skn27-postgres
docker-compose up -d
```

주의: `docker compose down -v` 또는 DB 데이터 폴더 삭제는 PostgreSQL 데이터를 지울 수 있다.

## 6. DB에 JSONL 재적재

임베딩을 새로 만들지 않고 JSONL 내용만 DB에 반영하려면 `--limit 0`을 사용한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --delete-missing --limit 0
```

옵션 의미:

```text
--delete-missing : JSONL에서 빠진 chunk_id를 DB에서도 삭제
--limit 0        : 새 임베딩 생성 없이 JSONL upsert만 수행
```

컬럼 삭제나 출처 필터링으로 청크가 줄어든 경우에는 반드시 `--delete-missing`을 붙인다.

## 7. 특정 데이터만 재적재

사료로 본 한국사만:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --chunk-file historical_sources.chunks.jsonl --delete-missing --limit 0
```

신편 한국사만:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --chunk-file new_history.chunks.jsonl --delete-missing --limit 0
```

한국사 이미지 자료만:

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --chunk-file image_materials.chunks.jsonl --delete-missing --limit 0
```

## 8. 임베딩 생성

임베딩이 없는 청크를 새로 임베딩하려면 `--limit`을 충분히 크게 지정한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --delete-missing --limit 40000 --batch-size 20 --sleep 2 --create-index
```

중간에 끊기면 같은 명령을 다시 실행하면 된다. 스크립트는 `embedding IS NULL`이거나 임베딩 모델이 다른 청크부터 이어서 처리한다.

## 9. 적재 결과 확인 SQL

전체 청크 수:

```sql
SELECT COUNT(*)
FROM rag.document_chunks;
```

출처별 청크 수:

```sql
SELECT source_name, source_type, COUNT(*) AS chunk_count
FROM rag.document_chunks
GROUP BY source_name, source_type
ORDER BY source_name;
```

이미지 자료 청크 수:

```sql
SELECT COUNT(*)
FROM rag.document_chunks
WHERE source_type = 'image_material';
```

임베딩 완료 수:

```sql
SELECT COUNT(*)
FROM rag.document_chunks
WHERE embedding IS NOT NULL;
```

임베딩 미완료 수:

```sql
SELECT COUNT(*)
FROM rag.document_chunks
WHERE embedding IS NULL;
```

장영실 검색 예시:

```sql
SELECT source_name, document_id, title, LEFT(chunk_text, 200) AS snippet
FROM rag.document_chunks
WHERE chunk_text ILIKE '%장영실%'
   OR title ILIKE '%장영실%'
   OR metadata::text ILIKE '%장영실%'
ORDER BY source_name, title
LIMIT 20;
```

## 10. 벡터 인덱스 확인

```sql
SELECT indexname
FROM pg_indexes
WHERE schemaname = 'rag'
  AND tablename = 'document_chunks';
```

아래 인덱스가 있으면 벡터 검색 인덱스가 생성된 것이다.

```text
document_chunks_embedding_cosine_idx
```

인덱스가 없다면 아래 SQL을 실행한다.

```sql
CREATE INDEX IF NOT EXISTS document_chunks_embedding_cosine_idx
ON rag.document_chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100)
WHERE embedding IS NOT NULL;

ANALYZE rag.document_chunks;
```

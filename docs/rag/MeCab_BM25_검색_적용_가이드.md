# MeCab BM25 검색 적용 가이드

한국사 챗봇의 키워드 검색은 PostgreSQL Full Text Search와 MeCab 명사 토큰을 사용한다.

- 벡터 검색: 사용자의 원문 질문으로 의미 유사도 검색
- BM25 검색: MeCab 명사 토큰으로 정확한 역사 용어 검색
- 검색 결과: 벡터, trigram, BM25 후보를 RRF로 병합한 뒤 reranker가 최종 정렬

임베딩 벡터를 다시 생성할 필요는 없다. 기존 `rag.document_chunks`의 `search_tokens`와 `search_vector`만 갱신한다.

## 1. 사전 조건

프로젝트 루트에서 실행한다.

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
```

`.env`의 PostgreSQL 접속 정보가 실행 중인 DB와 같아야 한다.

```env
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=history_rag
POSTGRES_USER=himate
POSTGRES_PASSWORD=your_password
```

PostgreSQL 컨테이너가 실행 중인지 확인한다.

```powershell
docker compose --env-file .env -f storage\postgresql\docker-compose.yml ps
```

## 2. 라이브러리 설치와 확인

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "from mecab_ko import Tagger; print(Tagger().parse('세종대왕이 훈민정음을 창제했다'))"
```

`세종`, `대왕`, `훈민정음` 등의 분석 결과가 출력되면 정상이다.

## 3. 기존 DB에 한 번에 적용

챗봇 서버를 중지한 뒤 아래 명령 하나를 실행한다. 약 3만 건 기준으로 토큰 갱신과 generated column 재생성에 수 분이 걸릴 수 있다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --setup-mecab-bm25
```

이 명령은 다음을 순서대로 수행한다.

1. `title`을 두 번 반영한 `title + chunk_text`에서 MeCab 명사 토큰을 생성해 `search_tokens`에 저장
2. 기존 `search_vector`와 `document_chunks_search_vector_idx`를 교체
3. `to_tsvector('simple', search_tokens)` generated column 생성
4. `search_vector` GIN 인덱스 생성 및 `ANALYZE` 실행

완료 시 아래와 비슷한 로그가 출력된다.

```text
refreshed_search_tokens=...
mecab_bm25_setup=done refreshed_search_tokens=...
```

이 과정은 `embedding`, `embedding_model`, HNSW 벡터 인덱스를 변경하지 않는다.

## 4. 신규 적재 DB

DB가 비어 있는 상태에서 청크를 처음 적재하면 `embed_chunks_to_pgvector.py`가 `search_tokens`, `search_vector`, GIN 인덱스를 자동 생성한다.

```powershell
.\.venv\Scripts\python.exe etl\preprocessing\history\embedding\embed_chunks_to_pgvector.py --limit 50000 --create-index
```

청크를 이미 적재한 뒤에 MeCab 검색 구조만 추가하거나 변경할 때만 `--setup-mecab-bm25`를 실행한다.

## 5. DBeaver 확인

### MeCab 토큰 저장 여부

```sql
SELECT
  COUNT(*) AS total_count,
  COUNT(*) FILTER (WHERE search_tokens <> '') AS tokenized_count
FROM rag.document_chunks;
```

`total_count`와 `tokenized_count`가 같으면 정상이다. 이미지 자료도 제목과 설명에서 토큰이 생성될 수 있지만, 이미지 벡터 임베딩은 여전히 `NULL`이다.

### BM25 GIN 인덱스 확인

```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'rag'
  AND tablename = 'document_chunks'
  AND indexname = 'document_chunks_search_vector_idx';
```

### BM25 검색과 인덱스 사용 확인

```sql
EXPLAIN ANALYZE
SELECT chunk_id, title,
       ts_rank_cd(search_vector, plainto_tsquery('simple', '세종 대왕 업적')) AS bm25_score
FROM rag.document_chunks
WHERE search_vector @@ plainto_tsquery('simple', '세종 대왕 업적')
ORDER BY bm25_score DESC
LIMIT 10;
```

실행 계획에 `Bitmap Index Scan on document_chunks_search_vector_idx`가 보이면 GIN 인덱스를 사용 중이다.

## 6. 운영 주의사항

- `--setup-mecab-bm25`는 `ALTER TABLE`을 수행하므로 서비스 트래픽이 없는 시간에 실행한다.
- 중간에 실패하면 트랜잭션이 롤백되어 기존 `search_vector`와 인덱스가 유지된다.
- OpenAI API 호출과 임베딩 비용은 발생하지 않는다.
- 챗봇은 `RAG_BM25_ENABLED=true`일 때 자동으로 MeCab BM25 검색을 사용한다.

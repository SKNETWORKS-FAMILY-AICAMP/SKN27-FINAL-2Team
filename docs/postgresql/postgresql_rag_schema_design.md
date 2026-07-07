# PostgreSQL RAG 스키마 설계 문서

## 1. 목적

이 문서는 한국사 챗봇 RAG에서 PostgreSQL과 pgvector가 맡는 역할을 정리한다.

PostgreSQL은 다음 데이터를 저장한다.

- 전처리된 한국사 문서 청크
- 청크별 메타데이터
- 텍스트 검색용 원문
- 벡터 검색용 임베딩
- 연표 데이터

Neo4j가 인물·사건 관계 탐색을 담당한다면, PostgreSQL은 실제 답변 근거가 되는 문서 검색을 담당한다.

---

## 2. 핵심 테이블

### 2.1 `rag.document_chunks`

`rag.document_chunks`는 챗봇 RAG 검색의 중심 테이블이다.

| 컬럼 | 타입 | 의미 |
|---|---|---|
| `id` | BIGSERIAL | 내부 PK |
| `chunk_id` | TEXT | 청크 고유 ID |
| `document_id` | TEXT | 원문 문서 ID |
| `source_type` | TEXT | 자료 유형 |
| `source_name` | TEXT | 자료명 |
| `title` | TEXT | 문서 제목 |
| `chunk_index` | INTEGER | 문서 안의 청크 순서 |
| `chunk_text` | TEXT | 검색 및 답변 근거 텍스트 |
| `token_count` | INTEGER | 대략 토큰 수 |
| `metadata` | JSONB | 시대, 분류, URL, 이미지 정보 등 |
| `embedding` | VECTOR(1536) | OpenAI embedding |
| `embedding_model` | TEXT | 임베딩 모델명 |
| `embedded_at` | TIMESTAMPTZ | 임베딩 생성 시각 |

현재 임베딩 모델은 `text-embedding-3-small` 기준이며 차원 수는 1,536이다.

이미지 자료는 URL과 메타데이터 기반 조회를 사용하므로 `embedding`은 넣지 않는다.

### 2.2 `rag.history_timeline`

`rag.history_timeline`은 한국사 연표 검색용 테이블이다.

| 컬럼 | 의미 |
|---|---|
| 연도 | 사건 발생 연도 |
| 내용 | 연표 항목 내용 |
| 시대/분류 | 연표 필터링 보조 정보 |

연표는 벡터 검색보다 연도·시대 조건 검색에 가깝기 때문에 별도 테이블로 둔다.

---

## 3. 인덱스 설계

| 인덱스 | 방식 | 목적 |
|---|---|---|
| `document_chunks_pkey` | B-tree | 내부 PK 조회 |
| `document_chunks_chunk_id_key` | UNIQUE | 청크 중복 방지 |
| `document_chunks_source_type_idx` | B-tree | 자료 유형 필터 |
| `document_chunks_metadata_gin_idx` | GIN JSONB | 메타데이터 필터 |
| `document_chunks_text_trgm_idx` | GIN trigram | 키워드 검색 |
| `document_chunks_title_trgm_idx` | GIN trigram | 제목 키워드 검색 |
| `document_chunks_embedding_cosine_idx` | HNSW | 벡터 유사도 검색 |

HNSW 인덱스는 다음 형태다.

```sql
CREATE INDEX document_chunks_embedding_cosine_idx
ON rag.document_chunks
USING hnsw (embedding vector_cosine_ops)
WHERE embedding IS NOT NULL;
```

이미지 자료는 `embedding IS NULL`이므로 HNSW 대상에서 제외된다.

제목 검색도 `title ILIKE`, `title % query` 조건을 사용하므로 `title`에도 trigram GIN 인덱스를 둔다.

```sql
CREATE INDEX IF NOT EXISTS document_chunks_title_trgm_idx
ON rag.document_chunks
USING GIN (title gin_trgm_ops);
```

---

## 4. 메타데이터 설계

`metadata`는 JSONB로 저장한다. 자료마다 원본 구조가 다르기 때문에 공통 필드와 자료별 필드를 함께 둔다.

공통 기준:

| 필드 | 의미 |
|---|---|
| `period` | 대표 시대 |
| `periods` | 관련 시대 목록 |
| `category_path` | 계층형 분류 경로 |
| `category_tags` | 검색 보조 태그 |
| `chronology` | 시대 순서, 대표 연도, 언급 연도 |
| `source_url` | 원문 또는 상세 페이지 URL |

이미지 자료 전용:

| 필드 | 의미 |
|---|---|
| `image.source` | 이미지 출처명 |
| `thumbnail_url` | 썸네일 URL |
| `original_image_url` | 원본 이미지 URL |

목차 번호 `(1)`, `1)`, `가.`, `Ⅰ.` 등은 전처리에서 제거한다.

---

## 5. 검색 구조

검색은 하이브리드 방식이다.

```text
사용자 질문
  -> 키워드/시대/이미지 의도 판단
  -> PostgreSQL pgvector + trigram 검색
  -> 필요 시 Neo4j 관계 키워드 보강
  -> LLM 답변 생성
```

이미지 요청은 임베딩을 생략하고 이미지 제목/메타데이터 기반으로 조회한다.

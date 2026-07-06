# PostgreSQL 전처리 폴더와 파일 역할 정리

이 문서는 PostgreSQL RAG에 필요한 전처리 파일, 산출물, 적재 스크립트의 역할을 정리한다.

---

## 1. 전체 실행 흐름

```text
etl/raw_data
  -> etl/preprocessing/history/*.py
  -> etl/preprocessing/history/processed/*.jsonl
  -> etl/preprocessing/history/embedding/embed_chunks_to_pgvector.py
  -> PostgreSQL rag.document_chunks
```

---

## 2. 전처리 스크립트

| 파일 | 역할 |
|---|---|
| `preprocess_historical_sources.py` | 사료로 본 한국사 전처리 |
| `preprocess_new_history.py` | 신편 한국사 전처리 |
| `preprocess_image_materials.py` | 한국사 이미지 자료 전처리 |
| `preprocess_history_timeline.py` | 한국사 연표 CSV 정규화 |
| `load_history_timeline_to_postgres.py` | 연표 데이터 PostgreSQL 적재 |
| `rag_metadata.py` | 공통 메타데이터, 시대, 제목 정규화 함수 |
| `embedding/embed_chunks_to_pgvector.py` | 청크 upsert, 임베딩, 인덱스 생성 |
| `embedding/evaluate_golden_questions.py` | golden question 기반 검색 품질 평가 |

---

## 3. 산출물

| 파일 | 의미 |
|---|---|
| `processed/historical_sources.documents.jsonl` | 사료 문서 단위 산출물 |
| `processed/historical_sources.chunks.jsonl` | 사료 청크 산출물 |
| `processed/new_history.documents.jsonl` | 신편 한국사 문서 단위 산출물 |
| `processed/new_history.chunks.jsonl` | 신편 한국사 청크 산출물 |
| `processed/image_materials.documents.jsonl` | 이미지 자료 문서 단위 산출물 |
| `processed/image_materials.chunks.jsonl` | 이미지 자료 조회용 메타데이터 청크 |
| `processed/history_timeline_processed.csv` | 연표 적재용 CSV |

---

## 4. PostgreSQL 관련 파일

| 파일 | 역할 |
|---|---|
| `storage/postgresql/docker-compose.yml` | PostgreSQL + pgvector 컨테이너 실행 |
| `storage/postgresql/schema/init.sql` | 최초 DB 생성 시 실행되는 스키마 |
| `storage/postgresql/schema/alter_apply_latest.sql` | 기존 DB에 컬럼 변경을 반영하는 스크립트 |
| `storage/postgresql/README.md` | PostgreSQL 실행 메모 |

---

## 5. DB 적재 대상

| 대상 | 입력 파일 | 적재 방식 |
|---|---|---|
| `rag.document_chunks` | `*.chunks.jsonl` | `embed_chunks_to_pgvector.py`에서 upsert |
| `rag.history_timeline` | `history_timeline_processed.csv` | `load_history_timeline_to_postgres.py` |

---

## 6. 자주 보는 확인 쿼리

```sql
SELECT source_type, source_name, COUNT(*)
FROM rag.document_chunks
GROUP BY source_type, source_name
ORDER BY source_type, source_name;
```

```sql
SELECT COUNT(*) AS embedded
FROM rag.document_chunks
WHERE embedding IS NOT NULL;
```

```sql
SELECT COUNT(*) AS image_chunks
FROM rag.document_chunks
WHERE source_type = 'image_material';
```


# PostgreSQL RAG 전처리 EDA 메모

## 1. 전처리 기준

PostgreSQL RAG용 전처리는 답변 근거 검색에 필요한 텍스트 품질을 맞추는 데 집중한다.

주요 기준:

- 목차 번호 제거
- 제목과 상위 경로 정규화
- 시대 정보 `period`, `periods` 공통화
- 계층 정보 `category_path` 공통화
- 참고문헌 영역 제거
- 중복 청크 제거
- 이미지 파일 경로 제거, URL만 보존

---

## 2. 자료별 처리

| 자료 | 처리 방식 |
|---|---|
| 사료로 본 한국사 | CSV와 markdown item을 읽어 국문/원문/해설을 문서화하고 청킹 |
| 신편 한국사 | CSV 본문과 목차 구조를 문서화하고 청킹 |
| 한국사 이미지 자료 | 제목, 설명, 시대, 이미지 URL을 문서화하되 임베딩 제외 |
| 한국사 연표 | 연도와 내용을 CSV로 정규화 후 별도 테이블 적재 |

---

## 3. 제거/정규화 규칙

목차 번호 예:

```text
(1) 세종의 정치
1) 관료제의 특징
가. 배경
Ⅰ. 토지제도
```

전처리 후:

```text
세종의 정치
관료제의 특징
배경
토지제도
```

참고문헌은 본문 검색 품질을 흐리므로 제거한다. 단, `출처:` 문자열은 사용자가 요청한 기준에 따라 제거 대상에서 제외한다.

---

## 4. 현재 산출물

| 산출 파일 | 행 수 | 의미 |
|---|---:|---|
| `historical_sources.documents.jsonl` | 1,146 | 사료 문서 |
| `historical_sources.chunks.jsonl` | 6,237 | 사료 청크 |
| `new_history.documents.jsonl` | 6,442 | 신편 한국사 문서 |
| `new_history.chunks.jsonl` | 24,864 | 신편 한국사 청크 |
| `image_materials.documents.jsonl` | 1,417 | 이미지 문서 |
| `image_materials.chunks.jsonl` | 1,417 | 이미지 메타데이터 청크 |
| `history_timeline_processed.csv` | 1,162 | 연표 데이터 |

---

## 5. 검수 포인트

| 확인 대상 | 봐야 할 것 |
|---|---|
| `title` | 번호가 남아 있지 않은지 |
| `metadata.category_path` | 리스트 형태로 계층이 유지되는지 |
| `metadata.periods` | 리스트 형태인지 |
| `metadata.chronology.mentioned_years` | 참고문헌 연도가 섞이지 않았는지 |
| `source_type=image_material` | 이미지 URL이 있고 로컬 이미지 경로가 없는지 |


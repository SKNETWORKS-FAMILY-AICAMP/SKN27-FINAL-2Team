# Neo4j 전처리 폴더와 파일 역할 정리

이 문서는 `etl/preprocessing/neo4j` 아래 폴더, CSV 산출물, Python 스크립트의 의미를 정리한다.

핵심 기준은 다음과 같다.

- `normalized/`, `dictionary/`, `mapping/`, `staging/`은 전처리 중간 산출물이다.
- runner 기준 Neo4j import 최종 대상은 `storage/neo4j/neo4j_import/nodes/`, `storage/neo4j/neo4j_import/relations/`이다.
- `seed/`는 사람이 관리하는 규칙표다.
- `scripts/`는 실제 전처리 로직이다.
- `run_neo4j_preprocessing.py`는 전체 전처리를 순서대로 실행하는 시작 파일이다.

---

## 1. 전체 실행 흐름

```text
raw_data
  -> normalized
  -> dictionary
  -> mapping + staging
  -> storage/neo4j/neo4j_import/nodes + storage/neo4j/neo4j_import/relations
  -> Neo4j import
```

전체 CSV를 다시 만들 때는 다음 파일 하나만 실행한다.

```powershell
.\.venv\Scripts\python.exe etl/preprocessing/neo4j/run_neo4j_preprocessing.py
```

실행 순서는 다음과 같다.

| 순서 | 스크립트 | 역할 |
|---:|---|---|
| 1 | `scripts/normalize_raw_data.py` | raw CSV를 EDA 기준으로 정리해서 `normalized/` 생성 |
| 2 | `scripts/make_base_dictionaries.py` | 1차 사전과 날짜 parsing staging 생성 |
| 3 | `scripts/make_mapping_tables.py` | 사전 사이의 crosswalk와 기본 관계 staging 생성 |
| 4 | `scripts/make_graph_csv.py` | Neo4j import용 최종 node/relation CSV 생성 |
| 5 | `scripts/make_theme_era_csv.py` | Theme/Era/EntityType 상위 레이어 node/relation CSV 생성 |

`scripts/make_term_person_review.py`는 기본 runner에 포함하지 않는다.
Term-Person 수동 검수 후보가 필요할 때 graph CSV 생성 후 단독 실행한다.

---

## 2. 폴더 역할

| 폴더 | 성격 | 의미 |
|---|---|---|
| `etl/preprocessing/neo4j/` | 실행 진입점 | runner와 산출물 폴더를 모아둔 Neo4j 전처리 루트 |
| `scripts/` | 코드 | CSV 생성 로직이 들어 있는 Python 파일 |
| `normalized/` | 중간 입력 | raw CSV에서 중복/불필요 컬럼을 정리한 1차 정규화 데이터 |
| `seed/` | 수동 규칙 | 사람이 검수하거나 직접 정의한 매핑, 분류, 시대 순서 규칙 |
| `dictionary/` | 기준표 | 그래프 노드 후보를 정의하는 사전 CSV |
| `mapping/` | 연결 규칙 | 서로 다른 사전이나 분류 체계를 연결하는 crosswalk CSV |
| `staging/` | 관계 중간 산출물 | 최종 relation CSV를 만들기 전의 중간 관계/파싱 결과 |
| `graph/nodes/` | 수동 실행 산출물 | `make_graph_csv.py`를 단독 실행할 때의 기본 node CSV 저장 위치 |
| `graph/relations/` | 수동 실행 산출물 | `make_graph_csv.py`를 단독 실행할 때의 기본 relationship CSV 저장 위치 |
| `__pycache__/` | 실행 캐시 | Python이 자동 생성한 캐시 폴더. import 대상 아님 |

`run_neo4j_preprocessing.py`로 실행하면 graph 생성 단계는 `graph/`가 아니라 `storage/neo4j/neo4j_import/` 아래에 바로 CSV를 만든다. Neo4j import에서는 보통 `storage/neo4j/neo4j_import/nodes/`를 먼저 넣고, 그 다음 `storage/neo4j/neo4j_import/relations/`를 넣는다.

### 2.1 runner가 생성하는 CSV와 생성하지 않는 CSV

`run_neo4j_preprocessing.py`는 다음 폴더의 CSV를 생성하거나 재생성한다.

- `normalized/`
- `dictionary/`
- `mapping/`
- `staging/`
- `storage/neo4j/neo4j_import/nodes/`
- `storage/neo4j/neo4j_import/relations/`

단, `staging/term_era_candidate.csv`는 수동 검수 파일이므로 존재하면 삭제하지 않고 보존한다.

반대로 `seed/` 폴더의 CSV는 runner가 생성하지 않는다. `seed/`는 사람이 직접 관리하는 입력 규칙표이기 때문이다.

현재 runner가 생성하지 않는 CSV는 다음 seed 파일들이다.

- `seed/category_axis_seed.csv`
- `seed/country_seed.csv`
- `seed/event_facet_seed.csv`
- `seed/period_seed.csv`
- `seed/region_seed.csv`
- `seed/relation_type_seed.csv`
- `seed/taxonomy_crosswalk_seed.csv`
- `seed/theme_seed.csv`
- `seed/category_theme_seed.csv`
- `seed/era_seed.csv`
- `seed/period_era_seed.csv`
- `seed/entity_type_seed.csv`
- `seed/keyword_era_seed.csv`
- `seed/reign_seed.csv`
- `seed/term_person_review_approved.csv`

이 파일들은 자동 생성 산출물이 아니라 전처리 규칙을 담은 입력 파일이다.

### 2.2 더 이상 생성하지 않는 예전 이름 CSV

현재 구조에서는 다음 예전 이름의 CSV를 더 이상 생성하지 않는다.

| 예전 파일명 | 현재 대체 파일 |
|---|---|
| `dictionary/category_dictionary.csv` | `dictionary/canonical_category_dictionary.csv` |
| `dictionary/event_category_dictionary.csv` | `dictionary/source_event_category_dictionary.csv` |
| `dictionary/category_mapping.csv` | `mapping/taxonomy_crosswalk.csv` |
| `relations/person_has_evidence_url.csv` | 없음. `person_relations.evidence_url`은 `person_related_to_person.csv`의 관계 속성으로만 보존 |
| `staging/term_category_relation.csv` | `staging/term_canonical_category_relation.csv` |
| `staging/event_category_relation.csv` | `staging/event_source_category_relation.csv` |
| `staging/person_duplicate_review.csv` | `staging/term_person_review.csv`에서 `review_type=PERSON_DUPLICATE`로 구분 |

Person 중복 검수 후보는 별도 CSV로 분리하지 않는다.
`staging/person_duplicate_review.csv`가 과거 실행 결과로 남아 있어도 graph 생성 입력이 아니며, 공식 검수 흐름에서는 삭제해도 된다.
이를 만들던 보조 스크립트 `make_person_duplicate_review.py`도 공식 흐름에서 제거했다.
후보는 `staging/term_person_review.csv` 하나에서 보고, 확정한 Term-Person 연결만 `seed/term_person_review_approved.csv`에 기록한다.
이렇게 바꾼 이유는 검수 대상이 Person 노드 병합이 아니라 Term 설명을 어느 Person에 연결할지의 엣지 선택이기 때문이다.
이름/한자가 같다는 이유로 Person ID를 합치면 서로 다른 인물의 관계, 사건 참여, 생몰년, 출처가 한 노드에 섞일 수 있다.
반대로 Term-Person 연결은 `term_id`, `person_id` 단위로 승인하면 틀린 후보를 seed에 넣지 않는 방식으로 보수적으로 관리할 수 있다.
이 후보 파일의 `birth_year`, `death_year`는 원천 Person 데이터의 연도 문자열을 그대로 표시하며, 원천이 비어 있으면 빈 값으로 둔다.
`14??`, `?`, `1745(1730)` 같은 부분/불확실 연도도 원천값이면 그대로 둔다.
Term 설명의 재위 연도는 후보 필터링에만 쓰고 생몰년 컬럼을 채우는 데 사용하지 않는다.

---

## 3. Python 파일 역할

| 파일 | 위치 | 역할 |
|---|---|---|
| `run_neo4j_preprocessing.py` | `neo4j/` | 전체 전처리 파이프라인을 순서대로 실행하는 시작 파일 |
| `neo4j_common.py` | `neo4j/scripts/` | 공통 유틸 함수. CSV 저장, 경로 해석, 값 정리, 토큰 분해 등을 담당 |
| `normalize_raw_data.py` | `neo4j/scripts/` | raw CSV를 EDA 기준으로 정규화해서 `normalized/` 생성 |
| `make_base_dictionaries.py` | `neo4j/scripts/` | 표준 카테고리, 이벤트 카테고리, 시대, 관계유형, URL 사전 생성 |
| `make_mapping_tables.py` | `neo4j/scripts/` | 사전 사이의 매핑표와 기본 staging 관계 생성 |
| `make_graph_csv.py` | `neo4j/scripts/` | 최종 Neo4j node/relation CSV 생성 |
| `make_theme_era_csv.py` | `neo4j/scripts/` | graph CSV와 seed를 읽어 Theme/Era/EntityType 상위 레이어 CSV 생성 |
| `make_term_era_candidates.py` | `neo4j/scripts/` | 고조선/초기 국가 시대 후보 용어를 이름/설명문에서 추출해 검수 시트 생성. runner 미포함, 수동 실행 |
| `make_term_person_review.py` | `neo4j/scripts/` | 이름/한자 1차 후보 중 Term 설명에 Person 관계망 단서가 있고 시대 범위와 생몰년이 명백히 충돌하지 않는 Term-Person 후보를 수동 검수 CSV로 생성. 기본 runner에는 포함하지 않고 필요할 때 단독 실행 |

각 스크립트는 단독 실행도 가능하지만, 일반적으로는 runner만 실행한다.

---

## 4. `normalized/` CSV

`normalized/`는 raw CSV를 그대로 쓰지 않고, EDA에서 결정한 기준으로 정리한 1차 입력 데이터다. 이후 모든 사전과 그래프 CSV는 이 폴더의 CSV를 기준으로 만들어진다.

| CSV | 행 수 | 의미 |
|---|---:|---|
| `terms.csv` | 61,598 | 역사 용어 원본에서 실제 용어 행만 남긴 정규화 데이터 |
| `events.csv` | 600 | 사건 데이터에서 `event_id` 기준으로 중복을 정리한 정규화 데이터 |
| `event_relations.csv` | 6,918 | 사건과 인물의 참여 관계를 정리한 데이터 |
| `person_relations.csv` | 206,507 | 인물과 인물 사이의 관계를 정리한 데이터 |

### 4.1 `terms.csv`

역사 용어 노드와 카테고리, 시대 관계를 만들기 위한 기본 입력이다.

주요 컬럼:

- `term_id`: 용어 ID
- `term_name`: 용어명
- `term_ch`: 한자 표기
- `term_remark`: 비고
- `term_year`: 연도 원문
- `term_times`: 시대 원문
- `term_lk`: 원본 카테고리 경로
- `term_desc`: 설명문
- `topterm_id`: 원본 상위 분류 ID

### 4.2 `events.csv`

사건 노드와 사건 분류, 시대, 사건 그룹, 출처 URL 관계를 만들기 위한 기본 입력이다.

주요 컬럼:

- `event_id`: 사건 ID
- `event_name`: 사건명
- `subject_category`: 원본 사건 분류
- `period`: 사건 시대
- `event_date`: 사건 날짜 원문
- `related_event`: 사건 묶음명
- `source_urls`: 원본 상세 URL 묶음

### 4.3 `event_relations.csv`

`Person - INVOLVED_IN - Event` 관계를 만들기 위한 기본 입력이다.

주요 컬럼:

- `event_id`
- `event_name`
- `relation_type`
- `person_id`
- `person_name`
- `source_urls`

### 4.4 `person_relations.csv`

`Person - RELATED_TO - Person` 관계를 만들기 위한 기본 입력이다.

주요 컬럼:

- `person_id`
- `person_name`
- `relation_type`
- `related_person_id`
- `related_person_name`
- `related_birth_year`
- `related_death_year`
- `related_bonkwan`
- `related_father`
- `related_count`
- `evidence_url`
- `detail_url`

---

## 5. `seed/` CSV

`seed/`는 자동으로 판단하기 어려운 규칙을 사람이 관리하는 폴더다. 코드에 하드코딩하지 않고 CSV로 빼둔 기준표다.

| CSV | 의미 |
|---|---|
| `category_axis_seed.csv` | 표준 카테고리 경로에서 의미 축을 추출하는 기준 |
| `country_seed.csv` | 국가/정치체 노드 후보와 원본 카테고리 경로 연결 기준 |
| `region_seed.csv` | 지역/권역 노드 후보와 계층 기준 |
| `event_facet_seed.csv` | 원본 이벤트 분류를 사건 facet으로 재분류하는 기준 |
| `relation_type_seed.csv` | 인물 관계 원문을 표준 관계 의미로 정규화하는 기준 |
| `taxonomy_crosswalk_seed.csv` | 이벤트 원본 분류와 표준 카테고리의 수동 매핑 기준 |
| `period_seed.csv` | 시대 순서, 범위 확장, 시대 계층 기준 |
| `theme_seed.csv` | 서비스 고정 주제 10개 정의. 사건/인물/정치/제도/문화/사회/군사/경제/사상·종교/외교 |
| `category_theme_seed.csv` | 표준 카테고리와 주제의 매핑 기준 |
| `era_seed.csv` | 표준 시대(Era) 10개 정의 |
| `period_era_seed.csv` | 기존 Period 표기 변형을 표준 시대로 매핑 |
| `entity_type_seed.csv` | 실체 유형 카테고리를 유형 축으로 정의 |
| `keyword_era_seed.csv` | 시험 빈출 키워드-시대 매핑 (ml_keyword_era_overrides.json 유래) |
| `reign_seed.csv` | 왕대/연호 이름과 연도 범위. 연도 파서 보조용 seed |
| `term_person_review_approved.csv` | 사람이 승인한 Term-Person 수동 연결 목록. 검수 후보의 `review_type`과 무관하게 Term이 특정 Person을 가리킨다고 확정한 행을 기록하며 `REFERS_TO` 관계에 `MANUAL`로 반영 |

공식 graph 생성 흐름에서는 Person ID 병합 seed를 사용하지 않는다.
동명이인 중 특정 Person이 Term 대상이라고 확정한 경우만 `term_person_review_approved.csv`에 기록한다.
`review_type=PERSON_DUPLICATE`에서 설명을 붙일 Person을 결정한 경우와 `review_type=TERM_PERSON`에서 특정 `person_id` 연결을 승인한 경우 모두 같은 형식으로 쓴다.
필요한 컬럼은 `term_id`, `person_id`, `review_status`, `note`뿐이며, 선택하지 않은 후보나 판단 불가 후보는 seed에 쓰지 않는다.

`theme_seed.csv`, `era_seed.csv`, `entity_type_seed.csv`는 명시 ID 컬럼을 가진다. 이 ID는 Neo4j 노드의 primary key로 쓰이며, seed 행을 재정렬하거나 중간에 새 행을 넣어도 기존 ID가 밀리지 않게 하기 위한 안정 장치다.

### 5.1 `period_seed.csv`

`삼국시대-조선시대` 같은 범위 표현을 전처리에서 확장하기 위한 기준이다.

예:

```text
삼국시대-조선시대
  -> 삼국시대
  -> 남북국시대
  -> 후삼국시대
  -> 고려시대
  -> 조선시대
```

중간 시대는 `range_group`, `period_order`, `is_range_expansion_candidate` 기준으로 결정한다.

---

## 6. `dictionary/` CSV

`dictionary/`는 그래프 노드 후보를 정의하는 기준표다. 여기 있는 파일은 대부분 최종 node CSV로 변환된다. runner 기준 최종 node CSV 위치는 `storage/neo4j/neo4j_import/nodes/`다.

| CSV | 행 수 | 의미 |
|---|---:|---|
| `canonical_category_dictionary.csv` | 400 | `history_terms.term_lk`를 분해해 만든 표준 카테고리 사전 |
| `source_event_category_dictionary.csv` | 53 | `events.subject_category`에서 만든 원본 사건 분류 사전 |
| `period_dictionary.csv` | 30 | 시대 노드 기준 사전 |
| `relation_type_dictionary.csv` | 16 | 인물 관계 유형 정규화 사전 |
| `source_url_dictionary.csv` | 57,412 | URL 출처와 RAG 수집 대상 사전 |
| `event_facet_dictionary.csv` | 53 | 사건 분류를 의미 facet으로 정리한 사전 |
| `country_dictionary.csv` | 5 | 국가/정치체 사전 |
| `region_dictionary.csv` | 7 | 지역/권역 사전 |
| `economic_domain_dictionary.csv` | 16 | 경제·산업 하위 분야 사전 |
| `taxonomy_facet_dictionary.csv` | 49 | 표준 카테고리 중간 경로에서 뽑은 일반 taxonomy facet 사전 |

### 6.1 `canonical_category_dictionary.csv`

`term_lk`를 `>>`, `>` 기준으로 분해해서 만든 표준 카테고리 사전이다.

주요 용도:

- `CanonicalCategory` 노드 생성
- `Term - HAS_CATEGORY - CanonicalCategory` 관계 생성
- 카테고리 계층 관계 후보 생성

### 6.2 `source_event_category_dictionary.csv`

이벤트 원본 분류를 보존하기 위한 사전이다.

원본 이벤트 분류는 표준 카테고리와 바로 같지 않기 때문에, 원형을 `SourceEventCategory`로 남기고 `taxonomy_crosswalk.csv`로 표준 카테고리와 연결한다.

### 6.3 `period_dictionary.csv`

시대 노드 기준 사전이다.

주요 용도:

- `Period` 노드 생성
- `Term - IN_PERIOD - Period` 관계 생성
- `Event - IN_PERIOD - Period` 관계 생성
- 시대 범위 확장 기준 제공

### 6.4 `relation_type_dictionary.csv`

인물 관계 원문을 그래프에서 쓰기 좋은 관계 의미로 정규화하는 사전이다.

예:

- `부`
- `자`
- `형제`
- `교유`
- `문인`

이 파일은 관계 방향, 대칭 여부, inverse 관계 검토 기준을 포함한다.

### 6.5 `source_url_dictionary.csv`

URL을 중복 없이 모아둔 출처 사전이다.

주요 용도:

- `SourceUrl` 노드 생성
- `Event - HAS_SOURCE_URL - SourceUrl`
- `Person - HAS_SOURCE_URL - SourceUrl`
- Tavily/Web RAG 수집 대상 관리

`person_relations.evidence_url`은 이 사전에 넣지 않는다. 인물 관계 근거 URL은 `person_related_to_person.csv`의 `evidence_url` 속성으로만 남겨 URL 허브 노드 생성을 피한다.

### 6.6 facet 계열 사전

아래 사전들은 카테고리나 이벤트 분류에서 뽑은 의미 축이다.

- `event_facet_dictionary.csv`
- `country_dictionary.csv`
- `region_dictionary.csv`
- `economic_domain_dictionary.csv`
- `taxonomy_facet_dictionary.csv`

이들은 지금은 분리되어 있지만, 나중에 파일 수를 줄이고 싶으면 `facet_dictionary.csv` 같은 통합 사전으로 합칠 수 있다.

---

## 7. `mapping/` CSV

`mapping/`은 사전과 사전 사이를 연결하는 crosswalk 폴더다. 사전 자체가 아니라 연결 규칙이다.

| CSV | 행 수 | 의미 |
|---|---:|---|
| `taxonomy_crosswalk.csv` | 53 | 원본 사건 분류와 표준 카테고리 연결 규칙 |
| `source_event_category_facet_crosswalk.csv` | 53 | 원본 사건 분류와 사건 facet 연결 규칙 |
| `canonical_category_country_crosswalk.csv` | 41 | 표준 카테고리와 국가 연결 규칙 |
| `canonical_category_region_crosswalk.csv` | 13 | 표준 카테고리와 지역 연결 규칙 |
| `canonical_category_economic_domain_crosswalk.csv` | 51 | 표준 카테고리와 경제 분야 연결 규칙 |
| `canonical_category_taxonomy_facet_crosswalk.csv` | 276 | 표준 카테고리와 taxonomy facet 연결 규칙 |

### 7.1 `taxonomy_crosswalk.csv`

`SourceEventCategory`와 `CanonicalCategory`를 연결한다.

예:

```text
events.subject_category
  -> SourceEventCategory
  -> taxonomy_crosswalk
  -> CanonicalCategory
```

### 7.2 `canonical_category_*_crosswalk.csv`

표준 카테고리 경로에서 국가, 지역, 경제 분야, taxonomy facet을 뽑아 연결한다.

중요한 점:

- `러시아`, `미국`, `북한`은 `외교·국제관계`의 하위 카테고리로만 보지 않는다.
- 그래프에서는 `Country`, `Region` 같은 별도 의미 노드로 연결한다.
- 원본 경로는 보존하고, 의미 관계는 `ABOUT_COUNTRY`, `ABOUT_REGION`으로 분리한다.

---

## 8. `staging/` CSV

`staging/`은 최종 relation CSV를 만들기 전의 중간 산출물이다.

| CSV | 행 수 | 의미 |
|---|---:|---|
| `term_canonical_category_relation.csv` | 61,697 | 용어와 표준 카테고리 연결 중간 테이블 |
| `event_source_category_relation.csv` | 713 | 사건과 원본 이벤트 분류 연결 중간 테이블 |
| `event_date_parse.csv` | 703 | 사건 날짜 원문 parsing 결과 |
| `term_year_parse.csv` | 61,598 | 용어 연도 원문 parsing 결과. 최종 `nodes/terms.csv`에 병합 |
| `term_person_review.csv` | 206 | Term-Person 수동 검수 후보. 기본 runner 산출물이 아니며, `make_term_person_review.py --save`를 단독 실행할 때 생성된다. `review_type`으로 검수 유형을 구분하고, 승인 결과는 `seed/term_person_review_approved.csv`에 기록 |
| `term_era_candidate.csv` | (수동 생성) | 고조선/초기 국가 시대 후보 용어 검수 시트. `make_term_era_candidates.py` 수동 실행 시 생성되며 runner는 생성하지 않음(현재 미생성). HIGH 신뢰도는 `AUTO_APPROVED`, 나머지는 `PENDING`으로 사람 검수 대상. 검수 결정은 재실행 시 보존됨. `make_theme_era_csv.py`는 이 파일이 있으면 검수 통과분을 `term_in_era.csv`에 합류 |

### 8.1 `term_canonical_category_relation.csv`

`Term - HAS_CATEGORY - CanonicalCategory` 최종 관계를 만들기 위한 중간 테이블이다.

`term_lk`에 `>>`가 있으면 하나의 용어가 여러 카테고리 경로에 연결될 수 있다.

### 8.2 `event_source_category_relation.csv`

`Event - HAS_EVENT_CATEGORY - SourceEventCategory` 최종 관계를 만들기 위한 중간 테이블이다.

이벤트 하나가 여러 원본 분류를 가질 수 있으므로 `event_id`, `event_category_id` 기준 관계로 펼쳐둔다.

### 8.3 `event_date_parse.csv`

`event_date` 원문에서 연도, 월, 왕대 표현 등을 보수적으로 추출한 결과다.

이 파일은 `events.csv` 노드 속성 보강과 `Event - IN_PERIOD - Period` 관계 생성에 사용된다.

---

## 9. 최종 node CSV

최종 node CSV는 Neo4j node import 대상이다. `run_neo4j_preprocessing.py`로 실행하면 `storage/neo4j/neo4j_import/nodes/` 아래에 생성된다.

`make_graph_csv.py`를 단독 실행하면 기본값으로 `etl/preprocessing/neo4j/graph/nodes/` 아래에 생성된다.

| CSV | 행 수 | Neo4j 노드 의미 |
|---|---:|---|
| `terms.csv` | 61,598 | `Term` 노드 |
| `events.csv` | 600 | `Event` 노드 |
| `people.csv` | 56,403 | `Person` 노드 |
| `canonical_categories.csv` | 400 | `CanonicalCategory` 노드 |
| `source_event_categories.csv` | 53 | `SourceEventCategory` 노드 |
| `periods.csv` | 30 | `Period` 노드 |
| `source_urls.csv` | 57,412 | `SourceUrl` 노드 |
| `event_groups.csv` | 32 | 관련 사건 묶음 `EventGroup` 노드 |
| `event_facets.csv` | 53 | 사건 의미 facet 노드 |
| `countries.csv` | 5 | 국가/정치체 노드 |
| `regions.csv` | 7 | 지역/권역 노드 |
| `economic_domains.csv` | 16 | 경제 분야 노드 |
| `taxonomy_facets.csv` | 49 | 중간 taxonomy facet 노드 |
| `search_tags.csv` | 175,714 | Term/Event/Person 검색 편의용 통합 tag 노드. Person 별칭은 `PersonAlias` 태그로 분리 |
| `themes.csv` | 10 | 서비스 고정 주제 축 노드 |
| `eras.csv` | 10 | 표준 시대 축 노드 |
| `entity_types.csv` | 4 | 실체 유형 축 노드 (인물/문헌/문화재/장소) |

### 9.1 핵심 노드

| 노드 CSV | 핵심 ID | 설명 |
|---|---|---|
| `terms.csv` | `term_id` | 역사 용어 |
| `events.csv` | `event_id` | 사건 |
| `people.csv` | `person_id` | 인물 |
| `periods.csv` | `period_id` | 시대 |
| `canonical_categories.csv` | `category_id` | 표준 카테고리 |
| `source_urls.csv` | `source_url_id` | 출처 URL |

### 9.2 보조 의미 노드

아래 노드들은 카테고리나 사건 분류에서 의미 축을 분리한 것이다.

- `countries.csv`
- `regions.csv`
- `economic_domains.csv`
- `taxonomy_facets.csv`
- `event_facets.csv`
- `search_tags.csv`

이 노드들은 import 쿼리 수는 늘리지만, 조회 쿼리에서는 `ABOUT_COUNTRY`, `ABOUT_REGION`, `HAS_FACET`처럼 단순한 관계로 접근할 수 있게 해준다.

---

## 10. 최종 relationship CSV

최종 relationship CSV는 Neo4j relationship import 대상이다. `run_neo4j_preprocessing.py`로 실행하면 `storage/neo4j/neo4j_import/relations/` 아래에 생성된다.

`make_graph_csv.py`를 단독 실행하면 기본값으로 `etl/preprocessing/neo4j/graph/relations/` 아래에 생성된다. 모든 relation CSV는 보통 `start_*_id`, `end_*_id`, `relation_type`을 가진다.

### 10.1 용어 중심 관계

| CSV | 행 수 | 의미 |
|---|---:|---|
| `term_has_canonical_category.csv` | 61,697 | `Term - HAS_CATEGORY - CanonicalCategory` |
| `term_in_period.csv` | 65,358 | `Term - IN_PERIOD - Period` |
| `term_about_country.csv` | 1,620 | `Term - ABOUT_COUNTRY - Country` |
| `term_about_region.csv` | 82 | `Term - ABOUT_REGION - Region` |
| `term_about_economic_domain.csv` | 2,894 | `Term - ABOUT_ECONOMIC_DOMAIN - EconomicDomain` |
| `term_about_taxonomy_facet.csv` | 22,962 | `Term - ABOUT_TAXONOMY_FACET - TaxonomyFacet` |
| `term_refers_to_person.csv` | 2,243 | `Term - REFERS_TO - Person` (이름/한자와 Term 연도/Person 생몰년이 모두 일치하는 유일 후보, 관계망 단서 기반 후보, 수동 승인 후보를 연결) |
| `term_mentions_person.csv` | 8,606 | `Term - MENTIONS_PERSON - Person` (설명문 안 신뢰된 인물명 언급. 직접 지시 관계보다 약함) |
| `term_has_search_tag.csv` | 349,531 | `Term - HAS_SEARCH_TAG - SearchTag` |
| `term_refers_to_event.csv` | 13 | `Term - REFERS_TO - Event` (이름 유일 매칭만 연결) |

`term_in_period.csv`에는 `match_type`이 있다.

| match_type | 의미 |
|---|---|
| `DIRECT` | 원문에 단일 시대가 직접 적힌 경우 |
| `RANGE_START` | 범위 표현의 시작 시대 |
| `RANGE_MIDDLE` | seed 기준으로 추론한 중간 시대 |
| `RANGE_END` | 범위 표현의 끝 시대 |

### 10.2 사건 중심 관계

| CSV | 행 수 | 의미 |
|---|---:|---|
| `event_has_source_category.csv` | 713 | `Event - HAS_EVENT_CATEGORY - SourceEventCategory` |
| `event_has_canonical_category.csv` | 692 | `Event - HAS_CATEGORY - CanonicalCategory` |
| `event_has_facet.csv` | 713 | `Event - HAS_EVENT_FACET - EventFacet` |
| `event_in_period.csv` | 600 | `Event - IN_PERIOD - Period` |
| `event_part_of_event_group.csv` | 224 | `Event - PART_OF_EVENT_GROUP - EventGroup` |
| `event_has_source_url.csv` | 2,382 | `Event - HAS_SOURCE_URL - SourceUrl` |
| `event_has_search_tag.csv` | 6,016 | `Event - HAS_SEARCH_TAG - SearchTag` |
| `event_about_country.csv` | 2 | `Event - ABOUT_COUNTRY - Country` |
| `event_about_taxonomy_facet.csv` | 714 | `Event - ABOUT_TAXONOMY_FACET - TaxonomyFacet` |

`event_about_region.csv`, `event_about_economic_domain.csv`는 현재 생성하지 않는다.

이 두 관계는 설계상 가능한 관계지만, 현재 `taxonomy_crosswalk.csv` 기준 이벤트-표준 카테고리 매핑 결과가 `Region`, `EconomicDomain` 축까지 닿지 않는다. 0행 CSV를 최종 import 폴더에 남겨두면 실제 그래프에 들어가는 관계처럼 보이고, 문서와 검수 단계에서 불필요한 혼란이 생긴다. 그래서 현재 구현은 0행일 때 CSV를 물리적으로 생성하지 않고, 나중에 seed/mapping 보강으로 행이 생기면 자동으로 다시 생성하는 방식이다.

현재 `history_graph_import_relations.cypher`에는 이 두 관계의 LOAD 블록이 없다. 따라서 Cypher 파일을 직접 실행해도 존재하지 않는 optional CSV 때문에 실패하지 않는다. `load_schema.py`의 optional skip 로직(해당 CSV 파일이 없으면 그 LOAD 문장만 건너뜀)은 이후 LOAD 블록을 다시 추가할 경우를 대비한 방어 장치로 남아 있다. 나중에 매핑이 보강되어 실제 행이 생기면 CSV는 자동으로 다시 생성되므로, LOAD 블록만 추가하면 별도 쿼리 구조를 다시 설계하지 않아도 된다.

### 10.3 인물 중심 관계

| CSV | 행 수 | 의미 |
|---|---:|---|
| `person_involved_in_event.csv` | 6,918 | `Person - INVOLVED_IN - Event` |
| `person_related_to_person.csv` | 184,044 | `Person - RELATED_TO - Person` (대칭 관계는 한 방향만 저장) |
| `person_has_source_url.csv` | 56,212 | `Person - HAS_SOURCE_URL - SourceUrl` |
| `person_has_search_tag.csv` | 238,817 | `Person - HAS_SEARCH_TAG - SearchTag` |

`person_related_to_person.csv`는 `relation_type_dictionary.csv`를 적용한 결과다.

주요 속성:

- `raw_relation_type`: 원본 관계명
- `normalized_relation_type`: 정규화 관계명
- `relation_group`: 가족, 교유, 사제 등 관계 묶음
- `direction_rule`: 방향성 처리 기준
- `is_symmetric`: 대칭 관계 여부
- `inverse_relation_type`: 반대 방향 관계 후보
- `evidence_url`: 인물 관계 근거 URL. 별도 `SourceUrl` 노드 관계로 만들지 않음

### 10.4 카테고리와 facet 관계

| CSV | 행 수 | 의미 |
|---|---:|---|
| `canonical_category_subcategory_of.csv` | 335 | `CanonicalCategory - SUBCATEGORY_OF - CanonicalCategory` |
| `source_category_mapped_to_canonical_category.csv` | 45 | `SourceEventCategory - MAPPED_TO_CATEGORY - CanonicalCategory` |
| `canonical_category_about_country.csv` | 41 | `CanonicalCategory - ABOUT_COUNTRY - Country` |
| `canonical_category_about_region.csv` | 13 | `CanonicalCategory - ABOUT_REGION - Region` |
| `canonical_category_about_economic_domain.csv` | 51 | `CanonicalCategory - ABOUT_ECONOMIC_DOMAIN - EconomicDomain` |
| `canonical_category_about_taxonomy_facet.csv` | 276 | `CanonicalCategory - ABOUT_TAXONOMY_FACET - TaxonomyFacet` |
| `region_subregion_of.csv` | 6 | `Region - SUBREGION_OF - Region` |
| `canonical_category_has_theme.csv` | 32 | `CanonicalCategory - HAS_THEME - Theme` |
| `period_part_of_era.csv` | 23 | `Period - PART_OF_ERA - Era` (표기 변형 통합) |
| `term_has_entity_type.csv` | 20,662 | `Term - HAS_ENTITY_TYPE - EntityType` |
| `term_in_era.csv` | 54,125 | `Term - IN_ERA - Era` (`IN_PERIOD -> PART_OF_ERA` 파생 + 키워드 override + 설명문 검수 통과분) |
| `event_in_era.csv` | 600 | `Event - IN_ERA - Era` (`IN_PERIOD -> PART_OF_ERA` 파생) |
| `person_in_era.csv` | 23,029 | `Person - IN_ERA - Era` (생몰년 기반 + 사건 기반 보조 추론, 더 좁은 Era가 있으면 넓은 Era 중복 제거) |
| `person_has_theme.csv` | 60,512 | `Person - HAS_THEME - Theme` (인물 라벨 + 사건 참여/인명 세부 카테고리 주제 상속) |

`canonical_category_subcategory_of.csv`에서는 국가/지역 facet으로 분리된 경로를 제외했다. 따라서 `러시아`, `미국`, `기타지역` 같은 값이 `외교·국제관계`의 의미상 하위 카테고리처럼 붙지 않는다.

---

## 11. Neo4j import 관점 정리

import 쿼리에서 직접 봐야 하는 폴더는 기본적으로 두 개다.

```text
storage/neo4j/neo4j_import/nodes/
storage/neo4j/neo4j_import/relations/
```

권장 import 순서는 다음과 같다.

1. 제약조건과 인덱스 생성
2. `storage/neo4j/neo4j_import/nodes/*.csv` 전체 import
3. `storage/neo4j/neo4j_import/relations/*.csv` 전체 import
4. 참조 누락 검증

`dictionary/`, `mapping/`, `staging/`은 import 쿼리에서 반드시 넣을 필요는 없다. 검수, 재생성, 원인 추적을 위한 전처리 산출물이다.

import 쿼리는 기존 Neo4j 실행 구조에 맞춰 `storage/neo4j/schema/` 아래에 둔다.

현재 import 관련 Cypher 파일은 다음과 같다.

| 파일 | 역할 |
|---|---|
| `history_graph_constraints.cypher` | node id 제약조건과 조회용 index 생성 |
| `history_graph_import_nodes.cypher` | `storage/neo4j/neo4j_import/nodes/`의 node CSV 적재 |
| `history_graph_import_relations.cypher` | `storage/neo4j/neo4j_import/relations/`의 relationship CSV 적재 |
| `history_graph_verify.cypher` | 적재 후 노드/관계 개수 확인 |

reset은 별도 Cypher 파일이 아니라 `storage/neo4j/load_schema.py` 내부에서 실행한다. 관계를 먼저 배치 삭제하고, 그 다음 노드를 배치 삭제한다. 배치 크기는 `NEO4J_RESET_BATCH_SIZE`로 조절하며 기본값은 `10000`이다.

Docker compose 기준 Neo4j 컨테이너는 `storage/neo4j/neo4j_import`를 `/var/lib/neo4j/import`로 마운트한다. 따라서 Cypher 파일은 다음 경로를 기준으로 CSV를 읽는다.

```text
file:///nodes/*.csv
file:///relations/*.csv
```

`run_neo4j_preprocessing.py`로 실행하면 아래 위치에 바로 생성되므로 별도 복사가 필요 없다.

```text
storage/neo4j/neo4j_import/nodes/*.csv
storage/neo4j/neo4j_import/relations/*.csv
```

단, `make_graph_csv.py`를 단독 실행해서 `etl/preprocessing/neo4j/graph/` 아래에 만들었다면 import 전에 `storage/neo4j/neo4j_import/`로 복사해야 한다.

노드 import 쿼리는 `SET n += row` 뒤에 숫자 속성을 `toIntegerOrNull()`로 다시 세팅한다. CSV는 문자열 기반이라 그대로 넣으면 `start_year`, `end_year`, `period_order`, `term_count` 같은 값도 문자열이 된다. 연도 범위 검색, 시대 정렬, 집계 비교를 제대로 하려면 import 시점에서 숫자 타입을 명시해야 한다.

실행 순서는 다음과 같다.

```text
internal_graph_reset
history_graph_constraints.cypher
history_graph_import_nodes.cypher
history_graph_import_relations.cypher
history_graph_verify.cypher
```

`storage/neo4j/load_schema.py`를 실행하면 항상 기존 노드와 관계를 전부 삭제한 뒤 다시 적재한다.

---

## 12. 파일을 볼 때의 기준

문제가 생겼을 때는 다음 순서로 추적한다.

| 문제 | 먼저 볼 파일 |
|---|---|
| 원본 row가 잘못 줄었는지 확인 | `normalized/*.csv` |
| 카테고리 목록 자체가 이상함 | `dictionary/canonical_category_dictionary.csv` |
| 용어와 카테고리 연결이 이상함 | `staging/term_canonical_category_relation.csv` |
| 이벤트 분류 매핑이 이상함 | `mapping/taxonomy_crosswalk.csv` |
| 국가/지역/경제 분야 연결이 이상함 | `mapping/canonical_category_*_crosswalk.csv` |
| 시대 범위가 이상하게 펼쳐짐 | `seed/period_seed.csv`, `dictionary/period_dictionary.csv`, `storage/neo4j/neo4j_import/relations/term_in_period.csv` |
| 인물 관계 방향/의미가 이상함 | `seed/relation_type_seed.csv`, `dictionary/relation_type_dictionary.csv`, `storage/neo4j/neo4j_import/relations/person_related_to_person.csv` |
| Neo4j import 대상 확인 | `storage/neo4j/neo4j_import/nodes/*.csv`, `storage/neo4j/neo4j_import/relations/*.csv` |

정리하면, 전처리 단계의 핵심 산출물은 많지만 역할은 분리되어 있다.

- 원본 정리: `normalized/`
- 기준 정의: `dictionary/`
- 연결 규칙: `mapping/`
- 중간 관계: `staging/`
- 최종 import: `graph/`

---

## 13. 2026-07-03 추가 파생 산출물

이번 보강으로 최종 import CSV의 의미가 일부 확장되었다. 새 파일을 별도 사전처럼 늘리는 대신, 원래 있어야 할 노드 속성은 노드 CSV에 넣고, 탐색에 필요한 관계만 relationship CSV로 추가했다.

### 13.1 `nodes/terms.csv`

기존 Term 기본 속성에 다음 파생 컬럼이 추가된다.

| 컬럼 | 생성 기준 | 사용 이유 |
|---|---|---|
| `start_year` | `year_text`에서 첫 숫자 연도 추출 | 연도 범위 검색 |
| `end_year` | `year_text`에서 마지막 숫자 연도 추출 | 기간형 용어 검색 |
| `year_precision` | 연도 표현 형태 | 정확 연도/범위/부분 연도 구분 |
| `year_parse_status` | 숫자 연도 추출 성공 여부 | 불명확한 연도 검수 |
| `description_length` | 설명문 길이 | 지문 생성 가능성 판단 |
| `question_ready` | 설명문 50자 이상이면 `Y` | 서비스 쿼리에서 바로 필터 |
| `is_exam_keyword` | `keyword_era_seed.csv` 키워드 매칭 | 시험 빈출 후보 우선순위 |

`start_year`, `end_year`, `year_precision`, `year_parse_status`는 `make_graph_csv.py`에서 즉석 계산하지 않는다. `make_base_dictionaries.py`가 먼저 `staging/term_year_parse.csv`를 만들고, 최종 graph 단계가 이 staging CSV를 `Term` 노드에 병합한다. 이렇게 분리한 이유는 파싱 결과를 EDA/검수 산출물로 직접 확인할 수 있게 하기 위해서다.

`term_year_parse.csv`의 현재 분포는 `PARSED` 33,458건, `UNKNOWN` 28,140건이다. precision은 `YEAR_RANGE`, `EXACT_YEAR`, `PARTIAL`, `MULTI`, `DECADE`, `UNKNOWN`을 사용한다. `reign_seed.csv`는 왕대/연호 표현을 숫자 연도로 보강하기 위한 seed이며, 같은 왕 이름이 여러 시대에 있으면 자동 계산에서 제외한다.

### 13.2 `nodes/people.csv`

`degree` 컬럼이 추가된다. 사건 참여 관계와 인물 관계에 등장한 횟수를 합산한 값이다. 이 값은 중심 인물 후보를 고르거나, 관계가 풍부한 인물을 우선 출제할 때 사용한다.

### 13.3 `nodes/eras.csv`

`era_seed.csv`에 `start_year`, `end_year`를 추가했고, 최종 `eras.csv`에도 같은 컬럼이 들어간다. 이 범위는 `Person - IN_ERA - Era`를 생몰년으로 생성할 때 기준이 된다.

### 13.4 새 relationship CSV

| CSV | 현재 건수 | 의미 |
|---|---:|---|
| `term_in_era.csv` | 54,125 | `Term - IN_ERA - Era`. `Term - IN_PERIOD - Period - PART_OF_ERA - Era`를 미리 펼친 관계이며, 키워드 override와 설명문 기반 검수 통과분(DESC_KEYWORD)을 합류한다. |
| `event_in_era.csv` | 600 | `Event - IN_ERA - Era`. 사건의 period를 Era로 펼친 관계다. |
| `person_in_era.csv` | 23,029 | `Person - IN_ERA - Era`. 생몰년 기반 연결을 우선하고, 생몰년이 없는 인물은 참여 사건 Era로 보조 추론한다. 더 좁은 Era가 같은 생애 겹침 구간을 완전히 설명하면 넓은 Era 중복은 제외한다. |
| `person_has_theme.csv` | 60,512 | `Person - HAS_THEME - Theme`. 모든 Person은 `인물` 주제에 연결하고, 참여 사건과 인명 세부 카테고리에서 얻은 내용 주제를 보조 상속한다. |

### 13.5 왜 직접 관계를 만들었는가

`Term/Event -> Period -> Era` 또는 `Person -> Event -> Period -> Era` 경로만으로도 이론상 조회는 가능하다. 하지만 문제 출제와 서비스 필터에서는 “시대 하나를 고르면 바로 후보를 가져오는” 쿼리가 자주 필요하다. 따라서 원천 관계는 유지하되, 자주 쓰는 축만 직접 관계로 물리화했다.

직접 관계는 원본이 아니라 파생 산출물이므로, 기준을 바꾸고 싶으면 seed와 중간 관계를 수정한 뒤 `run_neo4j_preprocessing.py`를 다시 실행하면 된다.

---

## 14. Event 분류 표준화와 검색 태그 레이어

이벤트 분류는 `terms.csv`의 `term_lk`와 바로 합치지 않는다. `term_lk`는 계층형 시소러스이고, `events.csv.subject_category`는 사건 데이터의 평면 원본 분류이기 때문이다.

관련 파일의 역할은 다음과 같다.

| 파일 | 역할 |
|---|---|
| `dictionary/source_event_category_dictionary.csv` | 이벤트 원본 분류 53개를 그대로 보존 |
| `dictionary/canonical_category_dictionary.csv` | `term_lk`를 분해해 만든 표준 카테고리 400개 |
| `mapping/taxonomy_crosswalk.csv` | 이벤트 원본 분류를 표준 카테고리로 연결 |
| `dictionary/event_facet_dictionary.csv` | 이벤트 분류를 사건 의미 facet으로 정리 |
| `storage/neo4j/neo4j_import/nodes/search_tags.csv` | Term/Event/Person 검색용 통합 태그 |
| `storage/neo4j/neo4j_import/relations/term_has_search_tag.csv` | 용어가 가진 이름·분류·시대·주제·유형 축을 한 검색 관계로 모은 관계 |
| `storage/neo4j/neo4j_import/relations/event_has_search_tag.csv` | 사건이 가진 이름·분류·시대·주제 축을 한 검색 관계로 모은 관계 |
| `storage/neo4j/neo4j_import/relations/person_has_search_tag.csv` | 인물이 가진 이름·별칭·참여 사건·지시 용어·주제·시대 축을 한 검색 관계로 모은 관계 |

`EventFacet`과 `SearchTag`는 일부 값이 겹친다. 하지만 `EventFacet`은 의미 축이고, `SearchTag`는 빠른 검색을 위한 비정규화 축이다.

정밀 탐색은 다음 축을 사용한다.

- `Event - HAS_EVENT_CATEGORY - SourceEventCategory`
- `Event - HAS_CATEGORY - CanonicalCategory`
- `Event - HAS_EVENT_FACET - EventFacet`

키워드 기반 빠른 검색은 다음 축을 사용한다.

```cypher
MATCH (n)-[:HAS_SEARCH_TAG]->(:SearchTag {tag_name: "전쟁"})
RETURN DISTINCT n;
```

`SearchTag`는 쿼리를 쉽게 만들기 위한 중복 레이어이므로, 태그의 출처를 잃지 않도록 `HAS_SEARCH_TAG` 관계에 `source_node_type`, `source_node_id`, `source_relation`, `source_detail` 속성을 남긴다.
Person 이름 별칭은 `source_node_type=PersonAlias`, `source_relation=person_alias`로 별도 태그를 만든다.
Person이 Event/Term에서 SearchTag를 상속받은 경우 `source_detail`에는 원천 `event_id` 또는 `term_id` 묶음이 들어가므로, 빠른 검색 뒤에도 어떤 원천 연결에서 온 태그인지 되짚을 수 있다.

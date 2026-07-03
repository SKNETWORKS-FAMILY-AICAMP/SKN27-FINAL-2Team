# Neo4j 전처리 폴더와 파일 역할 정리

이 문서는 `etl/preprocessing/neo4j` 아래 폴더, CSV 산출물, Python 스크립트의 의미를 정리한다.

핵심 기준은 다음과 같다.

- `normalized/`, `dictionary/`, `mapping/`, `staging/`은 전처리 중간 산출물이다.
- `graph/nodes/`, `graph/relations/`가 Neo4j import의 최종 대상이다.
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
  -> graph/nodes + graph/relations
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
| `graph/nodes/` | 최종 import 대상 | Neo4j node로 import할 CSV |
| `graph/relations/` | 최종 import 대상 | Neo4j relationship으로 import할 CSV |
| `__pycache__/` | 실행 캐시 | Python이 자동 생성한 캐시 폴더. import 대상 아님 |

Neo4j import에서는 보통 `graph/nodes/`를 먼저 넣고, 그 다음 `graph/relations/`를 넣는다.

### 2.1 runner가 생성하는 CSV와 생성하지 않는 CSV

`run_neo4j_preprocessing.py`는 다음 폴더의 CSV를 생성하거나 재생성한다.

- `normalized/`
- `dictionary/`
- `mapping/`
- `staging/`
- `graph/nodes/`
- `graph/relations/`

반대로 `seed/` 폴더의 CSV는 runner가 생성하지 않는다. `seed/`는 사람이 직접 관리하는 입력 규칙표이기 때문이다.

현재 runner가 생성하지 않는 CSV는 다음 7개다.

- `seed/category_axis_seed.csv`
- `seed/country_seed.csv`
- `seed/event_facet_seed.csv`
- `seed/period_seed.csv`
- `seed/region_seed.csv`
- `seed/relation_type_seed.csv`
- `seed/taxonomy_crosswalk_seed.csv`

이 파일들은 자동 생성 산출물이 아니라 전처리 규칙을 담은 입력 파일이다.

### 2.2 더 이상 생성하지 않는 예전 이름 CSV

현재 구조에서는 다음 예전 이름의 CSV를 더 이상 생성하지 않는다.

| 예전 파일명 | 현재 대체 파일 |
|---|---|
| `dictionary/category_dictionary.csv` | `dictionary/canonical_category_dictionary.csv` |
| `dictionary/event_category_dictionary.csv` | `dictionary/source_event_category_dictionary.csv` |
| `dictionary/category_mapping.csv` | `mapping/taxonomy_crosswalk.csv` |
| `staging/term_category_relation.csv` | `staging/term_canonical_category_relation.csv` |
| `staging/event_category_relation.csv` | `staging/event_source_category_relation.csv` |

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

`dictionary/`는 그래프 노드 후보를 정의하는 기준표다. 여기 있는 파일은 대부분 최종 `graph/nodes/`로 변환된다.

| CSV | 행 수 | 의미 |
|---|---:|---|
| `canonical_category_dictionary.csv` | 400 | `history_terms.term_lk`를 분해해 만든 표준 카테고리 사전 |
| `source_event_category_dictionary.csv` | 53 | `events.subject_category`에서 만든 원본 사건 분류 사전 |
| `period_dictionary.csv` | 30 | 시대 노드 기준 사전 |
| `relation_type_dictionary.csv` | 16 | 인물 관계 유형 정규화 사전 |
| `source_url_dictionary.csv` | 79,693 | URL 출처와 RAG 수집 대상 사전 |
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
| `event_date_parse.csv` | 600 | 사건 날짜 원문 parsing 결과 |

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

## 9. `graph/nodes/` CSV

`graph/nodes/`는 Neo4j node import 대상이다. 이 폴더의 파일은 실제 그래프 노드가 된다.

| CSV | 행 수 | Neo4j 노드 의미 |
|---|---:|---|
| `terms.csv` | 61,598 | `Term` 노드 |
| `events.csv` | 600 | `Event` 노드 |
| `people.csv` | 56,403 | `Person` 노드 |
| `canonical_categories.csv` | 400 | `CanonicalCategory` 노드 |
| `source_event_categories.csv` | 53 | `SourceEventCategory` 노드 |
| `periods.csv` | 30 | `Period` 노드 |
| `source_urls.csv` | 79,693 | `SourceUrl` 노드 |
| `event_groups.csv` | 32 | 관련 사건 묶음 `EventGroup` 노드 |
| `event_facets.csv` | 53 | 사건 의미 facet 노드 |
| `countries.csv` | 5 | 국가/정치체 노드 |
| `regions.csv` | 7 | 지역/권역 노드 |
| `economic_domains.csv` | 16 | 경제 분야 노드 |
| `taxonomy_facets.csv` | 49 | 중간 taxonomy facet 노드 |
| `search_tags.csv` | 583 | 검색 편의용 통합 tag 노드 |

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

## 10. `graph/relations/` CSV

`graph/relations/`는 Neo4j relationship import 대상이다. 모든 파일은 보통 `start_*_id`, `end_*_id`, `relation_type`을 가진다.

### 10.1 용어 중심 관계

| CSV | 행 수 | 의미 |
|---|---:|---|
| `term_has_canonical_category.csv` | 61,697 | `Term - HAS_CATEGORY - CanonicalCategory` |
| `term_in_period.csv` | 65,358 | `Term - IN_PERIOD - Period` |
| `term_about_country.csv` | 1,620 | `Term - ABOUT_COUNTRY - Country` |
| `term_about_region.csv` | 82 | `Term - ABOUT_REGION - Region` |
| `term_about_economic_domain.csv` | 2,894 | `Term - ABOUT_ECONOMIC_DOMAIN - EconomicDomain` |
| `term_about_taxonomy_facet.csv` | 22,962 | `Term - ABOUT_TAXONOMY_FACET - TaxonomyFacet` |

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
| `event_has_search_tag.csv` | 2,811 | `Event - HAS_SEARCH_TAG - SearchTag` |
| `event_about_country.csv` | 2 | `Event - ABOUT_COUNTRY - Country` |
| `event_about_region.csv` | 0 | `Event - ABOUT_REGION - Region` |
| `event_about_economic_domain.csv` | 0 | `Event - ABOUT_ECONOMIC_DOMAIN - EconomicDomain` |
| `event_about_taxonomy_facet.csv` | 714 | `Event - ABOUT_TAXONOMY_FACET - TaxonomyFacet` |

`event_about_region.csv`, `event_about_economic_domain.csv`이 0건인 것은 현재 이벤트-표준 카테고리 매핑 결과가 해당 의미 축까지 연결되지 않았기 때문이다. `taxonomy_crosswalk.csv`를 보강하면 증가할 수 있다.

### 10.3 인물 중심 관계

| CSV | 행 수 | 의미 |
|---|---:|---|
| `person_involved_in_event.csv` | 6,918 | `Person - INVOLVED_IN - Event` |
| `person_related_to_person.csv` | 206,507 | `Person - RELATED_TO - Person` |
| `person_has_source_url.csv` | 56,212 | `Person - HAS_SOURCE_URL - SourceUrl` |

`person_related_to_person.csv`는 `relation_type_dictionary.csv`를 적용한 결과다.

주요 속성:

- `raw_relation_type`: 원본 관계명
- `normalized_relation_type`: 정규화 관계명
- `relation_group`: 가족, 교유, 사제 등 관계 묶음
- `direction_rule`: 방향성 처리 기준
- `is_symmetric`: 대칭 관계 여부
- `inverse_relation_type`: 반대 방향 관계 후보

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

`canonical_category_subcategory_of.csv`에서는 국가/지역 facet으로 분리된 경로를 제외했다. 따라서 `러시아`, `미국`, `기타지역` 같은 값이 `외교·국제관계`의 의미상 하위 카테고리처럼 붙지 않는다.

---

## 11. Neo4j import 관점 정리

import 쿼리에서 직접 봐야 하는 폴더는 기본적으로 두 개다.

```text
graph/nodes/
graph/relations/
```

권장 import 순서는 다음과 같다.

1. 제약조건과 인덱스 생성
2. `graph/nodes/*.csv` 전체 import
3. `graph/relations/*.csv` 전체 import
4. 참조 누락 검증

`dictionary/`, `mapping/`, `staging/`은 import 쿼리에서 반드시 넣을 필요는 없다. 검수, 재생성, 원인 추적을 위한 전처리 산출물이다.

다만 import 쿼리가 복잡해질 수 있으므로, 다음 단계에서는 `graph/nodes/`와 `graph/relations/`의 파일 목록을 기준으로 Cypher import 파일을 자동 생성하거나, 별도 import runner를 두는 편이 좋다.

예상 구조:

```text
etl/preprocessing/neo4j/import/
  constraints.cypher
  import_nodes.cypher
  import_relations.cypher
  verify_import.cypher
  run_neo4j_import.py
```

이렇게 분리하면 전처리 CSV 생성과 Neo4j 적재 쿼리를 서로 섞지 않고 관리할 수 있다.

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
| 시대 범위가 이상하게 펼쳐짐 | `seed/period_seed.csv`, `dictionary/period_dictionary.csv`, `graph/relations/term_in_period.csv` |
| 인물 관계 방향/의미가 이상함 | `seed/relation_type_seed.csv`, `dictionary/relation_type_dictionary.csv`, `graph/relations/person_related_to_person.csv` |
| Neo4j import 대상 확인 | `graph/nodes/*.csv`, `graph/relations/*.csv` |

정리하면, 전처리 단계의 핵심 산출물은 많지만 역할은 분리되어 있다.

- 원본 정리: `normalized/`
- 기준 정의: `dictionary/`
- 연결 규칙: `mapping/`
- 중간 관계: `staging/`
- 최종 import: `graph/`

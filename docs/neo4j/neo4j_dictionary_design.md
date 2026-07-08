# Neo4j 사전 설계 문서

## 1. 목적

이 문서는 한국사 Neo4j 그래프 구축 전에 필요한 사전(dictionary), 중간 관계 파일(staging relation), 매핑(mapping) 파일의 역할과 필요성을 정리한다.

Neo4j에 데이터를 넣기 전에 사전을 만드는 이유는 원본 CSV의 문자열 값이 그대로는 그래프 탐색에 적합하지 않기 때문이다. 원본에는 카테고리, 시대, 관계 유형, URL 등이 문자열로 들어 있지만, 이 값들은 다음 문제가 있다.

- 같은 의미가 다른 표현으로 반복될 수 있다.
- 하나의 문자열 안에 여러 값이 섞여 있다.
- 계층 구조가 문자열 기호로만 표현되어 있다.
- 관계 방향성이나 대칭성이 명확하지 않다.
- 출처 URL이 여러 테이블과 컬럼에 흩어져 있다.

따라서 전처리 단계에서 다음 세 가지 파일 유형을 분리한다.

| 구분 | 의미 | 예시 |
|---|---|---|
| Dictionary | 표준 목록을 정하는 파일 | `canonical_category_dictionary.csv`, `source_event_category_dictionary.csv`, `relation_type_dictionary.csv` |
| Staging relation | 원본 데이터를 Neo4j 관계로 넣기 좋게 펼친 중간 파일 | `term_canonical_category_relation.csv`, `event_source_category_relation.csv` |
| Mapping | 서로 다른 분류 체계를 연결하는 파일 | `taxonomy_crosswalk.csv` |

정리하면 dictionary는 기준을 만들고, staging은 원본을 그래프 관계로 펼치며, mapping은 서로 다른 기준을 연결한다.

노드와 관계를 왜 그런 단위로 나누었는지, 각 노드가 없으면 어떤 문제가 생기는지, 어떤 대안을 버렸는지는 `docs/neo4j/neo4j_design_decisions_detail.md`에 상세히 기록한다.

---

## 사전 설계 판단의 위치

이 문서는 사전과 중간 CSV가 무엇을 담는지, 어떤 컬럼과 생성 규칙을 가지는지에 집중한다.
사전을 왜 이렇게 나누었는지, 사전이 없으면 어떤 문제가 생기는지, 원본 보존과 표준화를 왜 분리했는지는 `docs/neo4j/neo4j_design_decisions_detail.md`에 합쳤다.

특히 다음 판단은 상세 설계 판단 문서를 기준으로 본다.

- `CanonicalCategory`, `SourceEventCategory`, `EventFacet`, `TaxonomyFacet`을 나눈 이유
- `Country`, `Region`, `EconomicDomain`을 카테고리에서 별도 의미 축으로 분리한 이유
- 인물 관계 타입을 쪼개지 않고 `RELATED_TO`와 속성으로 둔 이유
- `SourceUrl` 노드와 `RELATED_TO.evidence_url` 속성을 분리한 이유
- SearchTag가 정규화 노드가 아니라 검색 보조 레이어인 이유

---

## 2. 권장 디렉터리 구조

Neo4j 전처리 산출물은 원본과 분리해서 관리한다.

```text
etl/
  preprocessing/
    neo4j/
      normalized/
        terms.csv
        events.csv
        event_relations.csv
        person_relations.csv

      dictionary/
        canonical_category_dictionary.csv
        source_event_category_dictionary.csv
        taxonomy_crosswalk.csv
        period_dictionary.csv
        relation_type_dictionary.csv
        source_url_dictionary.csv

      staging/
        term_canonical_category_relation.csv
        event_source_category_relation.csv
        event_date_parse.csv
        person_relation_staging.csv

      neo4j_import/
        nodes.csv
        relationships.csv
```

현재 판단 기준은 다음과 같다.

- `normalized/`: EDA 기준으로 정리한 raw 데이터
- `dictionary/`: 표준 기준표
- `staging/`: Neo4j 관계 생성 전 중간 산출물
- `neo4j_import/`: 최종 `LOAD CSV`용 파일

---

## 3. 전체 파일 요약

| 파일 | 위치 | 역할 |
|---|---|---|
| `terms.csv` | `normalized/` | EDA 기준으로 정리한 역사 용어 원본 |
| `events.csv` | `normalized/` | EDA 기준으로 정리한 사건 원본 |
| `event_relations.csv` | `normalized/` | EDA 기준으로 정리한 사건-인물 관계 원본 |
| `person_relations.csv` | `normalized/` | EDA 기준으로 정리한 인물-인물 관계 원본 |
| `canonical_category_dictionary.csv` | `dictionary/` | `history_terms.term_lk` 기반 `CanonicalCategory` 노드 사전 |
| `term_canonical_category_relation.csv` | `staging/` | `Term - HAS_CANONICAL_CATEGORY - CanonicalCategory` 관계 생성용 |
| `source_event_category_dictionary.csv` | `dictionary/` | `itkc_events.subject_category` 기반 이벤트 분류 사전 |
| `event_source_category_relation.csv` | `staging/` | `Event - HAS_EVENT_CATEGORY - SourceEventCategory` 관계 생성용 |
| `taxonomy_crosswalk.csv` | `mapping/` | 이벤트 분류와 표준 카테고리 연결 규칙 |
| `period_dictionary.csv` | `dictionary/` | `Period` 노드 기준 사전 |
| `event_date_parse.csv` | `staging/` | Event 날짜 원문 정규화 결과 |
| `relation_type_dictionary.csv` | `dictionary/` | 인물 관계 의미, 방향, 대칭성 규칙 |
| `source_url_dictionary.csv` | `dictionary/` | URL 출처와 RAG 수집 기준 사전 |

---

## 4. `canonical_category_dictionary.csv`

### 4.1 역할

`canonical_category_dictionary.csv`는 `history_terms.term_lk`에서 만든 표준 카테고리 사전이다.

원본 `term_lk`는 다음처럼 문자열 하나에 계층 정보가 들어 있다.

```text
정치·행정·법제>행정>중앙행정기구
교통·통신>교통시설>>교통·통신>교통로
```

여기서 `>`는 계층이고, `>>`는 복수 카테고리 경로다.

이 값을 그대로 `Term` 속성에만 넣으면 다음 질의가 불편해진다.

- 정치·행정·법제 아래의 모든 용어 찾기
- 행정 관련 용어 찾기
- 문화·예술 하위 카테고리 전체 탐색
- 같은 카테고리의 오답 후보 찾기

그래서 `term_lk`를 분해해서 `CanonicalCategory` 노드로 만들 기준표가 필요하다.

### 4.2 왜 필요한가

`canonical_category_dictionary.csv`가 필요한 이유는 다음과 같다.

1. `CanonicalCategory` 노드의 기준 목록이 된다.
2. `SUBCATEGORY_OF` 관계를 만들 수 있다.
3. 같은 카테고리에 속한 용어를 찾을 수 있다.
4. 문제 생성에서 같은 분류의 오답 후보를 찾을 수 있다.
5. 이벤트 카테고리와 매핑할 표준 기준점이 된다.

### 4.3 생성 방식

예를 들어 다음 경로가 있다.

```text
정치·행정·법제>행정>중앙행정기구
```

이 값은 다음 카테고리 row로 분해한다.

| depth | category_name | category_path | parent_category_path |
|---|---|---|---|
| 1 | 정치·행정·법제 | 정치·행정·법제 |  |
| 2 | 행정 | 정치·행정·법제>행정 | 정치·행정·법제 |
| 3 | 중앙행정기구 | 정치·행정·법제>행정>중앙행정기구 | 정치·행정·법제>행정 |

이렇게 하는 이유는 `category_name`만으로는 고유성을 보장할 수 없기 때문이다. 서로 다른 상위 카테고리 아래에 같은 이름의 하위 카테고리가 나올 수 있다. 따라서 고유 기준은 `category_path`가 된다.

### 4.4 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `category_id` | 카테고리 고유 ID |
| `category_name` | 현재 단계 이름 |
| `category_path` | 루트부터 현재 단계까지의 전체 경로 |
| `parent_category_id` | 상위 카테고리 ID |
| `parent_category_path` | 상위 카테고리 경로 |
| `depth` | 계층 깊이 |
| `root_category_name` | 최상위 카테고리 이름 |
| `term_count` | 해당 경로에 연결되는 용어 수 |
| `source` | 생성 근거 |
| `review_status` | 검수 상태 |

---

## 5. `term_canonical_category_relation.csv`

### 5.1 역할

`term_canonical_category_relation.csv`는 dictionary가 아니라 staging relation이다. `Term`과 `CanonicalCategory`를 연결하기 위한 중간 산출물이다.

`canonical_category_dictionary.csv`만 있으면 카테고리 목록은 알 수 있지만, 어떤 `term_id`가 어떤 카테고리에 속하는지는 알 수 없다. 그 연결 정보가 `term_canonical_category_relation.csv`다.

### 5.2 왜 필요한가

이 관계는 Cypher 쿼리로도 만들 수 있다. 하지만 `term_lk`에는 `>`, `>>` 파싱이 들어가고, 복수 카테고리 경로가 존재하므로 전처리에서 펼쳐두는 편이 안전하다.

필요한 이유는 다음과 같다.

1. Neo4j import가 단순해진다.
2. Cypher에서 문자열 파싱 로직을 반복하지 않아도 된다.
3. 복수 카테고리 연결을 명확하게 확인할 수 있다.
4. 원본 `term_lk`에서 어떤 관계가 만들어졌는지 검수할 수 있다.
5. `Term - HAS_CANONICAL_CATEGORY - CanonicalCategory` 관계를 안정적으로 만들 수 있다.

### 5.3 생성 방식

예를 들어 다음 원본이 있다.

```text
term_id = 5626
term_lk = 문화·예술>음악
```

관계 staging은 다음처럼 된다.

```text
5626 -> 문화·예술>음악
```

복수 경로가 있으면 한 용어가 여러 카테고리에 연결된다.

```text
term_lk = 교통·통신>교통시설>>교통·통신>교통로
```

위 값은 다음 두 관계로 펼쳐진다.

```text
Term -> 교통·통신>교통시설
Term -> 교통·통신>교통로
```

### 5.4 Term은 leaf category에만 연결

Term은 가장 구체적인 leaf category에만 직접 연결한다.

예를 들어 다음 경로가 있다.

```text
사회·생활>풍속·의례>매장
```

이때 Term을 `사회·생활`, `풍속·의례`, `매장`에 모두 연결하지 않는다. Term은 `매장`에만 연결하고, 상위 탐색은 `SUBCATEGORY_OF` 관계를 타고 올라가게 한다.

```text
Term -> 매장
매장 -> 풍속·의례
풍속·의례 -> 사회·생활
```

이 방식이 중복 관계를 줄이고, 그래프 탐색 구조도 더 명확하다.

### 5.5 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `term_id` | 용어 ID |
| `category_id` | 연결할 카테고리 ID |
| `category_path` | 사람이 확인하기 위한 카테고리 경로 |
| `source_term_lk` | 원본 `term_lk` 값 |

---

## 6. `source_event_category_dictionary.csv`

### 6.1 역할

`source_event_category_dictionary.csv`는 `itkc_events.csv.subject_category`에서 만든 이벤트 전용 카테고리 사전이다.

이벤트의 `subject_category`는 `history_terms.term_lk`와 성격이 다르다. `term_lk`는 계층형 용어 분류에 가깝고, `subject_category`는 사건 수집 과정에서 붙은 사건 분류에 가깝다.

예시는 다음과 같다.

```text
전쟁
반란
옥사
고변/탄핵
반란,\r\n\r\n정치인
인물기타,\r\n\r\n왜(변)란
```

### 6.2 왜 필요한가

이 사전이 필요한 이유는 다음과 같다.

1. 이벤트 원본 분류를 보존한다.
2. 복합 문자열을 토큰 단위로 정리한다.
3. 이벤트 분류의 빈도와 검수 대상을 확인할 수 있다.
4. `history_terms` 표준 카테고리와 바로 합치지 않고 매핑할 준비를 한다.
5. Event 질의에서 원본 이벤트 분류 기준 필터링을 지원한다.

### 6.3 분리 기준

`subject_category`는 쉼표와 줄바꿈으로 복수값이 들어갈 수 있다.

```text
반란,\r\n\r\n정치인
```

이 값은 다음 두 이벤트 카테고리로 분리한다.

```text
반란
정치인
```

단, `/`는 처음부터 무조건 분리하지 않는다. 예를 들어 `고변/탄핵`은 하나의 분류명일 수 있으므로 원형을 먼저 보존한다.

### 6.4 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `event_category_id` | 이벤트 카테고리 고유 ID |
| `event_category_name` | 분리된 이벤트 카테고리 이름 |
| `event_count` | 해당 카테고리를 가진 사건 수 |
| `source` | 원본 컬럼 |
| `review_status` | 검수 상태 |

---

## 7. `event_source_category_relation.csv`

### 7.1 역할

`event_source_category_relation.csv`는 dictionary가 아니라 staging relation이다. `Event`와 `SourceEventCategory`를 연결하기 위한 파일이다.

### 7.2 쿼리로 만들 수 있는가

가능은 하다. `term_lk`보다 파싱 난도는 낮다. 하지만 다음 이유 때문에 전처리 산출물로 만드는 편이 좋다.

- `subject_category`에 쉼표와 줄바꿈이 섞여 있다.
- 같은 `event_id`가 `event_subject`, `event_period` scope에서 중복 수집되어 있다.
- `detail_url`만 다른 중복 행이 존재한다.
- 이벤트 분류를 어떻게 분리했는지 검수할 수 있어야 한다.

따라서 이 파일은 `staging/`에 두는 것이 좋다.

### 7.3 왜 필요한가

1. `Event - HAS_EVENT_CATEGORY - SourceEventCategory` 관계를 명확히 만든다.
2. `subject_category` 복합값을 여러 관계로 펼친다.
3. 같은 `event_id` 중복을 정리한 뒤 관계를 생성할 수 있다.
4. 원본 `subject_category`를 보존해 검수할 수 있다.
5. 이후 `taxonomy_crosswalk.csv`를 통해 표준 카테고리와 연결할 수 있다.

### 7.4 생성 예시

단일값:

```text
event_id = ITKC_PH_1294A_0435
subject_category = 전쟁

Event -> 전쟁
```

복합값:

```text
subject_category = 반란,\r\n\r\n정치인

Event -> 반란
Event -> 정치인
```

### 7.5 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `event_id` | 사건 ID |
| `event_category_id` | 이벤트 카테고리 ID |
| `event_category_name` | 이벤트 카테고리 이름 |
| `source_subject_category` | 원본 `subject_category` 값 |

---

## 8. `taxonomy_crosswalk.csv`

### 8.1 역할

`taxonomy_crosswalk.csv`는 `source_event_category_dictionary.csv`와 `canonical_category_dictionary.csv`를 연결하는 매핑표다.

중요한 점은 이 파일이 두 사전을 대체하지 않는다는 것이다.

```text
source_event_category_dictionary.csv = 이벤트 원본 분류 사전
canonical_category_dictionary.csv = history_terms.term_lk 기반 표준 카테고리 사전
taxonomy_crosswalk.csv = 두 분류 체계를 연결하는 규칙표
```

### 8.2 왜 필요한가

이벤트 분류와 역사 용어 분류는 같은 체계가 아니다. 따라서 문자열이 비슷하다고 바로 합치면 의미가 섞일 수 있다.

예를 들어 이벤트 카테고리의 `전쟁`은 `history_terms`의 특정 카테고리 경로와 완전히 같은 문자열이 아닐 수 있다. 그러므로 다음처럼 명시적인 매핑표가 필요하다.

```text
전쟁 -> 국방·군사
옥사 -> 정치·행정·법제>사법
국왕 -> 인물
기관 -> 정치·행정·법제>행정>중앙행정기구
```

### 8.3 이 파일이 가능하게 하는 것

1. Event와 Term을 공통 카테고리 기준으로 연결한다.
2. 이벤트 기반 문제와 용어 기반 문제를 같은 분류 체계에서 다룬다.
3. 매핑이 애매한 항목을 검수 대상으로 분리한다.
4. 자동 매핑과 수동 매핑을 구분한다.
5. 원본 분류 체계와 표준 분류 체계를 모두 보존한다.

### 8.4 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `event_category_id` | 이벤트 카테고리 ID |
| `event_category_name` | 이벤트 카테고리 이름 |
| `mapped_category_id` | 표준 카테고리 ID |
| `mapped_category_path` | 표준 카테고리 경로 |
| `mapping_type` | `EXACT`, `PARTIAL`, `MANUAL`, `UNMAPPED` |
| `confidence` | 매핑 신뢰도 |
| `review_status` | 검수 상태 |
| `note` | 비고 |

---

## 9. `period_dictionary.csv`

### 9.1 역할

`period_dictionary.csv`는 시대명과 기간을 정규화하기 위한 사전이다. 최종적으로 `Period` 노드의 기준 목록이 된다.

원본에는 시대 정보가 여러 컬럼에 흩어져 있다.

```text
history_terms.term_times
history_terms.term_year
itkc_events.period
itkc_events.event_date
```

이 값들은 형태가 다르다.

```text
고려
고려전기
조선후기
삼국시대-조선시대
1218년(고종 5) 12월
```

### 9.2 왜 필요한가

1. `Period` 노드의 기준 목록이 된다.
2. 시대명을 표준화한다.
3. `IN_PERIOD` 관계 생성 기준이 된다.
4. 같은 시대 오답 후보를 찾을 수 있다.
5. 연도 범위 검색과 시대 검색을 함께 사용할 수 있다.

### 9.3 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `period_id` | 시대 고유 ID |
| `period_name` | 표준 시대명 |
| `period_level` | 시대 계층 수준 |
| `start_year` | 시작 연도 |
| `end_year` | 종료 연도 |
| `source` | 생성 근거 |
| `review_status` | 검수 상태 |
| `note` | 비고 |

---

## 10. `event_date_parse.csv`

### 10.1 역할

`event_date_parse.csv`는 dictionary가 아니라 날짜 정규화 staging이다. `event_date` 원문에서 연도, 월, 왕대 표현을 뽑아 Event 노드 속성과 Period 연결에 쓰기 위한 파일이다.

예를 들어 다음 원문이 있다.

```text
1218년(고종 5) 12월\r\n~ 1219년(고종 6) 1월
```

여기서 최소한 다음 정보를 뽑을 수 있다.

```text
start_year = 1218
end_year = 1219
start_month = 12
end_month = 1
start_reign_name = 고종
start_reign_year = 5
end_reign_name = 고종
end_reign_year = 6
date_precision = YEAR_MONTH_RANGE
```

### 10.2 지금 해야 하는 이유

날짜 정규화는 나중으로 미루면 Event와 Period 연결이 약해진다. 이 프로젝트에서는 MVP에서도 시대별 사건 검색, 같은 시기 오답 후보, 사건 정렬이 필요하므로 최소 파싱은 지금 하는 것이 좋다.

단, 지금 모든 것을 완벽하게 할 필요는 없다.

1차 목표는 다음 정도다.

- 원문 보존
- 시작 연도 추출
- 종료 연도 추출
- 월 추출
- 왕대 표현 문자열 보존
- 날짜 정밀도 부여
- 파싱 실패 항목 분리

음력/양력 변환이나 왕대 연도 역산은 1차 범위에서 제외해도 된다.

### 10.3 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `event_id` | 사건 ID |
| `date_text` | 원본 날짜 문자열 |
| `start_year` | 시작 연도 |
| `end_year` | 종료 연도 |
| `start_month` | 시작 월 |
| `end_month` | 종료 월 |
| `start_reign_name` | 시작 왕대명 |
| `start_reign_year` | 시작 왕대 연도 |
| `end_reign_name` | 종료 왕대명 |
| `end_reign_year` | 종료 왕대 연도 |
| `date_precision` | 날짜 정밀도 |
| `parse_status` | 파싱 상태 |

---

## 11. `relation_type_dictionary.csv`

### 11.1 역할

`relation_type_dictionary.csv`는 `itkc_person_relations.csv.relation_type`을 그래프에서 쓸 수 있는 의미 규칙으로 바꾸는 사전이다.

원본 관계 유형은 다음처럼 짧은 문자열이다.

```text
형제, 자, 부, 조부, 장인, 사위, 증조부, 교유, 스승, 제자, 생부, 출자, 아내, 남편, 모, 생모
```

사람은 `부`를 보면 아버지라는 것을 알지만, 코드와 DB는 다음 판단을 자동으로 알 수 없다.

- 관계 방향이 `person_id -> related_person_id`인지
- 대칭 관계인지
- 역관계가 무엇인지
- 가족 관계인지 사회 관계인지
- 동세대 관계인지 윗세대/아랫세대 관계인지
- 문제 생성이나 챗봇에서 어떤 그룹으로 묶어야 하는지

따라서 relation type 사전이 필요하다.

### 11.2 왜 필요한가

이 사전이 없으면 전처리, Neo4j import, 챗봇 쿼리, 문제 생성 로직에서 같은 판단을 반복해야 한다.

예를 들어 코드 곳곳에 다음과 같은 조건이 늘어난다.

```text
부이면 아버지
자이면 자식
형제이면 대칭 관계
교유이면 사회 관계
스승이면 사제 관계
```

이런 판단을 코드에 하드코딩하면 수정과 검수가 어려워진다. 사전으로 분리하면 원본 관계값을 표준 의미로 조인해서 사용할 수 있다.

### 11.3 관계 방향과 대칭성

예시는 다음과 같다.

| raw_relation_type | normalized_relation_type | relation_group | direction_rule | is_symmetric | inverse_relation_type |
|---|---|---|---|---|---|
| `부` | `HAS_FATHER` | `FAMILY_PARENT` | `person_to_related` | `N` | `HAS_CHILD` |
| `자` | `HAS_CHILD` | `FAMILY_CHILD` | `person_to_related` | `N` | `HAS_PARENT` |
| `형제` | `SIBLING_OF` | `FAMILY_SIBLING` | `undirected` | `Y` | `SIBLING_OF` |
| `교유` | `ASSOCIATED_WITH` | `SOCIAL` | `undirected` | `Y` | `ASSOCIATED_WITH` |
| `스승` | `HAS_TEACHER` | `SOCIAL_TEACHER` | `person_to_related` | `N` | `HAS_STUDENT` |
| `제자` | `HAS_STUDENT` | `SOCIAL_STUDENT` | `person_to_related` | `N` | `HAS_TEACHER` |
| `아내` | `HAS_WIFE` | `SPOUSE` | `person_to_related` | `N` | `HAS_HUSBAND` |
| `남편` | `HAS_HUSBAND` | `SPOUSE` | `person_to_related` | `N` | `HAS_WIFE` |

`HAS_FATHER`와 `SIBLING_OF`는 반대 관계가 아니다. `HAS_FATHER`는 세대 관계이고, `SIBLING_OF`는 동세대 관계다.

반대 관계 예시는 다음과 같다.

```text
HAS_FATHER <-> HAS_CHILD
HAS_TEACHER <-> HAS_STUDENT
HAS_WIFE <-> HAS_HUSBAND
SIBLING_OF <-> SIBLING_OF
```

### 11.4 Neo4j MVP 적용 방식

MVP에서는 관계 타입을 너무 많이 나누지 않고 `RELATED_TO` 하나로 넣은 뒤 속성으로 정규화 의미를 유지하는 방식이 좋다.

```text
(:Person)-[:RELATED_TO {
  raw_relation_type: "부",
  normalized_relation_type: "HAS_FATHER",
  relation_group: "FAMILY_PARENT",
  is_symmetric: false,
  inverse_relation_type: "HAS_CHILD"
}]->(:Person)
```

이렇게 하면 다음 질의가 쉬워진다.

- 가족 관계만 찾기
- 형제 같은 동세대 관계 찾기
- 교유 같은 사회 관계 찾기
- 스승/제자 관계 찾기
- 윗세대/아랫세대 관계 구분하기

---

## 12. `source_url_dictionary.csv`

### 12.1 역할

`source_url_dictionary.csv`는 RAG와 출처 추적을 위한 URL 사전이다.

현재 URL 사전 대상은 다음 세 가지다.

```text
events.source_urls
event_relations.source_urls
person_relations.detail_url
```

`person_relations.evidence_url`은 URL 사전에 넣지 않고 `RELATED_TO.evidence_url` 관계 속성으로만 보존한다. 인물 관계 근거 URL을 `SourceUrl` 노드로 승격하면 같은 URL 하나가 많은 인물 관계를 묶는 허브가 될 수 있기 때문이다.

URL은 그래프 구조 자체에는 필수는 아니지만, 답변의 근거와 RAG 품질에는 중요하다.

### 12.2 왜 필요한가

1. 사건 URL과 인물 상세 URL의 중복을 제거한다.
2. URL이 어떤 테이블과 컬럼에서 왔는지 추적한다.
3. Tavily extract 대상 URL queue로 쓸 수 있다.
4. `Evidence`, `Source`, `DocumentChunk` 확장에 사용할 수 있다.
5. 챗봇 답변에 출처 URL을 붙일 수 있다.

### 12.3 Hybrid RAG에서의 역할

최종 RAG 구조는 다음처럼 볼 수 있다.

```text
Neo4j = 사실 관계의 뼈대
Vector index = 설명문과 URL 본문 검색
Tavily = URL 본문 추출과 외부 웹 근거 보강
LLM = 그래프 결과와 문서 근거를 종합한 답변 생성
```

Tavily는 그래프의 확정 관계를 만드는 주 데이터가 아니라, URL 기반 근거 수집과 답변 보강 레이어로 둔다.

### 12.4 주요 컬럼

| 컬럼 | 의미 |
|---|---|
| `source_url_id` | URL 고유 ID |
| `url` | 원본 URL |
| `source_tables` | URL이 나온 테이블 목록 |
| `source_columns` | URL이 나온 컬럼 목록 |
| `source_types` | EVENT_DETAIL, EVENT_RELATION_DETAIL, PERSON_DETAIL 같은 URL 출처 유형 |
| `source_count` | 같은 URL이 수집 대상 컬럼에서 등장한 횟수 |
| `use_for_rag` | RAG 수집 대상 여부 |
| `fetch_status` | Tavily 수집 상태 |
| `note` | 수집/검수 메모 |

---

## 13. 구축 우선순위

사전과 staging 파일은 다음 순서로 만드는 것이 좋다.

1. `canonical_category_dictionary.csv`
2. `term_canonical_category_relation.csv`
3. `source_event_category_dictionary.csv`
4. `event_source_category_relation.csv`
5. `relation_type_dictionary.csv`
6. `event_date_parse.csv`
7. `period_dictionary.csv`
8. `source_url_dictionary.csv`
9. `taxonomy_crosswalk.csv`

`taxonomy_crosswalk.csv`는 중요하지만 가장 먼저 만들면 어렵다. 이벤트 카테고리와 표준 카테고리 사전이 먼저 있어야 매핑 후보를 만들 수 있다.

---

## 14. 결론

Neo4j 그래프 구축에서 사전은 부가물이 아니라 그래프 품질을 결정하는 기준표다.

각 파일의 핵심 역할은 다음과 같다.

- `canonical_category_dictionary.csv`: 표준 카테고리 노드 기준
- `term_canonical_category_relation.csv`: 용어와 카테고리 연결
- `source_event_category_dictionary.csv`: 이벤트 원본 분류 보존
- `event_source_category_relation.csv`: 사건과 이벤트 분류 연결
- `taxonomy_crosswalk.csv`: 이벤트 분류와 표준 카테고리 연결
- `period_dictionary.csv`: 시대 노드 기준
- `event_date_parse.csv`: 사건 날짜 정규화
- `relation_type_dictionary.csv`: 인물 관계 의미 규칙
- `source_url_dictionary.csv`: RAG와 출처 추적 기준

이 구분을 해두면 Neo4j import, 검수, 문제 생성, Hybrid RAG 확장을 모두 같은 흐름 안에서 관리할 수 있다.

---

## 15. 2026-07-03 정리 기준

### 15.1 dictionary와 mapping 분리

`dictionary/`에는 노드의 기준표가 되는 CSV만 둔다.

예시는 다음과 같다.

- `canonical_category_dictionary.csv`
- `source_event_category_dictionary.csv`
- `period_dictionary.csv`
- `relation_type_dictionary.csv`
- `source_url_dictionary.csv`
- `event_facet_dictionary.csv`
- `country_dictionary.csv`
- `region_dictionary.csv`
- `economic_domain_dictionary.csv`
- `taxonomy_facet_dictionary.csv`

`mapping/`에는 서로 다른 기준표를 연결하는 crosswalk CSV를 둔다.

예시는 다음과 같다.

- `taxonomy_crosswalk.csv`
- `source_event_category_facet_crosswalk.csv`
- `canonical_category_country_crosswalk.csv`
- `canonical_category_region_crosswalk.csv`
- `canonical_category_economic_domain_crosswalk.csv`
- `canonical_category_taxonomy_facet_crosswalk.csv`

이렇게 나누는 이유는 사전과 매핑표의 책임이 다르기 때문이다.

사전은 그래프에 들어갈 노드 후보를 정의한다. 매핑표는 이미 만들어진 노드 후보 사이의 연결 규칙을 정의한다. 두 종류가 같은 폴더에 있으면 파일 수가 많아 보이고, 어떤 파일을 검수해야 하는지 판단하기 어려워진다.

### 15.2 국가와 지역은 카테고리 하위 개념이 아니다

원본 `history_terms.term_lk`에는 다음과 같은 경로가 존재한다.

```text
외교·국제관계 > 러시아 > 경제·산업(러시아)
외교·국제관계 > 기타지역 > 동남아시아
```

이 경로는 원본 분류 체계에서 사용한 탐색 경로다. 하지만 Neo4j 의미 그래프에서는 `러시아`, `기타지역`, `동남아시아`를 `외교·국제관계`의 개념적 하위 카테고리로 해석하면 안 된다.

따라서 원본 경로는 `canonical_category_dictionary.csv`와 staging relation에 보존하되, 최종 그래프의 `SUBCATEGORY_OF` 관계에서는 국가/지역 facet으로 분리된 경로를 제외한다.

의미 그래프에서는 다음 관계를 사용한다.

```text
CanonicalCategory -[:ABOUT_COUNTRY]-> Country
CanonicalCategory -[:ABOUT_REGION]-> Region
Term -[:ABOUT_COUNTRY]-> Country
Term -[:ABOUT_REGION]-> Region
Event -[:ABOUT_COUNTRY]-> Country
Event -[:ABOUT_REGION]-> Region
```

즉, `러시아`는 `외교·국제관계`의 하위 카테고리가 아니라 해당 카테고리 경로가 다루는 국가 facet이다. `기타지역`은 실제 국가가 아니라 원본 taxonomy의 지역 묶음 버킷이며, `동남아시아`, `아메리카`, `유럽` 등은 별도 `Region` 노드로 다룬다.

### 15.3 시대 범위는 전처리에서 확장한다

`history_terms.term_times`에는 다음처럼 시작 시대와 끝 시대가 하나의 문자열로 들어간 경우가 있다.

```text
삼국시대-조선시대
고려후기-조선후기
개항기-현대
```

이 값은 쿼리 시점에 매번 해석하지 않는다. Cypher에서 문자열을 다시 파싱하면 쿼리가 복잡해지고, 시대 순서 기준이 여러 곳에 흩어진다.

따라서 시대 순서와 범위 확장 기준은 `seed/period_seed.csv`에 두고, 최종 관계 CSV를 만들 때 `term_in_period.csv`, `event_in_period.csv`에 미리 펼쳐 저장한다.

예를 들어 다음 원문은:

```text
삼국시대-조선시대
```

다음 관계로 확장된다.

| period_name | match_type |
|---|---|
| 삼국시대 | `RANGE_START` |
| 남북국시대 | `RANGE_MIDDLE` |
| 후삼국시대 | `RANGE_MIDDLE` |
| 고려시대 | `RANGE_MIDDLE` |
| 조선시대 | `RANGE_END` |

`match_type`은 다음 의미를 가진다.

| match_type | 의미 |
|---|---|
| `DIRECT` | 원문에 단일 시대가 직접 적힌 경우 |
| `RANGE_START` | 범위 표현의 시작 시대 |
| `RANGE_MIDDLE` | seed의 시대 순서로 추론한 중간 시대 |
| `RANGE_END` | 범위 표현의 끝 시대 |

범위 확장은 `range_group`이 같은 시대끼리만 수행한다. 예를 들어 `korean_major_period`는 `삼국시대`, `남북국시대`, `고려시대`, `조선시대` 같은 주요 한국사 시대를 확장하고, `archaeological_period`는 `구석기시대`, `신석기시대`, `청동기시대`, `초기철기시대`를 별도 순서로 확장한다.

이렇게 하면 조회 쿼리는 단순해진다.

```cypher
MATCH (t:Term)-[r:IN_PERIOD]->(p:Period {name: "고려시대"})
RETURN t, r.match_type
```

쿼리 결과에서 `match_type`을 보면 원문에 직접 있던 시대인지, 범위에서 추론된 중간 시대인지 구분할 수 있다.

---

## 15. 이벤트 분류와 용어 카테고리를 바로 합치지 않는 이유

`history_terms.term_lk`와 `events.subject_category`는 모두 카테고리처럼 보이지만 같은 성격의 데이터가 아니다.

`term_lk`는 시소러스 기반 계층형 분류다.

```text
정치·행정·법제>행정>중앙행정기구
문화·예술>음악
```

`>`는 depth를 뜻하고, `>>`는 한 용어가 여러 분류 경로에 속한다는 뜻이다. 그래서 `CanonicalCategory`와 `SUBCATEGORY_OF` 계층을 만들 수 있다.

반면 `events.subject_category`는 사건 수집 과정에서 붙은 평면 분류다.

```text
전쟁
반란
정치인
```

계층이 없고, 쉼표나 줄바꿈으로 복수값이 섞여 있으며, `term_lk`와 같은 명명 규칙을 쓰지 않는다. 따라서 `전쟁 = 국방·군사`처럼 의미상 연결되는 경우도 문자열만으로는 안전하게 합칠 수 없다.

그래서 다음 사전과 매핑을 둔다.

| 파일 | 필요한 이유 |
|---|---|
| `source_event_category_dictionary.csv` | 이벤트 원본 분류를 손실 없이 보존 |
| `canonical_category_dictionary.csv` | 용어집 기반 표준 카테고리 기준점 |
| `taxonomy_crosswalk.csv` | 서로 다른 두 분류 체계를 연결하는 검수 가능한 매핑표 |

이 방식은 원본 보존과 표준 검색을 동시에 만족한다. 매핑이 틀렸다면 원본 데이터를 고치지 않고 `taxonomy_crosswalk_seed.csv`만 수정하면 된다.

## 16. EventFacet과 SearchTag가 겹쳐도 둘 다 필요한 이유

`EventFacet`과 `SearchTag`에는 `전쟁`, `정치`, `제도`처럼 겹치는 값이 있을 수 있다. 하지만 둘의 목적은 다르다.

| 구분 | 목적 |
|---|---|
| `EventFacet` | 사건의 의미 성격을 정규화한 축 |
| `SearchTag` | 검색 쿼리를 단순하게 만들기 위한 통합 태그 축 |

`EventFacet`은 의미 모델이다. “이 사건은 전쟁 성격이다”처럼 사건 분류를 재분류한다.

`SearchTag`는 검색 최적화 레이어다. 사용자가 “전쟁 관련 항목”을 찾을 때 용어/사건/인물 이름, 원본 분류, 표준 카테고리, facet, 시대, 주제, 국가, 지역, taxonomy facet을 모두 `OR`로 탐색하면 쿼리가 길어진다. `SearchTag`를 두면 다음처럼 한 관계로 조회할 수 있다.

```cypher
MATCH (n)-[:HAS_SEARCH_TAG]->(:SearchTag {tag_name: "전쟁"})
RETURN DISTINCT n
```

대신 태그가 어디서 왔는지 잃지 않도록 `HAS_SEARCH_TAG` 관계에 `source_node_type`, `source_node_id`, `source_relation`, `source_detail`을 남긴다.
Person 별칭은 `source_node_type=PersonAlias`, `source_relation=person_alias`로 분리하고, Person이 Event/Term 태그를 상속받은 경우 `source_detail`에 원천 `event_id` 또는 `term_id` 묶음을 보존한다.
같은 노드가 여러 태그 출처로 조회될 수 있으므로 SearchTag 조회 결과는 `RETURN DISTINCT`로 받는다.

정리하면 다음과 같다.

- 정밀한 의미 분석: `SourceEventCategory`, `CanonicalCategory`, `EventFacet`
- 빠른 키워드 검색: `SearchTag`
- 매핑 기준 수정: seed/crosswalk 수정 후 CSV 재생성


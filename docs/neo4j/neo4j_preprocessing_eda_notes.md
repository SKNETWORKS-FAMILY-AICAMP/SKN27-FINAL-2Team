# Neo4j 전처리 EDA 정리

이 문서는 `test/MK/prep_neo4j` 아래의 check 계열 노트북 하단에 정리해둔 전처리 EDA Markdown 블록을 문서화한 것이다.

## 원본 노트북

- `check_terms.ipynb`
- `ckeck_event.ipynb`
- `check_people.ipynb`

---

## check_terms.ipynb

원본 위치: `test\MK\prep_neo4j\check_terms.ipynb`

<!-- source_cell: check_terms.ipynb[11] -->

## EDA 정리: `term_kind`, `topterm_id`, `term_lk` 구조 해석

현재 확인한 기준으로 `term_kind`는 행의 역할을 나타내는 값으로 본다.

- `term_kind = 0`: 원본 최상위 대분류 카테고리
- `term_kind = 1`: 하위 분류/태그/색인어 후보
- `term_kind = 2`: 실제 역사 용어 본문 항목

`topterm_id`는 직접 부모 ID라기보다, 해당 행이 속한 최상위 대분류의 `term_id`로 보인다. `topterm_id.value_counts()` 결과가 17개이고, 이 값들이 `term_kind = 0`인 대분류의 `term_id`와 일치하기 때문이다.

예를 들면 `정치·행정·법제`는 `term_kind = 0`, `term_id = 8`, `topterm_id = 8`이고, 그 아래의 하위 분류나 실제 용어는 `topterm_id = 8`을 공유한다. 따라서 `topterm_id`만으로는 `범죄` 같은 하위 분류 안에 어떤 실제 용어가 들어가는지 알 수 없다.

실제 용어의 상세 분류 경로는 `term_lk`에 들어 있다. 예시는 다음과 같다.

```text
정치·행정·법제>인사
정치·행정·법제>행정>중앙행정기구
사회·생활>풍속·의례>풍속>>사회·생활>일상생활>의생활
```

`>`는 계층 구분자로 보고, `>>`는 한 용어가 여러 분류 경로를 가질 때의 복수 경로 구분자로 보는 것이 자연스럽다.

따라서 1차 전처리에서는 다음 흐름이 적절하다.

1. `term_kind = 0`으로 원본 대분류 17개를 확보한다.
2. `term_lk`를 `>>` 기준으로 먼저 분리해 복수 분류 경로를 나눈다.
3. 각 분류 경로를 `>` 기준으로 다시 분해한다.
4. 분해된 경로 조각으로 `CanonicalCategory` 후보를 만든다.
5. 실제 용어인 `term_kind = 2` 행을 해당 `CanonicalCategory`에 연결한다.
6. `term_kind = 1`은 직접 부모 관계로 확정하지 않고, `term_lk`에서 만든 카테고리 후보를 검증하거나 보강하는 참고 데이터로 사용한다.

`범죄` 하위 분류의 실제 용어를 확인하려면 `topterm_id`만 보지 말고 `term_lk` 또는 `term_remark`를 같이 확인해야 한다.

```python
df[
    (df["term_kind"] == 2) &
    (df["topterm_id"] == 8) &
    (df["term_lk"].fillna("").str.contains("범죄"))
][["term_id", "term_name", "topterm_id", "term_lk", "term_remark", "term_desc"]]
```

---

<!-- source_cell: check_terms.ipynb[12] -->

## EDA 정리: 1차 전처리 컬럼 사용 기준

현재까지 확인한 기준으로 `history_terms.csv`는 `Term`, `CanonicalCategory`, `Period` 후보를 뽑는 원천 데이터로 사용한다. 핵심은 실제 용어 행인 `term_kind = 2`를 중심으로 보고, 분류 체계는 `term_lk`를 `>>`와 `>`로 분해해서 만드는 것이다.

### 1. 컬럼 기반 추출 가능 데이터

| 뽑을 데이터 | 주로 쓰는 컬럼 |
|---|---|
| `Term` 노드 | `term_id`, `term_name`, `term_ch`, `term_desc` |
| 원본 대분류 `CanonicalCategory`의 root | `term_kind=0`, `term_id`, `topterm_id`, `term_name` |
| 상세 `CanonicalCategory` | `term_lk`를 `>>`, `>`로 분해 |
| `Term - HAS_CANONICAL_CATEGORY - CanonicalCategory` 관계 | `term_id`, `term_lk` |
| 시대/기간 후보 `Period` | `term_times`, `term_year` |
| `start_year`, `end_year`, `date_precision` | `term_year` |
| `Term - IN_PERIOD / INFERRED_IN_PERIOD - Period` | `term_times`, `term_year`, 나중에 `term_desc` |
| 엔티티 타입 후보 | `topterm_id`, `term_lk`, `term_name`, `term_desc` |
| 주제/키워드 후보 | `term_lk`, `term_desc` |
| 중복/별칭 검수 후보 | `term_name`, `term_ch`, `term_remark`, `term_desc` |
| 장소 후보 | `topterm_id=663`, `term_lk=지명`, `term_desc` |
| 서명/문헌 후보 | `topterm_id=664`, `term_lk=서명` |
| 문화재 후보 | `topterm_id=665`, `term_lk=문화재` |
| 관직/기관 후보 | `term_lk`의 `인사`, `중앙행정기구`, `기관`, `term_desc` |
| 검수 후보 | 중복 이름, 복수 `term_lk`, 불명확한 `term_year`, 애매한 `term_remark` |

### 2. Neo4j 1차 MVP에 사용할 컬럼

| 원본 컬럼 | 사용 방향 | 설명 |
|---|---|---|
| `term_id` | 사용 | `Term` 노드의 고유 ID로 사용한다. `term_name` 중복이 많으므로 ID 기준이 필요하다. |
| `topterm_id` | 사용 | 원본 최상위 대분류 연결 기준이다. `term_kind = 0`의 `term_id`와 연결된다. |
| `term_name` | 사용 | `Term.name`으로 사용한다. 단, 고유키로 쓰지 않는다. |
| `term_kind` | 사용 | 행의 역할 구분값이다. `0`은 대분류, `1`은 하위 분류/태그 후보, `2`는 실제 용어다. |
| `term_ch` | 사용 | 한자/원문 표기이므로 `Term.hanja` 속성으로 넣는다. |
| `term_remark` | 보조 사용 | 동명이인, 세부 구분, 검수 단서로 사용한다. 핵심 관계 기준은 아니다. |
| `term_year` | 사용 | `year_text`로 보존하고, 이후 `start_year`, `end_year`, `date_precision` 파싱에 사용한다. |
| `term_times` | 사용 | `period_text`로 보존하고, `Period` 후보 및 `Term - IN_PERIOD` 후보 생성에 사용한다. |
| `term_lk` | 핵심 사용 | 카테고리 경로다. `>>`는 복수 경로, `>`는 계층 구분자로 사용한다. |
| `term_desc` | 사용 | 설명문이다. 검색/RAG, 주제 추론, 장소/기관/인물 후보 추출에 사용한다. |

### 3. 1차 MVP에서 제외하거나 원본 메타로만 둘 컬럼

| 원본 컬럼 | 판단 | 이유 |
|---|---|---|
| `term_attr` | 1차 제외 | 결측이 많고 현재 의미가 불명확하다. |
| `term_user` | 제외 | 원본 시스템의 입력자/관리자 ID로 보인다. 역사 지식 의미와 무관하다. |
| `term_created` | 제외 또는 원본 메타 보존 | 역사적 시점이 아니라 원본 DB 등록 시각이다. |
| `term_reference` | 선택 보존 | 출처 메타데이터다. 1차 카테고리/용어 그래프에는 필수는 아니지만 나중에 `Source` 노드나 답변 근거에 쓸 수 있다. |

### 4. `term_lk` 기반 카테고리 세분화 원칙

```text
>> : 복수 카테고리 경로 구분자
>  : 단일 경로 안의 계층 구분자
```

예시:

```text
사회·생활>풍속·의례>풍속>>사회·생활>일상생활>의생활
```

분해 결과:

```text
1번 경로: 사회·생활 > 풍속·의례 > 풍속
2번 경로: 사회·생활 > 일상생활 > 의생활
```

따라서 `Term`은 각 경로의 마지막 `CanonicalCategory`와 `HAS_CANONICAL_CATEGORY`로 연결하고, `CanonicalCategory`끼리는 `SUBCATEGORY_OF`로 연결한다.

### 5. 지금 단계에서 만들 수 있는 산출물

- `normalized/terms.csv`: 실제 용어 행 정규화 결과
- `canonical_category_dictionary.csv`: `term_lk`를 분해해서 만든 카테고리 노드 후보
- `term_canonical_category_relation.csv`: `Term`과 최하위 `CanonicalCategory` 연결
- `period_dictionary.csv` 후보: `term_times`, `term_year` 기반 시대 후보
- `term_period_relation.csv` 후보: `Term`과 `Period` 연결
- `review_candidates.csv`: 중복 이름, 복수 카테고리, 불명확한 연도, 애매한 `term_remark` 검수 후보

### 6. 1차 Neo4j MVP 구조

노드:

```text
Term
Category
TopCategory
Period
```

관계:

```text
Term - HAS_CANONICAL_CATEGORY -> CanonicalCategory
CanonicalCategory - SUBCATEGORY_OF -> CanonicalCategory
Term - IN_PERIOD 또는 INFERRED_IN_PERIOD -> Period
```

현재 EDA 결론상 다음 작업은 `term_lk` 분해 로직을 먼저 만들고, 그 결과로 `canonical_category_dictionary.csv`와 `term_canonical_category_relation.csv`를 만드는 것이다.

---

## ckeck_event.ipynb

원본 위치: `test\MK\prep_neo4j\ckeck_event.ipynb`

<!-- source_cell: ckeck_event.ipynb[24] -->

## EDA 정리: event 데이터 중복과 `scope` 해석

현재 `df1`은 `itkc_events.csv`에서 읽은 사건 기본 정보이고, `df2`는 `itkc_event_relations.csv`에서 읽은 사건-인물 관계 정보다.

### 1. `scope` 해석

`df1.scope`는 최종 `Event` 노드의 역사 속성이라기보다, 같은 사건이 어떤 수집 경로에서 발견되었는지를 나타내는 값으로 본다.

```text
event_subject : 주제/분류 기준 수집 경로
event_period  : 시대 기준 수집 경로
```

현재 분포는 다음처럼 600개씩 동일하다.

```text
event_subject    600
event_period     600
```

즉 같은 사건이 `event_subject`, `event_period` 두 경로에서 중복 수집된 구조로 보인다.

### 2. `event_id` 기준 중복 제거 가능 여부 확인

`event_id`만 보고 바로 중복 제거하면 정보가 사라질 수 있으므로, 먼저 같은 `event_id` 안에서 `scope` 외의 값이 달라지는지 확인한다.

```python
compare_cols = [col for col in df1.columns if col not in ["event_id", "scope"]]

diff_check = (
    df1
    .groupby("event_id")[compare_cols]
    .nunique(dropna=False)
)

diff_rows = diff_check[diff_check.gt(1).any(axis=1)]
diff_rows
```

이 코드의 의미는 다음과 같다.

- `event_id`, `scope`를 제외한 컬럼만 비교한다.
- `event_id`별로 각 컬럼의 고유값 개수를 센다.
- 어떤 컬럼이라도 고유값 개수가 2개 이상이면 `diff_rows`에 남긴다.
- `diff_rows`가 비어 있으면 같은 `event_id` 안에서 다른 값은 `scope`뿐이라는 뜻이다.

현재 확인 결과는 `event_name`, `subject_category`, `period`, `event_date`, `person_count`, `related_event`는 모두 고유값 개수가 `1`이고, `detail_url`만 고유값 개수가 `2`로 보인다.

즉 같은 `event_id` 안에서 사건 내용 자체는 동일하고, 수집 경로별 상세 URL만 다르다. 따라서 `event_id` 기준으로 Event를 1개만 남기되, `detail_url`은 버리기보다 여러 출처 URL을 `source_urls`로 합쳐 보존하는 방식이 적절하다.

`df1.drop_duplicates(subset=["event_id"])`와 `df1.drop_duplicates(subset=["event_id", "detail_url"])`의 결과 수가 다른 이유도 이 때문이다. 같은 사건이라도 `detail_url`이 2개라서, `event_id + detail_url` 기준으로 보면 서로 다른 행으로 남는다.

### 3. 최종 Event 노드 컬럼 판단

최종 `Event` 노드에는 `scope`를 넣지 않는다. `scope`는 수집 경로 메타데이터이고, 사건 자체의 속성은 아니기 때문이다.

1차 Event 노드 기준 컬럼 판단은 다음과 같다.

| 컬럼 | 의미 | 처리 | 사용 방식/이유 |
|---|---|---|---|
| `scope` | 수집 경로 | 제외 | `event_subject`, `event_period`는 사건 속성이 아니라 수집 route라서 최종 Event 노드에는 넣지 않는다. |
| `event_id` | 사건 고유 ID | 사용 | Event 노드의 primary key 역할. 중복 제거 기준도 `event_id`로 둔다. |
| `event_name` | 사건명 | 사용 | Event 노드의 `name` 속성으로 사용한다. |
| `subject_category` | 사건 주제 분류 원문 | 사용 | 바로 표준 CanonicalCategory로 합치지 않고, `SourceEventCategory` 또는 매핑 후보로 사용한다. |
| `period` | 시대 원문 | 사용 | `Period` 노드 연결 후보로 사용하되, 원문 값도 보존한다. |
| `event_date` | 날짜/기간 원문 | 사용 | 연도, 월, 왕대 파싱 후보로 사용한다. 파싱 전에는 원문 속성으로 보존한다. |
| `person_count` | 관련 인물 수 | 제외 가능 | `df2`의 `event_id`, `person_id` 관계에서 다시 계산할 수 있으므로 원본 count에 의존하지 않는다. |
| `related_event` | 관련 사건명/사건 묶음 | 사용 | 같은 전쟁, 옥사, 사건군을 묶는 `EventGroup` 후보로 사용한다. |
| `detail_url` | 상세 페이지 URL | `source_urls`로 보존 | 같은 `event_id`에 URL이 2개 있으므로 단일 값으로 고르지 않고 합쳐서 출처 추적용으로 둔다. |

추천 정리 코드는 다음과 같다.

```python
event_source_url = (
    df1
    .groupby("event_id")["detail_url"]
    .apply(lambda x: "|".join(sorted(x.dropna().unique())))
    .reset_index(name="source_urls")
)

event_df = (
    df1
    .drop_duplicates(subset=["event_id"])
    .drop(columns=["scope", "person_count", "detail_url"])
    .merge(event_source_url, on="event_id", how="left")
)
```

`detail_url`을 그대로 남기면 같은 사건에 대해 어떤 URL을 대표값으로 삼을지 애매해진다. 그래서 `source_urls`로 합친 뒤 Event 노드의 출처 속성으로 쓰는 편이 낫다.

### 4. `subject_category` 후속 처리

`subject_category`는 현재 고유값이 119개이고, 아래처럼 복수 분류가 문자열 안에 같이 들어간 경우가 있다.

```text
반란,\r\n\r\n정치인
정치일반,\r\n\r\n국왕
옥사,\r\n\r\n고변/탄핵
```

따라서 `event.csv.subject_category`는 바로 표준 카테고리로 쓰지 말고, 별도 `taxonomy_crosswalk.csv`에서 표준 `CanonicalCategory`로 매핑한다.

```text
event.csv.subject_category -> taxonomy_crosswalk.csv -> CanonicalCategory
```

즉, 이벤트 카테고리는 `history_terms.term_lk`와 직접 합치는 것이 아니라 공통 표준 카테고리 사전에 매핑하는 방식으로 처리한다.

### 5. Event 데이터의 Neo4j 사용 방향

현재 event 쪽에서 바로 만들 수 있는 데이터는 다음과 같다.

| 만들 데이터 | 주로 쓰는 컬럼 | 설명 |
|---|---|---|
| `Event` 노드 | `event_id`, `event_name`, `event_date`, `period`, `source_urls` | 사건의 기본 노드. 날짜와 시대는 파싱 전 원문도 보존한다. |
| `SourceEventCategory` 후보 | `subject_category` | 119개 원문 분류를 토큰화해서 이벤트 전용 카테고리 사전으로 만든다. |
| `Event - HAS_EVENT_CATEGORY - SourceEventCategory` | `event_id`, `subject_category` | 사건과 이벤트 분류의 직접 관계. |
| `SourceEventCategory - MAPPED_TO - CanonicalCategory` 후보 | `subject_category`, `history_terms.term_lk` 기반 canonical category dictionary | 이벤트 분류와 역사용어 표준 카테고리는 매핑표로 연결한다. |
| `Event - IN_PERIOD - Period` 후보 | `period`, `event_date` | `period`는 시대명, `event_date`는 상세 날짜/기간 파싱 후보로 사용한다. |
| `Event - PART_OF_EVENT_GROUP - EventGroup` 후보 | `related_event` | `고려거란전쟁`처럼 여러 사건을 묶는 상위 사건군 후보로 사용한다. |
| `Person - INVOLVED_IN - Event` 관계 | `df2.event_id`, `df2.person_id`, `relation_type` | 인물 관계는 `df1.person_count`가 아니라 `df2`에서 만든다. |

따라서 event 쪽 1차 결론은 다음과 같다.

- `scope`, `person_count`는 최종 Event 노드에서 제외한다.
- `detail_url`은 드랍하지 않고 `source_urls`로 묶어 출처 속성으로 보존한다.
- `subject_category`는 바로 `history_terms` 카테고리와 합치지 않고, 이벤트 카테고리 사전과 매핑표를 만든다.
- `period`, `event_date`는 아직 파싱하지 말고 원문을 보존한 뒤, 기간/왕대 사전이 생기면 `Period`와 연결한다.
- `related_event`는 단순 문자열 속성으로만 끝내지 말고 `EventGroup` 후보로 본다.
- person 관계는 `person_relations.csv` 또는 `itkc_event_relations.csv`를 파악한 뒤 최종 관계 사전을 만든다.

---

## check_people.ipynb

원본 위치: `test\MK\prep_neo4j\check_people.ipynb`

<!-- source_cell: check_people.ipynb[8] -->

## EDA 정리: `person_relations.csv` 인물 관계 데이터

현재 파일은 `itkc_person_relations.csv`이며, 한 행은 인물 1명의 속성이 아니라 **인물과 인물 사이의 관계 1개**로 본다.

```text
person_id 인물 -- relation_type --> related_person_id 인물
```

예를 들어 `P000002 각겸(覺謙)`이 여러 명과 `교유` 관계를 가지면, 이것을 한 row에 리스트로 묶지 않는다. Neo4j에서는 관계 여러 개로 표현하는 것이 자연스럽다.

```text
P000002 각겸 --교유--> P002331 권근
P000002 각겸 --교유--> P020378 석나암
P000002 각겸 --교유--> P039883 이색
```

따라서 이 데이터는 `Person` 노드 생성용이라기보다 `Person - RELATED_TO - Person` 관계 생성용으로 먼저 해석한다.

### 1. 컬럼별 의미와 사용 판단

| 컬럼 | 의미 | 처리 | 사용 방식/이유 |
|---|---|---|---|
| `person_id` | 시작 인물 ID | 사용 | 관계의 source Person. |
| `person_name` | 시작 인물 이름 | 보존/검수 | `person_id`의 이름 검수용. 정식 인물 속성은 `itkc_people.csv`와 비교한다. |
| `relation_type` | 관계 유형 | 사용 | `교유`, `부` 같은 관계 종류. 관계 타입 또는 관계 속성으로 사용한다. |
| `related_person_id` | 대상 인물 ID | 사용 | 관계의 target Person. |
| `related_person_name` | 대상 인물 이름 | 보존/검수 | `related_person_id`의 이름 검수용. |
| `related_birth_year` | 대상 인물 출생연도 | Person 보강 후보 | 관계 속성이 아니라 대상 Person의 속성 후보. |
| `related_death_year` | 대상 인물 사망연도 | Person 보강 후보 | 관계 속성이 아니라 대상 Person의 속성 후보. |
| `related_bonkwan` | 대상 인물 본관 | Person 보강 후보 | 관계 속성이 아니라 대상 Person의 속성 후보. |
| `related_father` | 대상 인물의 아버지 | Person 보강 후보 | 관계 속성이 아니라 대상 Person의 가족 정보 후보. |
| `related_count` | 관련 수 표시 | 선택 보존 | 의미가 관계 강도인지 대상 인물의 관련 수인지 애매하므로 핵심 관계 판단에는 쓰지 않는다. |
| `evidence_url` | 관계 근거 URL | 보존 | 관계의 근거 출처로 사용한다. RAG 근거 수집에도 활용 가능하다. |
| `detail_url` | 시작 인물 상세 URL | 보존 | Person 상세 출처 URL로 사용한다. |

### 2. 중복 제거 기준

관계 데이터에서는 같은 사람이 여러 명과 연결되는 것이 정상이다. 따라서 `person_id`만 기준으로 중복 제거하면 안 된다.

1차 중복 제거 기준은 다음처럼 잡는다.

```python
person_relation_df = df.drop_duplicates(
    subset=["person_id", "related_person_id", "relation_type"]
)
```

이 기준은 같은 시작 인물, 같은 대상 인물, 같은 관계 유형이 모두 같을 때만 중복으로 본다.

### 3. 관계 import용 컬럼

Neo4j 관계 CSV만 따로 만든다면 최소 컬럼은 다음 정도면 된다.

```python
person_relation_edges = person_relation_df[
    ["person_id", "related_person_id", "relation_type", "evidence_url"]
]
```

출처와 원본 값을 조금 더 보존하려면 다음처럼 가져간다.

```python
person_relation_edges = person_relation_df[
    [
        "person_id",
        "related_person_id",
        "relation_type",
        "related_count",
        "evidence_url",
        "detail_url",
    ]
]
```

`person_name`, `related_person_name`, `related_birth_year`, `related_death_year`, `related_bonkwan`, `related_father`는 관계 import용에서는 제외할 수 있다. 다만 버리는 것이 아니라 Person 노드 보강용 staging 데이터로 따로 볼 수 있다.

### 4. `relation_type='부'` 해석

`relation_type`이 `부`인 행은 보통 다음처럼 해석한다.

```text
person_id 인물의 아버지 = related_person_id 인물
```

예를 들어 `각안(覺岸)`의 `부`가 `최철(崔徹)`로 나와도 바로 오류로 보지 않는다. `각안`은 성명이 아니라 법명/승려명일 수 있기 때문에 겉으로 보이는 성이 다를 수 있다.

따라서 성씨 불일치만으로 관계를 제거하지 않고, 검수 후보 정도로만 본다.

### 5. URL과 RAG 활용

`evidence_url`, `detail_url`은 그래프 구조 자체에는 필수는 아니지만, RAG와 검증 가능성을 위해 보존하는 것이 좋다.

| URL 컬럼 | 사용 방향 |
|---|---|
| `evidence_url` | 인물 관계의 근거 URL. 관계 속성 또는 Evidence 노드 후보. |
| `detail_url` | 시작 인물 상세 페이지 URL. Person 노드의 source URL 후보. |

Tavily를 쓰면 이 URL들의 본문을 추출해서 RAG 근거로 사용할 수 있다. 다만 Tavily 결과를 바로 확정 관계로 넣기보다, URL 본문 추출과 답변 근거 보강용으로 쓰는 것이 안전하다.

```text
Neo4j = 인물/사건/용어의 구조적 관계
Vector RAG = 설명문, URL 본문, term_desc 검색
Tavily = URL 본문 추출과 외부 웹 근거 보강
```

따라서 이 프로젝트의 RAG 구조는 `Graph RAG + Vector RAG + Web RAG`를 섞은 Hybrid RAG로 볼 수 있다.

### 6. 1차 결론

- 한 행은 Person 노드 1개가 아니라 Person-Person 관계 1개다.
- 한 사람의 관련 인물을 한 row에 리스트로 묶지 않는다.
- 중복 제거는 `person_id`, `related_person_id`, `relation_type` 기준으로 한다.
- 관계 import용에서는 `person_name`, `related_person_name`, `related_birth_year`, `related_death_year`, `related_bonkwan`, `related_father`를 제외할 수 있다.
- 제외한 인물 속성 컬럼은 버리는 것이 아니라 Person 노드 보강용 staging으로 남긴다.
- `related_count`는 의미가 애매하므로 핵심 관계 판단에는 쓰지 않고 선택 보존한다.
- `evidence_url`, `detail_url`은 RAG와 출처 추적을 위해 보존한다.

---





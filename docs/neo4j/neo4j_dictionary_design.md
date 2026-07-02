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
| Dictionary | 표준 목록을 정하는 파일 | `category_dictionary.csv`, `event_category_dictionary.csv`, `relation_type_dictionary.csv` |
| Staging relation | 원본 데이터를 Neo4j 관계로 넣기 좋게 펼친 중간 파일 | `term_category_relation.csv`, `event_category_relation.csv` |
| Mapping | 서로 다른 분류 체계를 연결하는 파일 | `category_mapping.csv` |

정리하면 dictionary는 기준을 만들고, staging은 원본을 그래프 관계로 펼치며, mapping은 서로 다른 기준을 연결한다.

---

## 왜 이렇게까지 사전을 만드는가

사전을 만드는 이유는 단순히 CSV를 예쁘게 정리하기 위해서가 아니다. 이 프로젝트에서 사전은 그래프가 흔들리지 않게 만드는 기준표이자, 원본 데이터와 Neo4j 사이의 해석 규칙이다.

원본 CSV의 값은 사람이 읽기에는 충분하지만, 그래프 DB가 안정적으로 탐색하기에는 불완전하다. 예를 들어 사람은 `부`, `형제`, `교유`를 보고 의미를 이해할 수 있지만, Neo4j는 이 값이 부모 관계인지, 동세대 관계인지, 대칭 관계인지, 역관계가 무엇인지 알지 못한다. 마찬가지로 사람은 `정치·행정·법제>행정>중앙행정기구`를 보고 계층 구조를 이해하지만, DB 입장에서는 그냥 긴 문자열 하나다.

따라서 사전은 다음 질문에 대한 답을 미리 정해두는 작업이다.

- 이 원본 문자열은 그래프에서 어떤 노드가 되는가?
- 이 값은 어떤 표준 이름으로 볼 것인가?
- 이 관계는 어느 방향으로 연결해야 하는가?
- 이 관계는 대칭인가?
- 이 값은 원본 카테고리인가, 표준 카테고리인가?
- 이 날짜는 정확한 연도인가, 범위인가, 왕대 표현인가?
- 이 URL은 관계 근거인가, 상세 페이지인가, RAG 수집 대상인가?

즉, 사전은 원본 데이터를 해석하는 약속이다. 이 약속이 없으면 같은 원본을 두고도 전처리 코드, Neo4j import 코드, 챗봇 검색 코드, 문제 생성 코드가 서로 다르게 해석할 수 있다.

### 사전 없이 바로 Neo4j에 넣으면 생기는 문제

사전을 만들지 않고 원본 CSV를 바로 Neo4j에 넣을 수도 있다. 하지만 그 경우 문제는 import 시점이 아니라 조회와 확장 시점에 드러난다.

| 문제 | 사전 없이 처리할 때 | 사전이 있을 때 |
|---|---|---|
| 카테고리 탐색 | `term_lk` 문자열을 매번 split해야 한다. | `Category` 노드와 `SUBCATEGORY_OF` 관계를 탐색한다. |
| 복수 카테고리 | `>>`가 있는 값을 쿼리마다 다시 해석해야 한다. | staging 관계로 이미 펼쳐져 있다. |
| 이벤트 분류 | `전쟁`, `옥사`, `국왕`이 표준 카테고리와 어떻게 연결되는지 불명확하다. | `category_mapping.csv`에서 연결 기준을 관리한다. |
| 인물 관계 | `부`, `자`, `형제`의 방향과 의미를 쿼리마다 판단해야 한다. | `relation_type_dictionary.csv`에서 의미, 방향, 대칭성을 관리한다. |
| 날짜 검색 | `1218년(고종 5) 12월` 같은 문자열 검색에 머문다. | `start_year`, `end_year`, `date_precision`으로 필터링한다. |
| RAG 출처 | URL이 여러 컬럼에 흩어져 중복 수집된다. | `source_url_dictionary.csv`에서 URL과 출처를 관리한다. |
| 검수 | 어떤 규칙으로 관계가 생성됐는지 추적하기 어렵다. | source, mapping_type, review_status로 검수할 수 있다. |

결국 사전 없이도 1회성 조회는 가능하다. 하지만 같은 로직을 반복해서 쓰는 순간 쿼리는 길어지고, 코드에는 조건문이 늘어나고, 결과는 재현하기 어려워진다.

### 사전은 그래프의 품질 관리 장치다

Neo4j 그래프에서 중요한 것은 노드와 관계를 많이 만드는 것이 아니라, 같은 기준으로 일관되게 만드는 것이다. 사전은 이 일관성을 보장한다.

예를 들어 `relation_type = 부`인 행이 있다.

```text
person_id = A
relation_type = 부
related_person_id = B
```

사람은 이 행을 보고 `A의 아버지가 B`라고 해석한다. 하지만 이 해석을 코드마다 직접 넣으면 다음 문제가 생긴다.

```text
전처리 코드에서는 HAS_FATHER로 해석
Neo4j import에서는 PARENT_OF로 해석
챗봇 쿼리에서는 FAMILY로만 해석
문제 생성에서는 가족 관계에서 누락
```

이런 불일치를 막기 위해 `relation_type_dictionary.csv`에 한 번만 정의한다.

```text
부 -> HAS_FATHER
direction_rule = person_to_related
inverse_relation_type = HAS_CHILD
relation_group = FAMILY_PARENT
is_symmetric = N
```

이렇게 하면 모든 후속 단계가 같은 기준을 사용한다.

### 사전은 하드코딩을 줄인다

사전이 없으면 전처리 코드가 점점 조건문 중심으로 변한다.

```text
부이면 HAS_FATHER
자이면 HAS_CHILD
형제이면 SIBLING_OF
교유이면 ASSOCIATED_WITH
스승이면 HAS_TEACHER
```

이런 판단이 코드에 박히면 새로운 관계 유형이 추가될 때마다 코드를 수정해야 한다. 반면 사전으로 관리하면 CSV 행을 추가하거나 수정하는 방식으로 대응할 수 있다.

이 프로젝트의 코딩 원칙에서도 하드코딩을 줄이는 것이 중요하므로, 의미 규칙은 코드보다 사전에 두는 편이 맞다. 코드는 사전을 읽고 적용하는 역할만 맡는다.

### 사전은 원본 보존과 표준화를 동시에 가능하게 한다

사전을 만든다고 해서 원본 값을 버리는 것은 아니다. 오히려 원본을 더 잘 보존하기 위해 사전이 필요하다.

예를 들어 이벤트의 `subject_category`가 다음과 같다고 하자.

```text
반란,\r\n\r\n정치인
```

이 값을 바로 `Category`에 합쳐버리면 원본 이벤트 분류가 어떻게 생겼는지 흐려진다. 그래서 다음처럼 나눈다.

```text
event_category_dictionary.csv = 반란, 정치인이라는 원본 이벤트 분류 보존
category_dictionary.csv = history_terms 기반 표준 카테고리 보존
category_mapping.csv = 둘 사이의 연결 규칙 관리
```

이 구조에서는 원본 분류도 남고, 표준 분류도 남고, 둘을 어떻게 연결했는지도 남는다. 나중에 매핑이 틀렸다고 판단되면 원본 데이터를 다시 건드리지 않고 `category_mapping.csv`만 수정하면 된다.

### 사전은 문제 생성 품질에 직접 영향을 준다

이 프로젝트의 그래프는 단순 조회뿐 아니라 한국사 문제 생성에도 쓰인다. 문제 생성에서는 정답과 비슷하지만 틀린 오답 후보를 잘 고르는 것이 중요하다.

사전이 없으면 오답 후보는 단순 문자열 유사도나 키워드 검색에 의존하게 된다. 하지만 사전이 있으면 다음 기준을 함께 사용할 수 있다.

- 같은 표준 카테고리인가?
- 같은 상위 카테고리 아래에 있는가?
- 같은 시대인가?
- 같은 사건군과 연결되는가?
- 같은 인물 네트워크와 연결되는가?
- 같은 관계 그룹에 속하는가?

예를 들어 `회사령`의 오답 후보를 만들 때 단순히 이름이 비슷한 용어를 찾는 것보다, `일제 강점기`, `제도/정책`, `경제 통제` 같은 기준을 함께 쓰는 것이 훨씬 낫다. 이 기준을 가능하게 하는 것이 카테고리 사전, 시대 사전, 관계 사전이다.

### 사전은 Hybrid RAG의 필터 역할을 한다

RAG에서 vector 검색만 쓰면 의미적으로 비슷한 문서를 찾을 수는 있지만, 역사 문제에서는 시대와 분류가 틀리면 답변 품질이 크게 떨어진다.

사전이 있으면 RAG 검색 전에 그래프 필터를 걸 수 있다.

```text
1. 질문에서 인물/사건/용어 후보 추출
2. Neo4j에서 관련 Period, Category, RelationGroup 탐색
3. 같은 시대와 분류 안에서 vector 검색
4. source_url_dictionary의 URL 본문과 함께 답변 생성
```

즉 사전은 Graph RAG와 Vector RAG 사이의 필터이자 연결 고리다.

### 사전은 검수 단위를 작게 만든다

원본 전체를 사람이 검수하는 것은 비현실적이다. 대신 사전을 만들면 검수 단위가 작아진다.

예를 들어 `history_terms`는 수만 건이지만, `category_dictionary.csv`는 수백 건 수준이다. `itkc_person_relations.csv`는 수십만 행이지만, `relation_type_dictionary.csv`는 관계 유형 16개 수준이다.

즉 원본 전체를 검수하는 대신 다음만 집중해서 보면 된다.

- 카테고리 경로가 올바른가?
- 이벤트 카테고리 토큰이 잘 분리됐는가?
- 이벤트 카테고리와 표준 카테고리 매핑이 적절한가?
- `부`, `자`, `형제`, `교유`의 방향과 그룹이 올바른가?
- 날짜 파싱 실패 건은 무엇인가?
- RAG 수집 대상 URL이 적절한가?

이렇게 하면 검수 비용이 줄고, 잘못된 규칙 하나가 어디에 영향을 주는지도 추적하기 쉬워진다.

### 사전 구축은 과한 작업이 아니라 반복 비용을 줄이는 작업이다

처음에는 사전을 만드는 일이 번거로워 보인다. 하지만 사전이 없으면 다음 작업에서 같은 비용이 반복된다.

```text
Neo4j import할 때 한 번
챗봇 검색 쿼리 만들 때 한 번
문제 생성 후보 만들 때 한 번
RAG 필터 만들 때 한 번
검수할 때 한 번
```

사전을 만들면 이 판단을 한 번만 정리하고 계속 재사용할 수 있다.

따라서 이 프로젝트에서 사전은 선택적 부가물이 아니라, 다음 단계들의 공통 기반이다.

```text
전처리
Neo4j import
그래프 질의
문제 생성
Hybrid RAG
검수
```

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
        category_dictionary.csv
        event_category_dictionary.csv
        category_mapping.csv
        period_dictionary.csv
        relation_type_dictionary.csv
        source_url_dictionary.csv

      staging/
        term_category_relation.csv
        event_category_relation.csv
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
| `category_dictionary.csv` | `dictionary/` | `history_terms.term_lk` 기반 `Category` 노드 사전 |
| `term_category_relation.csv` | `staging/` | `Term - HAS_CATEGORY - Category` 관계 생성용 |
| `event_category_dictionary.csv` | `dictionary/` | `itkc_events.subject_category` 기반 이벤트 분류 사전 |
| `event_category_relation.csv` | `staging/` | `Event - HAS_EVENT_CATEGORY - EventCategory` 관계 생성용 |
| `category_mapping.csv` | `dictionary/` 또는 `mapping/` | 이벤트 분류와 표준 카테고리 연결 규칙 |
| `period_dictionary.csv` | `dictionary/` | `Period` 노드 기준 사전 |
| `event_date_parse.csv` | `staging/` | Event 날짜 원문 정규화 결과 |
| `relation_type_dictionary.csv` | `dictionary/` | 인물 관계 의미, 방향, 대칭성 규칙 |
| `source_url_dictionary.csv` | `dictionary/` | URL 출처와 RAG 수집 기준 사전 |

---

## 4. `category_dictionary.csv`

### 4.1 역할

`category_dictionary.csv`는 `history_terms.term_lk`에서 만든 표준 카테고리 사전이다.

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

그래서 `term_lk`를 분해해서 `Category` 노드로 만들 기준표가 필요하다.

### 4.2 왜 필요한가

`category_dictionary.csv`가 필요한 이유는 다음과 같다.

1. `Category` 노드의 기준 목록이 된다.
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

## 5. `term_category_relation.csv`

### 5.1 역할

`term_category_relation.csv`는 dictionary가 아니라 staging relation이다. `Term`과 `Category`를 연결하기 위한 중간 산출물이다.

`category_dictionary.csv`만 있으면 카테고리 목록은 알 수 있지만, 어떤 `term_id`가 어떤 카테고리에 속하는지는 알 수 없다. 그 연결 정보가 `term_category_relation.csv`다.

### 5.2 왜 필요한가

이 관계는 Cypher 쿼리로도 만들 수 있다. 하지만 `term_lk`에는 `>`, `>>` 파싱이 들어가고, 복수 카테고리 경로가 존재하므로 전처리에서 펼쳐두는 편이 안전하다.

필요한 이유는 다음과 같다.

1. Neo4j import가 단순해진다.
2. Cypher에서 문자열 파싱 로직을 반복하지 않아도 된다.
3. 복수 카테고리 연결을 명확하게 확인할 수 있다.
4. 원본 `term_lk`에서 어떤 관계가 만들어졌는지 검수할 수 있다.
5. `Term - HAS_CATEGORY - Category` 관계를 안정적으로 만들 수 있다.

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

## 6. `event_category_dictionary.csv`

### 6.1 역할

`event_category_dictionary.csv`는 `itkc_events.csv.subject_category`에서 만든 이벤트 전용 카테고리 사전이다.

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

## 7. `event_category_relation.csv`

### 7.1 역할

`event_category_relation.csv`는 dictionary가 아니라 staging relation이다. `Event`와 `EventCategory`를 연결하기 위한 파일이다.

### 7.2 쿼리로 만들 수 있는가

가능은 하다. `term_lk`보다 파싱 난도는 낮다. 하지만 다음 이유 때문에 전처리 산출물로 만드는 편이 좋다.

- `subject_category`에 쉼표와 줄바꿈이 섞여 있다.
- 같은 `event_id`가 `event_subject`, `event_period` scope에서 중복 수집되어 있다.
- `detail_url`만 다른 중복 행이 존재한다.
- 이벤트 분류를 어떻게 분리했는지 검수할 수 있어야 한다.

따라서 이 파일은 `staging/`에 두는 것이 좋다.

### 7.3 왜 필요한가

1. `Event - HAS_EVENT_CATEGORY - EventCategory` 관계를 명확히 만든다.
2. `subject_category` 복합값을 여러 관계로 펼친다.
3. 같은 `event_id` 중복을 정리한 뒤 관계를 생성할 수 있다.
4. 원본 `subject_category`를 보존해 검수할 수 있다.
5. 이후 `category_mapping.csv`를 통해 표준 카테고리와 연결할 수 있다.

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

## 8. `category_mapping.csv`

### 8.1 역할

`category_mapping.csv`는 `event_category_dictionary.csv`와 `category_dictionary.csv`를 연결하는 매핑표다.

중요한 점은 이 파일이 두 사전을 대체하지 않는다는 것이다.

```text
event_category_dictionary.csv = 이벤트 원본 분류 사전
category_dictionary.csv = history_terms.term_lk 기반 표준 카테고리 사전
category_mapping.csv = 두 분류 체계를 연결하는 규칙표
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

원본 데이터에는 여러 URL 컬럼이 있다.

```text
itkc_events.detail_url
itkc_event_relations.detail_url
itkc_person_relations.evidence_url
itkc_person_relations.detail_url
```

URL은 그래프 구조 자체에는 필수는 아니지만, 답변의 근거와 RAG 품질에는 중요하다.

### 12.2 왜 필요한가

1. 중복 URL을 제거한다.
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
| `source_table` | URL이 나온 테이블 |
| `source_column` | URL이 나온 컬럼 |
| `source_type` | `EVIDENCE`, `DETAIL`, `SOURCE` 등 |
| `use_for_rag` | RAG 수집 대상 여부 |
| `fetch_status` | Tavily 수집 상태 |
| `note` | 비고 |

---

## 13. 구축 우선순위

사전과 staging 파일은 다음 순서로 만드는 것이 좋다.

1. `category_dictionary.csv`
2. `term_category_relation.csv`
3. `event_category_dictionary.csv`
4. `event_category_relation.csv`
5. `relation_type_dictionary.csv`
6. `event_date_parse.csv`
7. `period_dictionary.csv`
8. `source_url_dictionary.csv`
9. `category_mapping.csv`

`category_mapping.csv`는 중요하지만 가장 먼저 만들면 어렵다. 이벤트 카테고리와 표준 카테고리 사전이 먼저 있어야 매핑 후보를 만들 수 있다.

---

## 14. 결론

Neo4j 그래프 구축에서 사전은 부가물이 아니라 그래프 품질을 결정하는 기준표다.

각 파일의 핵심 역할은 다음과 같다.

- `category_dictionary.csv`: 표준 카테고리 노드 기준
- `term_category_relation.csv`: 용어와 카테고리 연결
- `event_category_dictionary.csv`: 이벤트 원본 분류 보존
- `event_category_relation.csv`: 사건과 이벤트 분류 연결
- `category_mapping.csv`: 이벤트 분류와 표준 카테고리 연결
- `period_dictionary.csv`: 시대 노드 기준
- `event_date_parse.csv`: 사건 날짜 정규화
- `relation_type_dictionary.csv`: 인물 관계 의미 규칙
- `source_url_dictionary.csv`: RAG와 출처 추적 기준

이 구분을 해두면 Neo4j import, 검수, 문제 생성, Hybrid RAG 확장을 모두 같은 흐름 안에서 관리할 수 있다.

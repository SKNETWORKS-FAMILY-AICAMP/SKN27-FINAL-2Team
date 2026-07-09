# history_terms Neo4j Mermaid 설계

이 문서는 `history_terms.csv`의 Neo4j 전처리와 적재 설계를 Mermaid 다이어그램 중심으로 정리한 문서다.  
현재 1차 목표는 `term_kind`, `topterm_id`, `term_lk` 구조를 해석하고, `Term`, `Category`, `TopCategory` 중심의 MVP 그래프를 만드는 것이다.

## 1. 전체 구축 흐름

```mermaid
flowchart TD
    A["원본 데이터 로드"] --> B["정규화 및 EDA"]
    B --> C["사전 CSV 생성"]
    C --> D["스테이징 CSV 생성"]
    D --> E["Neo4j import CSV 생성"]
    E --> F["Neo4j LOAD CSV 적재"]
    F --> G["문제 생성 / RAG 검색 활용"]

    A1["history_terms.csv"] --> A
    A2["event.csv"] --> A
    A3["event_relation.csv"] --> A
    A4["era_reference.json"] --> A

    C1["period_dictionary.csv"] --> C
    C2["category_dictionary.csv"] --> C
    C3["category_mapping.csv"] --> C
    C4["topic_dictionary.csv"] --> C
    C5["topic_keyword_dictionary.csv"] --> C

    D1["normalized_terms.csv"] --> D
    D2["normalized_events.csv"] --> D
    D3["person_event_relation.csv"] --> D
    D4["term_category_relation.csv"] --> D
    D5["term_period_relation.csv"] --> D

    E1["nodes.csv"] --> E
    E2["relationships.csv"] --> E
```

## 2. 원본 데이터 역할

```mermaid
flowchart LR
    HT["history_terms.csv"] --> HT1["Term 기본 노드"]
    HT --> HT2["Category 후보"]
    HT --> HT3["Period 추론 후보"]
    HT --> HT4["Topic 추론 후보"]

    EV["event.csv"] --> EV1["Event 노드"]
    EV --> EV2["EventGroup 후보"]
    EV --> EV3["event_date 정규화"]
    EV --> EV4["Period 연결"]

    ER["event_relation.csv"] --> ER1["Person 노드"]
    ER --> ER2["INVOLVED_IN 관계"]
    ER --> ER3["CO_OCCURS_WITH 확장 후보"]

    REF["era_reference.json"] --> REF1["Period 사전 초안"]
    REF --> REF2["Topic 사전 초안"]
    REF --> REF3["Keyword 매칭 기준"]
```

## 3. `term_kind`, `topterm_id`, `term_lk` 해석

`term_kind`는 행의 역할을 나타낸다.

| 값 | 의미 | 전처리 역할 |
|---|---|---|
| `0` | 원본 최상위 대분류 | `TopCategory` 또는 원본 대분류 기준 |
| `1` | 하위 분류/태그/색인어 후보 | `Category` 후보 검증/보강 |
| `2` | 실제 역사 용어 | `Term` 노드 생성 대상 |

`topterm_id`는 직접 부모가 아니라 최상위 대분류 ID로 본다.  
실제 상세 분류 연결은 `term_lk`의 경로 문자열을 분해해서 만든다.

```mermaid
flowchart TD
    K0["term_kind = 0<br/>정치·행정·법제<br/>term_id = 8<br/>topterm_id = 8"] --> K1["term_kind = 1<br/>범죄<br/>topterm_id = 8"]
    K0 --> K2["term_kind = 2<br/>실제 용어<br/>topterm_id = 8"]

    K2 --> LK["term_lk<br/>정치·행정·법제 > 사법 > 범죄"]
    LK --> C1["Category<br/>정치·행정·법제"]
    LK --> C2["Category<br/>사법"]
    LK --> C3["Category<br/>범죄"]

    C2 --> R1["SUBCATEGORY_OF"]
    R1 --> C1
    C3 --> R2["SUBCATEGORY_OF"]
    R2 --> C2
    K2 --> R3["HAS_CATEGORY"]
    R3 --> C3
```

## 4. `term_lk` 분류 경로 분해

`term_lk`는 실제 용어가 속한 분류 경로다.  
`>>`는 복수 경로 구분자, `>`는 계층 구분자로 처리한다.

```mermaid
flowchart TD
    A["term_lk 원문"] --> B["복수 경로 분리<br/>구분자: >>"]
    B --> C["단일 경로 분리<br/>구분자: >"]
    C --> D["경로 조각 정제"]
    D --> E["Category 후보 생성"]
    E --> F["Category 계층 생성"]
    F --> G["Term - HAS_CATEGORY - Category 관계 생성"]

    EX["사회·생활>풍속·의례>풍속>>사회·생활>일상생활>의생활"] --> A
```

## 5. 최상위 카테고리 9개

설계본의 서비스용 최상위 카테고리는 9개로 둔다. 원본 대분류 17개는 보존하되, 서비스 그래프에서는 아래 기준으로 매핑한다.

```mermaid
flowchart LR
    TC["TopCategory"] --> C1["나라"]
    TC --> C2["시대"]
    TC --> C3["인물"]
    TC --> C4["조직 단체"]
    TC --> C5["사건"]
    TC --> C6["제도 정책"]
    TC --> C7["사상 종교"]
    TC --> C8["문화 생활"]
    TC --> C9["유물 유적"]
```

## 6. MVP 노드 설계

```mermaid
flowchart TD
    Entity["Entity<br/>공통 상위 라벨"]

    Entity --> Term["Term<br/>역사 용어"]
    Entity --> Event["Event<br/>역사 사건"]
    Entity --> Person["Person<br/>인물"]

    Period["Period<br/>시대"]
    Category["Category<br/>상세 분류"]
    TopCategory["TopCategory<br/>최상위 카테고리"]
    Topic["Topic<br/>주제"]
    Keyword["Keyword<br/>매칭 키워드"]
    EventGroup["EventGroup<br/>사건 묶음"]
```

## 7. MVP 관계 설계

```mermaid
flowchart LR
    Term["Term"] -->|HAS_CATEGORY| Category["Category"]
    Term -->|HAS_TOP_CATEGORY| TopCategory["TopCategory"]
    Category -->|SUBCATEGORY_OF| CategoryParent["Category"]
    Category -->|MAPPED_TO| TopCategory

    Term -->|IN_PERIOD| Period["Period"]
    Term -->|INFERRED_IN_PERIOD| InferredPeriod["Period"]
    Event["Event"] -->|IN_PERIOD| EventPeriod["Period"]
    Event -->|PART_OF_EVENT_GROUP| EventGroup["EventGroup"]

    Person["Person"] -->|INVOLVED_IN| Event
    Term -->|HAS_TOPIC| Topic["Topic"]
    Event -->|HAS_TOPIC| Topic
    Term -->|HAS_MATCHED_KEYWORD| Keyword["Keyword"]
    Event -->|HAS_MATCHED_KEYWORD| Keyword
```

## 8. 원본 관계와 추론 관계 분리

원본 CSV에 직접 들어 있는 관계와 전처리로 추론한 관계는 분리한다.  
이렇게 해야 검수, 삭제, 신뢰도 기반 검색이 쉬워진다.

```mermaid
flowchart TD
    A["원본 CSV 기반"] --> B["IN_PERIOD"]
    A --> C["INVOLVED_IN"]
    A --> D["PART_OF_EVENT_GROUP"]

    E["전처리 추론 기반"] --> F["INFERRED_IN_PERIOD"]
    E --> G["HAS_TOPIC"]
    E --> H["HAS_MATCHED_KEYWORD"]

    B --> I["confidence = HIGH"]
    C --> I
    D --> I

    F --> J["confidence / score 저장"]
    G --> J
    H --> J
```

## 9. 전처리 산출물 구조

```mermaid
flowchart TD
    RAW["data/raw"] --> R1["history_terms.csv"]
    RAW --> R2["event.csv"]
    RAW --> R3["event_relation.csv"]
    RAW --> R4["era_reference.json"]

    DICT["data/dictionary"] --> D1["period_dictionary.csv"]
    DICT --> D2["category_dictionary.csv"]
    DICT --> D3["category_mapping.csv"]
    DICT --> D4["topic_dictionary.csv"]
    DICT --> D5["topic_keyword_dictionary.csv"]

    STG["data/staging"] --> S1["normalized_terms.csv"]
    STG --> S2["normalized_events.csv"]
    STG --> S3["person_event_relation.csv"]
    STG --> S4["term_period_relation.csv"]
    STG --> S5["term_category_relation.csv"]
    STG --> S6["review_candidates.csv"]

    IMP["data/neo4j_import"] --> N1["nodes.csv"]
    IMP --> N2["relationships.csv"]
```

## 10. 1차 구현 순서

```mermaid
flowchart TD
    A["1. 시대 사전 초안 생성"] --> B["2. 분류/카테고리 사전 초안 생성"]
    B --> C["3. 기본 노드 생성<br/>Term / Event / Person / Period / Category / TopCategory"]
    C --> D["4. 원본 기반 관계 생성<br/>IN_PERIOD / HAS_CATEGORY / HAS_TOP_CATEGORY / INVOLVED_IN"]
    D --> E["5. 추론 기반 관계 생성<br/>HAS_TOPIC / INFERRED_IN_PERIOD / PART_OF_EVENT_GROUP"]
    E --> F["6. nodes.csv / relationships.csv 생성"]
    F --> G["7. Neo4j LOAD CSV 적재"]
```

## 11. 2차 확장 후보

```mermaid
flowchart LR
    MVP["MVP 그래프"] --> A["Place"]
    MVP --> B["Polity"]
    MVP --> C["Organization"]
    MVP --> D["Office"]
    MVP --> E["Reign"]
    MVP --> F["Alias"]
    MVP --> G["CO_OCCURS_WITH"]
    MVP --> H["Action 관계 후보"]
```

## 12. 정리

1차 전처리의 핵심은 `history_terms.csv`를 단순 용어 목록으로만 보지 않고, `term_kind`, `topterm_id`, `term_lk`를 분리해서 해석하는 것이다.

- `term_kind = 0`은 원본 대분류 기준이다.
- `term_kind = 1`은 하위 분류/태그 후보이며, 직접 부모 관계로 확정하지 않는다.
- `term_kind = 2`는 실제 `Term` 노드 생성 대상이다.
- 상세 분류 연결은 `term_lk` 경로 분해 결과를 우선한다.
- 원본 관계와 추론 관계는 별도 관계 타입과 속성으로 분리한다.

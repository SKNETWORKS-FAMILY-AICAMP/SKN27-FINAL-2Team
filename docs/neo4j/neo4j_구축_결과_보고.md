# Neo4j GraphDB 구축 결과 보고

한국사 학습 챗봇의 문제 생성·개념 검색을 위해, 한국사 용어·사건·인물 데이터를 Neo4j 지식 그래프로 구축했다. 이 문서는 전처리를 어떻게 했고, 왜 그렇게 했으며, 그 결과 무엇을 얻었는지, 그리고 노드·엣지를 어떤 기준으로 설계했는지를 정리한 보고 문서다.

**최종 결과: 노드 17종 295,920건, 관계 CSV 39개 1,176,708건, 관계 타입 22종. 전처리 스크립트 1회 실행으로 전체 재현 가능.**

---

## 1. 왜 GraphDB인가

원천 데이터는 성격이 서로 다른 CSV 4종이다.

| 원천 | 내용 | 규모(정규화 후) |
|---|---|---:|
| 한국역사용어시소러스 (국사편찬위원회) | 역사 용어 + 계층형 분류 + 시대 | 61,598 |
| 한국고전종합DB 관계망 (ITKC) | 역사 사건 | 600 |
| ITKC 사건-인물 관계 | 사건 참여 | 6,918 |
| ITKC 인물-인물 관계 | 가족·교유·사제 등 | 206,507 |

이 데이터의 핵심 가치는 개별 행이 아니라 **연결**에 있다. "조선 시대 군사 주제의 인물", "위화도회군에 참여한 인물의 사제 관계", "이 용어와 관련된 사건의 출처 URL" 같은 질문은 관계형 DB에서는 다단계 조인이 필요하지만, 그래프에서는 엣지를 따라가는 것으로 끝난다. 벡터 DB(의미 검색)와 역할을 나눠, **구조적·관계형 질의는 GraphDB가 담당**하도록 설계했다.

---

## 2. 전처리를 어떻게 했는가

### 2.1 파이프라인 구조

전체 전처리는 시작 파일 하나(`etl/preprocessing/neo4j/run_neo4j_preprocessing.py`)로 실행되며, 5단계 스크립트가 순서대로 돌아간다.
Term-Person 수동 검수 후보는 graph 생성 필수 단계가 아니므로 기본 runner에 포함하지 않고, 필요할 때 `make_term_person_review.py --save`로 별도 생성한다.

```
raw CSV → normalized → dictionary → mapping/staging → Neo4j import CSV → Cypher 적재
```

| 순서 | 스크립트 | 역할 |
|---:|---|---|
| 1 | `normalize_raw_data.py` | raw를 EDA 기준으로 정규화 (중복 제거, 불필요 컬럼 정리) |
| 2 | `make_base_dictionaries.py` | 노드 후보 사전 생성, 연도·날짜 파싱 |
| 3 | `make_mapping_tables.py` | 분류 체계 간 crosswalk(매핑) 생성 |
| 4 | `make_graph_csv.py` | Neo4j import용 최종 노드/관계 CSV 생성 |
| 5 | `make_theme_era_csv.py` | Theme/Era/EntityType 서비스 축 레이어 생성 |

**왜 이렇게 했는가.** 원천에서 최종 CSV까지를 한 번에 만들지 않고 normalized → dictionary → mapping → staging 중간 산출물을 모두 CSV로 남겼다. 각 단계가 파일로 고정되어 있으면 "카테고리 연결이 이상하다" 같은 문제가 생겼을 때 어느 단계에서 틀어졌는지 파일만 열어보고 추적할 수 있고, 특정 단계만 고쳐서 재실행할 수 있다.

**이익.** 디버깅과 검수가 단계별로 가능해졌고, 신규 팀원도 중간 CSV를 열어 데이터 상태를 직접 확인할 수 있다.

### 2.2 규칙은 코드가 아니라 seed CSV로 관리

자동으로 판단하기 어려운 규칙은 사람이 관리하는 seed CSV 15종으로 분리했다. 시대 순서·범위(period_seed), 인물 관계 정규화(relation_type_seed), 분류 매핑(taxonomy_crosswalk_seed), 주제·시대·유형 정의, 왕대·연호(reign_seed), 수동 검수 승인 목록 등이다.

**왜 그렇게 했는가.** raw 분류명은 출처마다 다르고(`국방·군사` vs `전쟁`), 이름이 같은 것만 자동 매핑하면 누락이 생기고, 의미 유사도로 자동 연결하면 그래프가 흐려진다. 이런 판단을 코드에 하드코딩하면 수정할 때마다 개발자가 필요하다.

**이익.** 매핑이 틀리면 원본 데이터나 코드가 아니라 **seed CSV만 수정하고 runner를 재실행**하면 된다. 사람의 검수 결정(예: 동명이인 승인)도 seed로 보존되어 재실행해도 사라지지 않는다. 전처리 전체가 "동일 입력 + 동일 seed = 동일 출력"으로 재현 가능하다.

### 2.3 품질 이슈를 어떻게 처리했는가

| 이슈 | 처리 방식 | 왜 그렇게 했는가 |
|---|---|---|
| 연도 결측·비정형 (`?-1308`, `B.C.33`, `1920년대`) | 행을 버리지 않고 보수적으로 파싱, `year_precision`·`parse_status` 속성 부여 (PARSED 33,458 / UNKNOWN 28,140) | 연도가 없어도 용어 자체는 검색 가치가 있다. 파싱 실패를 상태값으로 남기면 검수 대상이 명확해진다 |
| 동명이왕 (`고종` 등) | 왕대 연호 자동 계산에서 제외 | 잘못된 연도가 들어가는 것보다 비워두는 것이 안전하다 |
| 분류 체계 불일치 (계층형 vs 평면) | 원본 분류 보존 + 수동 crosswalk 매핑 | 3장에서 상세 설명 |
| 동명이인 (Term-Person 연결) | 이름/한자와 생몰년이 모두 맞는 유일 후보, 관계망 단서가 있는 후보, 수동 검수 승인분만 반영 | 자동 연결의 오류는 그래프 전체 신뢰도를 훼손한다. 정밀도 우선 |
| 인물 관계 방향·대칭 (부/자, 교유) | seed로 정규화, 대칭 관계는 한 방향만 저장 | 원본에 A→B, B→A가 모두 있어 그대로 넣으면 중복. 4장에서 상세 설명 |
| 0행 optional 관계 | CSV 자체를 생성하지 않음 | 빈 CSV가 있으면 "관계가 존재하는데 데이터만 비었다"처럼 보여 검수 때마다 혼란. 매핑이 보강되면 재실행 시 자동 생성 |

공통 원칙은 **"버리지 않고 상태를 남긴다, 불확실하면 자동 연결하지 않는다"**이다. 그 결과 원천 데이터의 손실 없이, 검수 가능한 형태로 품질을 관리할 수 있다.

---

## 3. 노드 설계: 어떻게, 왜

### 3.1 노드 17종

| 그룹 | Label (건수) | 역할 |
|---|---|---|
| 핵심 데이터 | Term(61,598), Event(600), Person(56,403) | 검색·출제의 실제 대상 |
| 분류 체계 | CanonicalCategory(400), SourceEventCategory(53) | 표준 카테고리 계층 / 사건 원본 분류 보존 |
| 시대 축 | Period(30), Era(10) | 원본 시대 표기 / 표준 시대 10개 |
| 서비스 축 | Theme(10), EntityType(4) | 고정 주제 10개 / 실체 유형(인물·문헌·문화재·장소) |
| 의미 축 | Country(5), Region(7), EconomicDomain(16), TaxonomyFacet(49) | 카테고리 경로에서 분리한 국가·권역·경제·중간 분류 |
| 검색·묶음 | EventFacet(53), EventGroup(32), SearchTag(175,714) | 사건 성격 facet / 관련 사건 묶음 / 통합 검색 태그 |
| 출처 | SourceUrl(57,412) | 사건 URL과 인물 상세 URL. Web RAG 수집 후보 |

### 3.2 설계 결정 1 — 원본 보존과 표준화의 분리

사건의 원본 분류(`전쟁`, `반란`)는 `SourceEventCategory`로 그대로 보존하고, 용어 시소러스에서 만든 표준 카테고리 `CanonicalCategory`와는 crosswalk 매핑 테이블로만 연결했다. 문자열이 비슷하다고 두 분류 체계를 합치지 않았다.

**왜.** `term_lk`는 계층형 시소러스이고 `subject_category`는 수집 과정에서 붙은 평면 분류다. 관리 체계가 다른 두 분류를 합쳐버리면 원본으로 되돌릴 수 없고, 매핑 오류를 교정할 수도 없다.

**이익.** 원본 무손실 + 표준 축 검색이 동시에 가능하다. 매핑이 틀리면 crosswalk seed만 고치면 된다.

### 3.3 설계 결정 2 — 의미 축의 분리

카테고리 경로 안에는 `외교·국제관계>러시아`, `경제·산업>수산업`처럼 성격이 다른 값이 섞여 있다. 이런 값을 단순 하위 카테고리로 두지 않고 Country, Region, EconomicDomain, TaxonomyFacet라는 별도 노드로 분리했다. 국가와 권역도 상하 관계로 두지 않았다.

**왜.** `러시아`는 "외교 카테고리의 하위 항목"이 아니라 "국가"라는 독립적 의미 축이다. 카테고리 계층에 묻어두면 "러시아 관련 용어 전부"를 찾을 때 경로 문자열 파싱이 필요해진다.

**이익.** `(:Term)-[:ABOUT_COUNTRY]->(:Country {name:"러시아"})` 한 줄로 국가 필터가 끝난다. 지역·경제 분야도 동일하다.

### 3.4 설계 결정 3 — 시대의 2단 구조

원본 시대 표기(고려시대/고려, 일제시기/일제시대 같은 변형 30종)는 `Period`로 보존하고, `PART_OF_ERA`로 표준 시대 `Era` 10개(선사시대~현대)에 통합했다.

**왜.** 서비스는 "고려"라는 하나의 축이 필요하지만, 원본 표기를 지워버리면 출처 추적이 불가능해진다.

**이익.** 사용자는 Era 10개로 필터링하고, 데이터 검증은 Period 원본으로 한다. 표기 변형이 추가되어도 seed 한 줄로 흡수된다.

### 3.5 설계 결정 4 — 검색 편의 레이어 (SearchTag)

용어/사건/인물 이름과 원본 분류·표준 카테고리·facet·시대·주제·국가 등 여러 축을 통합한 SearchTag를 별도로 두었다. 의도된 비정규화(중복) 레이어다.

**왜.** 키워드 하나로 Term/Event/Person을 찾으려면 원래는 이름, 분류, 시대, 주제, 의미 축을 OR 조건으로 뒤져야 한다.

**이익.** `(n)-[:HAS_SEARCH_TAG]->(:SearchTag {tag_name:"전쟁"})` 패턴으로 공통 검색 진입점이 생긴다. 태그에 출처 속성(`source_node_type`, `source_node_id`, `source_relation`, `source_detail`)을 남겨 정밀 검증 시 원래 축으로 되돌아갈 수 있다. Person 별칭은 `PersonAlias` 출처로 분리하고, Event/Term에서 상속된 Person 태그는 `source_detail`에 원천 ID를 보존한다.

---

## 4. 엣지 설계: 어떻게, 왜

### 4.1 주요 관계

| 관계 | 연결 | 행 수 |
|---|---|---:|
| HAS_CATEGORY | Term/Event → CanonicalCategory | 61,697 / 692 |
| IN_PERIOD | Term/Event → Period | 65,358 / 600 |
| IN_ERA (직통) | Term/Event/Person → Era | 54,125 / 600 / 23,029 |
| HAS_THEME (직통) | Term/Event/Person → Theme | 58,605 / 1,405 / 60,512 |
| HAS_ENTITY_TYPE | Term → EntityType | 20,662 |
| RELATED_TO | Person → Person | 184,044 |
| INVOLVED_IN | Person → Event | 6,918 |
| REFERS_TO | Term → Person/Event | 2,243 / 13 |
| MENTIONS_PERSON | Term → Person | 8,606 |
| HAS_SOURCE_URL | Event/Person → SourceUrl | 2,382 / 56,212 |
| HAS_SEARCH_TAG | Term/Event/Person → SearchTag | 349,531 / 6,016 / 238,817 |
| 구조 관계 | SUBCATEGORY_OF 335, MAPPED_TO_CATEGORY 45, PART_OF_ERA 23 등 | - |

### 4.2 설계 결정 1 — 인물 관계는 type 하나 + 속성으로 의미 보존

인물 관계 타입을 `HAS_FATHER`, `SIBLING_OF`처럼 쪼개지 않고 전부 `RELATED_TO` 하나로 통일했다. 실제 의미(부/자/형제/교유/사제)는 `normalized_relation_type`, `relation_group`, `direction_rule`, `is_symmetric` 등 관계 속성으로 보존했다.

**왜.** 원본 관계명은 16종 이상으로 다양하고 검수 전 의미가 불안정하다. type을 잘게 쪼개면 스키마가 raw 품질에 끌려다니고, 관계명이 추가될 때마다 import 쿼리를 고쳐야 한다.

**이익.** import가 안정적이고, 관계 의미 정제는 seed 수정만으로 가능하다. 대칭 관계(교유·형제)는 원본에 양방향으로 중복 존재하므로 무방향 쌍 기준 한 방향만 저장해 184,044건으로 압축했다. (조회 시 무방향 패턴 `(a)-[:RELATED_TO]-(b)` 사용)

### 4.3 설계 결정 2 — 자주 쓰는 경로는 직통 엣지로 물리화

이론상 `Term → IN_PERIOD → Period → PART_OF_ERA → Era` 경로로 시대를 알 수 있지만, 전처리 단계에서 이 경로를 미리 펼쳐 `Term-[:IN_ERA]->Era` 직통 엣지를 만들었다. HAS_THEME도 동일하다. Person은 생몰년과 Era 연도 범위의 겹침을 우선 적용하고, 생몰년이 없으면 참여 사건의 시대로 보조 추론했다. 더 좁은 Era가 같은 생애 겹침 구간을 완전히 설명하면 넓은 Era 중복은 제외한다.

**왜.** 문제 출제 서비스의 핵심 쿼리는 "시대 하나, 주제 하나를 고르면 바로 후보를 가져오는" 패턴이다. 매 요청마다 3-hop을 타는 것은 낭비이고 쿼리도 복잡해진다.

**이익.** 서비스 쿼리가 전부 1-hop으로 단순화된다. 직통 엣지는 파생 산출물이므로 기준이 바뀌면 seed 수정 후 재생성하면 되고, 원천 경로(Period, PART_OF_ERA)는 그대로 남아 있어 근거 추적도 가능하다.

### 4.4 설계 결정 3 — 범위 시대 확장은 쿼리가 아니라 전처리에서

`삼국시대-조선시대` 같은 범위 표현은 CSV 생성 단계에서 시작(RANGE_START)/중간(RANGE_MIDDLE)/끝(RANGE_END) 시대로 미리 확장했다. 중간 시대는 period_seed의 순서 정보로 계산한다.

**왜.** 이 로직을 Cypher로 처리하면 모든 시대 쿼리가 복잡해지고 느려진다. 규칙은 한 번만 계산하면 되는 정적 정보다.

**이익.** Neo4j 쿼리는 단순 매칭만 하면 되고, 확장 결과가 CSV로 남아 있어 "왜 이 용어가 고려시대에 걸리는가"를 파일에서 바로 확인할 수 있다.

### 4.5 설계 결정 4 — 출처 URL을 관계 속성이 아닌 노드로

`events.source_urls`, `event_relations.source_urls`, `person_relations.detail_url`은 `SourceUrl` 노드(57,412건, 중복 제거)로 분리한 뒤 `HAS_SOURCE_URL`로 연결한다. 반면 `person_relations.evidence_url`은 범용 URL 허브가 되는 것을 막기 위해 `RELATED_TO.evidence_url` 관계 속성으로만 보존한다.

**왜.** 사건 URL과 인물 상세 URL은 출처 노드와 RAG 후보로 유용하지만, 인물 관계의 근거 URL은 같은 문헌/목록 URL 하나가 많은 사람에게 붙을 수 있다. 이를 노드 관계로 승격하면 특정 URL이 과도한 허브가 되어 graph view와 탐색 품질을 흐린다.

**이익.** `SourceUrl`은 사건과 인물 상세 페이지의 출처 노드이자 Web RAG(Tavily) 수집 후보 목록을 겸한다. 인물 관계의 근거 URL은 `RELATED_TO` 속성에 남아 관계 근거를 잃지 않으면서도 URL 허브 노드를 만들지 않는다.

---

## 5. 적재와 검증

- **적재**: `storage/neo4j/load_schema.py` 실행 한 번으로 기존 그래프 reset → 제약조건 → 노드 → 관계 → 검증 순서로 진행. `LOAD CSV + MERGE` 기반 멱등 적재라 재실행해도 안전하다.
- **무결성**: 17개 label 전부에 ID unique constraint. 다중 근거가 정당한 관계(사건-인물 참여 등)는 MERGE 키에 원본 식별자를 포함해 중복 collapse를 방지했다.
- **타입**: CSV는 전부 문자열이므로 연도·순서·집계 속성은 import 시점에 `toIntegerOrNull()`로 캐스팅. 덕분에 "1850~1910년 사이 용어" 같은 숫자 범위 검색이 가능하다.
- **검증**: 적재 직후 `history_graph_verify.cypher`가 label별 노드 수(총 295,920)와 relationship type별 관계 수(총 1,176,708)를 출력해 누락·빈 label을 즉시 확인한다.

---

## 6. 요약: 이 설계로 얻은 것

| 얻은 것 | 근거 |
|---|---|
| **재현성** | 스크립트 1회 실행으로 전체 재생성. 규칙은 seed CSV, 검수 결정도 seed로 보존 |
| **원본 무손실** | 원본 분류·시대 표기·URL 전부 보존. 표준화는 매핑으로만 연결 |
| **검수 가능성** | 모든 중간 산출물이 CSV. 파싱 실패·미매핑이 상태값으로 명시 |
| **쿼리 단순성** | 시대·주제·유형·국가 필터가 전부 1-hop. 키워드 검색은 SearchTag 한 줄 |
| **신뢰도 우선** | 동명이인은 한자·생몰년·설명 근거가 맞는 경우만 연결하고, 동명이왕은 자동 연도 계산에서 제외. 오류보다 공백을 선택 |
| **확장성** | RAG 연계(SourceUrl), 매핑 보강, 시대·주제 축 추가가 모두 seed 수정 + 재실행으로 해결 |

### 6.1 전처리 품질 원칙

이 보고서는 품질 원칙을 요약만 남긴다.
상세한 설계 판단과 실패 시 영향은 `docs/neo4j/neo4j_design_decisions_detail.md`에 합쳤다.

핵심 원칙은 다음 네 가지다.

- 원본은 보존하고 표준화는 관계로 표현한다.
- 강한 의미의 관계는 보수적으로 만든다.
- 자주 쓰는 조회 경로는 전처리에서 1-hop 관계로 펼친다.
- 파생 관계에는 `match_source`, `source_detail` 같은 근거를 남긴다.

---

## 7. 부록: 빠른 참조

상세한 노드·관계별 설계 이유는 `docs/neo4j/neo4j_design_decisions_detail.md`로 합쳤다.
이 보고서에는 최종 import 규모를 확인하기 위한 요약만 남긴다.

### 7.1 노드 요약

| 묶음 | 노드 |
|---|---|
| 핵심 실체 | `Term` 61,598 / `Event` 600 / `Person` 56,403 |
| 분류 | `CanonicalCategory` 400 / `SourceEventCategory` 53 / `EventFacet` 53 / `TaxonomyFacet` 49 |
| 시대·주제·유형 | `Period` 30 / `Era` 10 / `Theme` 10 / `EntityType` 4 |
| 의미 축 | `Country` 5 / `Region` 7 / `EconomicDomain` 16 |
| 검색·출처·묶음 | `SearchTag` 175,714 / `SourceUrl` 57,412 / `EventGroup` 32 |

### 7.2 관계 요약

| 묶음 | 주요 관계 |
|---|---|
| 핵심 연결 | `REFERS_TO`, `MENTIONS_PERSON`, `INVOLVED_IN`, `RELATED_TO` |
| 분류·표준화 | `HAS_CATEGORY`, `HAS_EVENT_CATEGORY`, `MAPPED_TO_CATEGORY`, `HAS_EVENT_FACET`, `SUBCATEGORY_OF` |
| 시대·주제·유형 | `IN_PERIOD`, `PART_OF_ERA`, `IN_ERA`, `HAS_THEME`, `HAS_ENTITY_TYPE` |
| 의미 축 | `ABOUT_COUNTRY`, `ABOUT_REGION`, `ABOUT_ECONOMIC_DOMAIN`, `ABOUT_TAXONOMY_FACET`, `SUBREGION_OF` |
| 검색·출처·묶음 | `HAS_SEARCH_TAG`, `HAS_SOURCE_URL`, `PART_OF_EVENT_GROUP` |

- 상세 구현 문서: `docs/neo4j/neo4j_implementation_full_flow.md`
- 파일별 역할 문서: `docs/neo4j/neo4j_preprocessing_file_map.md`
- 구조도 문서: `docs/neo4j/neo4j_implementation_mermaid_flow.md`, `docs/neo4j/neo4j_그래프_스키마_mermaid.md`

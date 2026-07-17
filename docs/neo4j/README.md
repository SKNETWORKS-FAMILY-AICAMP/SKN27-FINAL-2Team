# 한국사 범용 Neo4j 설계

> 상태: `TARGET-DESIGN`
> 기준일: 2026-07-17
> 범위: 3종 원천의 ETL, 사실 검증, 동명이인 해소, 챗봇 지식 조회와 오답 후보 검색의 공통 Graph

## 1. 목적과 책임 범위

Neo4j는 문제 생성과 챗봇이 함께 사용하는 검증된 역사 지식 Graph다. 문제 생성에서는
이미 정해진 정답 대상을 기준으로 공통 Anchor를 함께 타는 다른 역사 대상을 찾고,
챗봇에서는 `CanonicalEntity` 사이의 검증된 typed relationship와 근거를 조회한다.
Neo4j 자체가 문제 문장이나 챗봇 최종 답변을 생성하지는 않는다.

```text
정답 대상
  -> 검증된 분류·역할·국가·시대·지역·사실 anchor
  -> 같은 anchor에 연결된 다른 canonical 대상
  -> 후보 ID·이름·공유 근거·RAG 검색 힌트 반환
```

Neo4j/ETL이 책임지는 것은 다음과 같다.

- 3종 원천의 안정 ID와 provenance 보존
- 원천 간 동일 실체 통합과 동명이인 분리
- topic, era, entity type, role, polity 등 다축 분류
- 인물-국가, 사건-지역 등 근거 있는 역사 관계 추출
- 근거가 확인된 관계만 검색 가능한 anchor로 공개
- 후보가 선택된 이유를 검색 서비스가 설명할 수 있는 결과 계약 제공
- 챗봇이 관계의 방향·타입·근거를 조회할 수 있는 공통 사실 계약 제공

다음은 이 설계의 책임 밖이다.

- 문제 유형과 난이도의 랜덤 선택
- 정답 근거를 찾는 pgvector 검색
- 지문 생성 API와 sLLM 선지 생성
- 최종 4개 오답 선정, 후보 가중치, path pattern 선택, PageRank 여부
- 문제 조립·평가·저장
- 챗봇의 질의 해석, 답변 문장 생성과 대화 상태 관리

검색 팀은 검색·랭킹 알고리즘을 결정한다. ETL 팀은 정확한 노드와 관계, 검증 상태,
관계 방향, 조회 가능한 anchor를 제공한다.

### 1.1 현재 확정 수준

| 구분 | 상태 |
|---|---|
| 핵심 Graph 골격 | `CanonicalEntity`, provenance, 7개 Anchor 축, `RoleAssignment`, `EvidenceSpan` 사용 확정 |
| 관계 저장 원칙 | typed relationship, endpoint 타입 검사, 근거·상태·버전 저장 확정 |
| EntityType·Predicate 전체 목록 | EDA 후 재확정 |
| Place–Region, DetailClass–Topic, 국가·작품의 이중 표현 | EDA 후 정책 결정 |

따라서 문서에 나오는 세부 Predicate는 현재 EDA seed다. NER, Entity Linking,
RelationCandidate 추출과 관계 분포 EDA가 끝난 뒤 최종 카탈로그와 allowlist로 다시
갱신해야 한다.

## 2. 실제 원천

기본 원천은 `etl/raw_data`의 다음 세 데이터군이다.

1. 한국민족문화대백과사전 `articles_list.jsonl`, `articles_detail.jsonl`
2. 한국고전종합DB 관계망 `itkc_people.csv`, `itkc_events.csv`,
   `itkc_person_relations.csv`, `itkc_event_relations.csv`
3. 한국역사용어시소러스 CSV

원천별 ID는 서로 다른 namespace다. `AKS eid`, `ITKC person_id/event_id`,
`thesaurus term_id`를 직접 같은 ID로 병합하지 않는다. 각 `SourceRecord`를 하나의
`CanonicalEntity`로 해소한다.

## 3. 고정 분류 축

### 3.1 Topic

재료의 최상위 topic은 다음 10개를 사용한다.

```text
사건
인물
정치
제도
문화
사회
군사
경제
사상·종교
외교
```

한 대상은 여러 topic을 가질 수 있다. 예를 들어 전쟁은 `사건·군사·외교`, 조세 제도는
`제도·정치·경제`에 함께 연결될 수 있다. 단, 원문이나 승인된 매핑 규칙이 지지하는
topic만 부여한다.

### 3.2 Era

재료의 최상위 era는 다음 10개를 사용한다.

```text
조선
고려
삼국시대
개항기
현대
일제강점기
남북국시대
초기국가
선사시대
고조선
```

`조선 전기`, `조선 후기` 같은 세부 시기는 `Era-[:SUBCATEGORY_OF]->Era`로 표현한다.
시대 경계는 별도의 승인된 기간표로 관리하고 LLM이 임의로 정하지 않는다. 원천이
`조선`까지만 지지하면 `조선 전기/후기`를 추측하지 않는다.

`부여`, `옥저`, `동예`, `삼한`은 시대가 아니라 정치체이므로 `Polity`로 둔다.
이들은 `EXISTED_DURING -> 초기국가`로 연결한다. `마한·진한·변한`은 필요한 경우
`Polity-[:SUBCATEGORY_OF]->Polity(삼한)`로 표현한다.

### 3.3 다른 축

| 축 | 예 | 목적 |
|---|---|---|
| `EntityType` | Person, Event, Institution, Heritage, Place, Organization, Concept (`Work`, `Polity`는 EDA 후보) | 서로 다른 종류의 후보 혼입 방지 |
| `PersonRole` | 왕, 왕비, 신하, 장군, 학자, 승려 | 인물 역할 비교 |
| `Polity` | 조선, 고려, 부여, 옥저, 동예, 마한 | 국가·정치체 맥락 |
| `Region` | 한성, 평양, 수원, 전라도 | 장소·발생지 맥락 |
| 세부 분류 | 정변, 전쟁, 조세 제도, 회화, 불교 사상 등 | 구체적인 공통 anchor |

분류 축과 역사 사실을 한 계층에 섞지 않는다. `왕`은 인물의 역할이고 `조선`은
정치체이며 `조선 후기`는 시대다.

### 3.4 문제 생성 용어 대응

문제 생성 문서의 용어 때문에 별도 노드를 중복 생성하지 않는다. 범용 Graph의 기존
노드·관계에 다음처럼 대응한다.

| 문제 생성 용어 | 범용 Graph 대응 |
|---|---|
| `QuestionTarget` | `CanonicalEntity` |
| `SemanticClass` | `DetailClass` |
| 부모·하위 `SemanticClass` | `DetailClass-[:SUBCATEGORY_OF]->DetailClass` 계층 |
| 원자 `Fact` | VERIFIED typed relationship 또는 `RelationAssertion` |
| 근거 | `EvidenceSpan` |
| `TopicType`, `QuestionFacet` | 문제 생성 요청을 `EntityType`, `DetailClass`, `question_intent_id`, 허용 경로로 변환하는 외부 계약 |

취약점 분석용 Topic 10개와 Era 10개는 이 대응 때문에 바꾸거나 9개 `TopicType`과
합치지 않는다. `SemanticClass`는 `DetailClass`의 외부 계약 명칭으로만 취급하며 별도
`SemanticClass` label을 만들지 않는다.

## 4. 핵심 연결 원칙

구체 대상은 여러 축에 동시에 연결한다.

```text
(정조:Person)-[:HAS_ENTITY_TYPE]->(Person)
(정조)-[:HAS_TOPIC]->(정치)
(정조)-[:IN_ERA]->(조선 후기)
(정조)-[:ASSOCIATED_WITH_POLITY]->(조선)
```

역할이 국가·시대에 따라 달라지는 경우에는 관계 맥락을 잃지 않도록 중간 노드를 쓴다.

```text
(정조)-[:HAS_ROLE_ASSIGNMENT]->(재위:RoleAssignment)
(재위)-[:ROLE]->(왕)
(재위)-[:IN_POLITY]->(조선)
(재위)-[:IN_ERA]->(조선 후기)
(재위)-[:SUPPORTED_BY]->(EvidenceSpan)
```

따라서 `왕과 국가`는 `RoleAssignment`를 통해 연결된다. `정치와 국가`는 추상 노드끼리
의미 없는 전역 edge를 만드는 대신 정치적 인물·사건·제도가 `HAS_TOPIC -> 정치`와
`ASSOCIATED_WITH_POLITY -> 해당 국가`를 함께 갖도록 연결한다. 이렇게 해야 후보 검색
결과가 실제 대상과 근거로 설명된다.

관계의 강도를 원문보다 높이지 않는다.

```text
"화성 축조를 추진했다" -> PROMOTED_CONSTRUCTION
"화성을 직접 건설했다" -> BUILT
```

첫 문장만으로 `BUILT`를 만들지 않는다.

## 5. 상세화 원칙

목표는 가능한 한 세밀하게 분류하는 것이지만 우선순위는 정확성이다.

> 원문 또는 승인된 결정 규칙이 명시적으로 뒷받침하는 가장 깊은 단계까지만 연결한다.

- 날짜·왕대처럼 구조화할 수 있는 값은 기간표와 규칙으로 분류한다.
- 본문의 인물·사건·국가·지역·문화재 언급은 NER로 후보를 찾을 수 있다.
- NER 결과는 Entity Linking을 통과해야 canonical ID가 된다.
- 관계는 Relation Extraction 후 근거 검증을 통과해야 한다.
- RelationCandidate의 타입 조합·빈도·근거 회수율을 EDA한 뒤 Predicate 계약을 확정한다.
- 알 수 없으면 더 넓은 상위 분류만 유지하거나 `UNKNOWN`으로 남긴다.
- 미분류보다 오분류가 더 위험하다.

## 6. 검증 상태

entity resolution과 관계 검증 상태는 분리해서 관리한다.

```text
Entity link: ACCEPTED | AMBIGUOUS | UNRESOLVED | REJECTED
Relation:    VERIFIED | PENDING | REJECTED | CONFLICT
LLM proposal: PROPOSED
Production SourceRecord: ACCEPTED
```

상태값은 위처럼 대문자로 정규화한다. 원천 수집·파싱 단계의 raw 상태값은 staging에
원문 그대로 보존하고 production `SourceRecord`의 정규화 상태와 섞지 않는다.

오답 후보 검색에는 `ACCEPTED` canonical 대상과 `VERIFIED` 관계만 사용한다.
LLM의 confidence 값만으로 `VERIFIED` 처리하지 않는다.

## 7. 오답 후보 검색의 최소 계약

기본 검색 형태는 다음과 같다.

```text
(correct:CanonicalEntity)
  -[VERIFIED relation]->(shared anchor)
  <-[VERIFIED relation]-(candidate:CanonicalEntity)
```

검색 팀에는 임의 관계 전체가 아니라 허용된 anchor relation 목록을 제공한다.

- 같은 `EntityType`은 필수 조건
- 동일 canonical ID와 승인 별칭은 제외
- `조선`, `정치`, `Person`처럼 degree가 큰 노드 하나만 공유한 후보는 제한
- 세부 역할·정치체·세부 시대·구체 사건 유형 등 설명 가능한 anchor를 우선 제공
- ITKC의 미확정 `사건인물`과 근거 없는 AKS `relatedArticles`는 anchor에서 제외

후보 경로는 숫자 hop이 아니라 승인된 `path_pattern_id`로 선택한다. 문제 생성/검색
계층은 난이도를 path pattern, `specificity_level`, `taxonomy_distance` 조건으로 변환한다.
물리 hop 수는 적재 최적화에 따라 달라지므로 후보 자격·난이도·결과 계약에 사용하지 않는다.
각 path pattern은 허용 축·관계 방향·계층 탐색 규칙을 카탈로그에 고정하며 임의의
`RELATED_TO*` 탐색은 허용하지 않는다.

Neo4j 결과는 최소한 다음을 반환한다.

```json
{
  "candidate_canonical_id": "...",
  "candidate_name": "...",
  "entity_type_id": "person",
  "shared_anchors": [
    {
      "axis": "role_context",
      "path_pattern_id": "SHARED_ROLE_ASSIGNMENT",
      "role_id": "role:king",
      "role_name": "왕",
      "polity_id": "polity:joseon",
      "polity_name": "조선",
      "correct_assignment_id": "assignment:...",
      "candidate_assignment_id": "assignment:...",
      "taxonomy_distance": null,
      "correct_evidence_ids": ["..."],
      "candidate_evidence_ids": ["..."]
    }
  ],
  "rag_search_terms": ["후보 대표명", "승인 별칭", "공유 맥락"],
  "verification_status": "VERIFIED"
}
```

후보 점수, PageRank, Personalized PageRank, 최종 후보 수는 이 계약에 포함하지 않는다.

## 8. 문서 구성

| 문서 | 내용 |
|---|---|
| `01_raw_data_eda.md` | 3종 원천의 실제 필드와 한계 |
| `02_exam_pattern_analysis.md` | 전체 문제 생성 흐름에서 Neo4j의 경계 |
| `03_storage_and_material_contract.md` | 입력 anchor와 후보 반환 계약 |
| `04_etl_and_entity_resolution.md` | 정규화·NER·동명이인 해소·근거 검증 ETL |
| `05_neo4j_generation_schema.md` | 목표 노드·관계 스키마 |
| `06_distractor_and_difficulty.md` | 공통 노드 기반 후보 조회 규칙 |
| `07_runtime_generation_pipeline.md` | 런타임 연결 지점과 실패 처리 |
| `08_validation_and_roadmap.md` | 품질 gate와 구현 순서 |

`neo4j_preprocessing_eda_notes.md`와 `neo4j_관계_정규화_점검.md`는 현재 구현 감사 문서다.
목표 스키마가 이미 구현됐다는 뜻이 아니다.

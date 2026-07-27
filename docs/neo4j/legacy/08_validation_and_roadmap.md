# 08. 검증 기준과 구현 로드맵

> 레거시 문서: 이전 스키마 기준 로드맵이다. 현재 구현 계획으로 사용하지 않는다.
>
> 상태: `TARGET-ROADMAP`
> 목표: 잘못된 세부 관계가 챗봇 지식 조회나 오답 후보 검색으로 유출되지 않도록 단계별 gate를 고정한다.

## 1. 구현 우선순위

### 단계 A. 핵심 골격과 EDA seed 확정

1. top-level Topic 10개와 안정 ID를 seed로 만든다.
2. top-level Era 10개와 안정 ID를 seed로 만든다.
3. `EntityType`, `PersonRole`, `Polity`, `Region`, 세부 분류를 서로 다른 축으로 분리한다.
4. `조선 전기/후기` 등 시대 경계표와 버전을 정한다.
5. EDA 산출물 형식과 최소 EntityType·Predicate seed를 정한다.
6. seed는 extractor 제약용이며 최종 카탈로그가 아님을 명시한다.

완료 조건:

- 같은 이름의 Era와 Polity가 별도 ID다.
- `사상·종교` 표기가 하나의 ID로 정규화된다.
- EDA 후보와 production 승인 관계가 저장·조회 계층에서 분리된다.

### 단계 B. raw staging 보완

1. AKS list/detail, ITKC 4개 CSV, 시소러스 CSV를 release/hash와 함께 읽는다.
2. `itkc_people.csv`를 독립 인물 마스터로 포함한다.
3. 중복 행, parser error, 원천 URL과 논리 레코드를 구분한다.
4. 원천별 namespace가 포함된 `source_record_id`를 만든다.

완료 조건:

- 원천 파일 hash와 레코드 수가 재현된다.
- 동일 숫자 ID가 다른 원천에서 충돌하지 않는다.
- 관계 endpoint에 없는 ITKC 인물도 staging에 존재한다.

### 단계 C. 이름 정규화와 Entity Linking

1. 대표명·별칭·한자·자·호를 `EntityName` 후보로 만든다.
2. 인물·사건·용어별 AKS 검색 후보를 수집한다.
3. 이름, 한자, 생몰년, 시대, 본관, 관계 이웃 등으로 candidate score를 만든다.
4. 강한 충돌 규칙을 먼저 적용한다.
5. 결과를 `ACCEPTED/AMBIGUOUS/UNRESOLVED/REJECTED`로 나눈다.

완료 조건:

- 이름 하나만으로 자동 병합된 건이 없다.
- 동명이인 gold set에서 오병합이 허용 기준 이하이다.
- 승인 별칭이 별도 canonical 후보로 검색되지 않는다.

### 단계 D. NER·관계 EDA와 카탈로그 재확정

1. 구조 필드와 원천 사전을 우선 사용한다.
2. AKS 본문에 hybrid NER을 적용한다.
3. Entity Linking을 통과한 endpoint로 raw `RelationCandidate`를 생성한다.
4. 모든 후보에 정확한 quote/offset과 source record를 요구한다.
5. `subject type × raw relation × object type`과 빈도·coverage·근거 회수율을 집계한다.
6. 관계 방향·역관계·대칭성·다의성·시간 문맥을 표본 검수한다.
7. `Work`·`Polity` EntityType, Place–Region, DetailClass–Topic 매핑 정책을 결정한다.
8. Person–Person/Organization과 Event·Institution·Heritage·Work Predicate를 재정의한다.
9. Predicate별 endpoint allowlist와 `predicate_catalog_version`을 승인한다.
10. 알 수 없는 판정은 `UNKNOWN`, 복수 identity는 `AMBIGUOUS`로 둔다.

완료 조건:

- 원천 관계 후보 분포와 표본 검수 결과가 재현된다.
- 승인된 모든 Predicate에 정의·방향·endpoint allowlist·근거 기준이 있다.
- 카탈로그에 없는 taxonomy/Predicate ID가 production에서 0건이다.
- evidence quote가 원문에 없는 후보가 production으로 넘어가지 않는다.
- `추진/명령/직접 건설/중건` 의미가 구분된다.

### 단계 E. 검증과 production projection

1. quote/offset, entity link, type signature, 날짜·시대 충돌을 code gate로 확인한다.
2. claim-evidence 검증을 NLI 또는 별도 검증 LLM/규칙으로 수행한다.
3. 충돌과 근거 부족은 review queue로 보낸다.
4. `ACCEPTED` 대상과 `VERIFIED` 관계만 production Graph에 적재한다.

완료 조건:

- 모든 검색 edge에 `relation_id`, 근거, 정책 버전이 있다.
- `PENDING/CONFLICT/REJECTED` 관계가 검색 query에서 0건이다.
- ITKC `사건인물`이 구체 역할 근거 없이 승격된 건이 0건이다.
- AKS `relatedArticles`가 typed fact로 승격된 건이 0건이다.

### 단계 F. 공통 Graph 조회 계약

1. 조회 기준 canonical ID를 입력으로 받는다.
2. 챗봇 조회에는 방향·endpoint 타입이 맞는 VERIFIED typed relationship와 evidence ID를 반환한다.
3. 오답 후보 조회에는 entity type과 관계 의미가 같은 공통 Anchor를 조회한다.
4. broad anchor 단독 후보를 차단한다.
5. 후보별 `shared_anchors`, 정답·후보 양쪽 evidence ID, RAG 검색 맥락을 반환한다.
6. 검색 팀의 ranking/sampling 계층과 연결한다.

완료 조건:

- 챗봇 관계 결과마다 Predicate 방향·endpoint 타입·evidence ID를 재현할 수 있다.
- 동일 canonical 대상과 별칭이 후보에서 제외된다.
- 후보마다 선정 이유를 재현할 수 있다.
- 후보가 없어도 미검증 fallback을 사용하지 않는다.
- Graph 배포본 ID로 결과를 재현할 수 있다.

## 2. 필수 Graph 불변식

1. 모든 검색 대상에 유일한 `canonical_id`가 있다.
2. 같은 normalized name이 여러 canonical 대상을 가리킬 수 있다.
3. 모든 검색 대상에 검증된 `EntityType`이 하나 있다.
4. topic은 다중값을 허용한다.
5. era와 polity는 별도 축이다.
6. 서로 다른 축 사이에 `SUBCATEGORY_OF`를 사용하지 않는다.
7. 모든 검색 관계의 상태는 `VERIFIED`다.
8. evidence 없는 LLM 관계는 검색 Graph에 없다.
9. RoleAssignment의 polity/era는 근거가 있는 값만 존재한다.
10. 한 Graph 조회가 서로 다른 release의 관계를 섞지 않는다.
11. 모든 production 분류 관계가 대상 축의 `taxonomy_version`을 참조하며,
    `HAS_ENTITY_TYPE`은 승인된 `entity_type_catalog_version`을 참조한다.
12. 모든 production typed relationship가 승인된 `predicate_catalog_version`을 참조한다.
13. 검색용 상위 DetailClass 지름길 edge 유무가 `taxonomy_distance`와 후보 의미를 바꾸지 않는다.
14. 모든 후보 경로는 승인된 `path_pattern_catalog_version`의 pattern ID를 참조한다.

## 3. 품질 검증 세트

### 3.1 동명이인

- 같은 이름·다른 시대
- 같은 이름·다른 본관
- 같은 이름·다른 entity type
- 한자만 다른 인물
- 별칭과 대표명이 다른 동일 인물

측정값:

```text
false merge rate
false split rate
AMBIGUOUS recall
source별 canonical coverage
```

오답 후보 검색에서는 false merge가 특히 위험하므로 coverage보다 오병합 방지를 우선한다.

### 3.2 시대·국가·역할

- 조선 전기/후기 경계값
- 초기국가 Era와 부여/옥저/동예 Polity 구분
- 삼한과 마한/진한/변한 계층
- 한 인물이 서로 다른 시기·국가에서 맡은 역할
- `왕` 역할은 있으나 국가 근거가 없는 사례

### 3.3 관계 의미

- 관련 인물과 실제 참여자 구분
- 참여자와 지휘관 구분
- 건설·명령·추진·중건 구분
- 사건 발생지와 인물 활동지 구분
- 관련 문서 링크와 역사 Fact 구분

### 3.4 후보 검색

- 세부 anchor를 공유하는 정상 후보
- broad topic/era만 공유하는 과다 후보
- 동일 alias가 중복 후보가 되는 사례
- 관계 방향이 반대인 사례
- 정답/후보 한쪽 근거만 VERIFIED인 사례
- 후보 RAG 검색에서 동명이인이 섞이는 사례

## 4. 운영 지표

| 지표 | 의미 |
|---|---|
| canonical coverage | 원천 레코드 중 ACCEPTED canonical 연결 비율 |
| ambiguity rate | AMBIGUOUS 비율 |
| VERIFIED relation precision | 표본 검수에서 사실인 VERIFIED 관계 비율 |
| evidence exact-match rate | quote/offset이 원문과 일치하는 비율 |
| anchor coverage | canonical 대상별 검색 가능한 세부 anchor 수 |
| zero-candidate rate | 요청에서 후보가 하나도 없는 비율 |
| broad-only rate | broad anchor밖에 없는 대상 비율 |
| candidate RAG success rate | 반환 후보 중 근거 문서를 찾은 비율 |

관계 수 자체를 성공 지표로 삼지 않는다. 정확한 VERIFIED 관계와 실제 후보/RAG 성공률이
더 중요하다.

## 5. 수동 검토가 필요한 경우

- 같은 이름과 시대가 겹치지만 관계 이웃이 충돌한다.
- AKS 후보가 둘 이상이고 구분 근거가 없다.
- 시대 경계 정책에 따라 세부 Era가 달라진다.
- 원천 두 곳의 생몰년·국가·역할이 충돌한다.
- LLM과 verifier 판정이 다르다.
- 새 Predicate 또는 새 세부 taxonomy가 필요하다.
- EDA에서 기존 EntityType으로 설명되지 않는 `DOCUMENT/WORK/POLITY` 분포가 확인된다.

검토 전에는 더 넓은 상위 분류만 유지하거나 검색에서 제외한다.

## 6. MVP

첫 배포는 전체 역사를 한 번에 완성하려 하지 않는다.

1. top-level Topic/Era와 기본 EntityType
2. Person·Event 우선 canonical resolution
3. 조선 왕·국가·세부 시대의 RoleAssignment
4. ITKC의 안전한 인물 관계와 AKS 근거 보강
5. 사건의 세부 유형·시대·국가·지역
6. VERIFIED 공통 anchor 후보 API

MVP에서도 동명이인 차단과 evidence gate는 줄이지 않는다. 세부 taxonomy coverage만
단계적으로 넓힌다.

## 7. 완료 정의

- 문서와 구현에서 Neo4j가 문제 유형·난이도·선지 생성을 소유하지 않는다.
- 3종 원천의 모든 production 대상이 provenance와 canonical ID를 가진다.
- 동명이인은 이름만으로 병합되지 않는다.
- topic, era, polity, role, entity type이 분리되어 필요한 대상에 함께 연결된다.
- 분류와 관계는 원문이 지지하는 깊이와 강도를 넘지 않는다.
- LLM은 `UNKNOWN/AMBIGUOUS`를 반환할 수 있고 code gate를 우회할 수 없다.
- 후보 검색에는 VERIFIED edge만 쓰이며 결과마다 `shared_anchors`가 있다.
- EntityType·Predicate·Place–Region·DetailClass–Topic 정책이 EDA 보고서와 승인 버전으로 고정된다.
- PageRank·최종 랭킹·난이도는 검색/문제 생성 팀의 결정으로 남아 있다.

## 8. Neo4j 설계 결정 백로그

이 절은 확정 스키마가 아니다. 현재 문서와 예상 구현 사이에서 확인된 문제를 기록하고,
각 항목을 하나씩 검토한 뒤 승인된 결정만 앞 절의 스키마·불변식·조회 계약에 반영한다.
EDA 결과가 필요한 항목은 추측으로 확정하지 않는다.

| 순서 | 검토 항목 | 현재 문제 | 변경하지 않을 때의 영향 | 결정 상태 |
|---:|---|---|---|---|
| 1 | Anchor 축·속성·상태값 정합성 | canonical 축 이름을 `detail_class`로 통일하고 `semantic_detail`은 입력 alias로만 유지했다. `person_role` 단일 축과 `role_context` 복합 경로를 구분했으며 `specificity_level`을 Anchor 스키마에 추가했다. production 상태값은 도메인별 대문자 enum으로 통일하고 raw 상태값은 staging에 보존한다. | 상태값 enum과 축 이름이 query마다 달라지는 문제를 방지한다. | 계약 수정 완료 |
| 2 | broad Anchor 후보 차단 | 기존 후보 Cypher의 `OR`가 broad Anchor 여러 개만으로 통과시키던 문제를 specific Anchor 필수 `AND` 조건으로 수정했다. | 이후 cutoff와 최소 개수 정책을 잘못 설정하면 과다 후보가 생길 수 있으므로 검색 품질 테스트가 필요하다. | 계약 수정 완료·수치 EDA 대기 |
| 3 | Topic과 EntityType의 경계 | Topic의 `인물`, `사건`이 EntityType의 `Person`, `Event`와 의미상 중복될 수 있다. | 모든 인물·사건이 같은 broad Topic 허브에 연결되어 정보량 없는 공통점과 과다 후보를 만든다. | EDA·crosswalk 검토 필요 |
| 4 | EntityType 단일성 | 현재 불변식은 검색 대상마다 EntityType 하나를 요구하지만 `Work`, `Heritage`, `Concept`, `Polity`의 중첩·대표 타입 정책이 확정되지 않았다. | 같은 종류의 대상이 적재자 판단에 따라 다른 타입이 되어 동일 타입 후보 검색에서 서로 누락될 수 있다. | EDA 후 결정 |
| 5 | RoleAssignment 조회 계약 | `person_role`은 물리 Anchor 축으로 유지하고 후보 결과는 `SHARED_ROLE_ASSIGNMENT`, `SHARED_ROLE_ASSIGNMENT_ERA`의 `role_context`로 통일했다. 물리 hop 수는 계약에서 제거했다. | 실제 역할·국가·시대 근거가 부족하면 해당 pattern 후보가 감소하므로 EDA에서 coverage를 확인해야 한다. | 계약 수정 완료·coverage EDA 대기 |
| 6 | Polity·Place와 Anchor 매핑 | 검색 대상 `CanonicalEntity:Polity/Place`와 분류용 `Polity/Region` Anchor를 함께 둘 경우 1:1 매핑·검증·동기화 규칙이 필요하다. | 같은 조선·같은 장소를 나타내는 두 노드의 속성과 연결이 달라져 조회 경로에 따라 결과가 누락되거나 충돌한다. | EDA 후 결정 |
| 7 | typed relationship provenance 원본 | 직접 관계의 `evidence_ids`와 `RelationAssertion`·`SUPPORTED_BY` 경로가 공존하지만 어느 쪽이 원본인지, 언제 Assertion으로 승격하는지 명확하지 않다. | 관계를 수정·재검토할 때 근거와 직접 edge가 불일치하고, 관계 생성 이유와 검토 이력을 안정적으로 추적하기 어렵다. | 검토 대기 |
| 8 | Neo4j 제약과 import QA | ID 유일성, 필수 속성, 상태값, release 일관성을 DB 제약과 import QA 중 어디서 보장할지 구분이 부족하다. | 중복 canonical·relation ID나 근거 없는 관계가 적재되어 ID 조회와 병합 결과가 비결정적이 될 수 있다. | 검토 대기 |
| 9 | Typed Relation 카탈로그 | 실제 원천에서 추출 가능한 관계의 빈도·endpoint 타입·근거 품질을 확인하기 전에 Predicate를 고정할 수 없다. | 필요한 관계는 빠지고 같은 의미가 여러 Predicate로 분산되거나, 거의 나오지 않는 관계를 위해 불필요한 구조를 유지하게 된다. | EDA 후 결정 |
| 10 | 복합 의미 DetailClass와 RoleAssignment 중복 | `조선 후기 국왕:DetailClass`와 `왕+조선+조선 후기:RoleAssignment`처럼 같은 의미가 분류와 역할 문맥 두 경로로 표현된다. 복합 DetailClass를 허용할지, 파생 mapping으로만 둘지, RoleAssignment 경로로 단일화할지 정해야 한다. | 두 경로의 값·근거가 달라지면 같은 의미의 후보가 조회 pattern에 따라 포함되거나 누락되고 taxonomy distance·난이도·설명 근거가 흔들린다. | EDA·문제 생성 crosswalk 검토 후 결정 |

### 8.1 검토 원칙

1. 각 항목은 `문제 확인 → 선택지 비교 → 결정 → 문서 계약 통일 → 구현 검증` 순서로 처리한다.
2. 결정 전에는 기존 스키마를 확정안으로 간주하지 않는다.
3. EDA가 필요한 3·4·6·9·10번은 분포와 표본 근거를 확보한 뒤 결정한다.
4. 1·2·5·7·8번은 문서 계약과 Neo4j 기능을 기준으로 먼저 검토할 수 있다.
5. 문제 생성의 복수 정답 판정, 지문 입력, 재시도 정책은 이 백로그의 범위에 포함하지 않는다.

권장 검토 순서는 `1 → 2 → 5 → 7 → 8 → EDA → 3 → 4 → 10 → 6 → 9`다.

### 8.2 확정된 문제 생성 crosswalk

문제 생성팀 검토를 반영해 범용 Graph Mermaid는 유지한다. 문제 생성 계약의 용어는
다음처럼 기존 구조에 대응한다.

```text
QuestionTarget  -> CanonicalEntity
SemanticClass  -> DetailClass
parent/subgroup -> DetailClass SUBCATEGORY_OF 계층
Fact            -> typed relationship 또는 RelationAssertion
Evidence        -> EvidenceSpan
```

문제 생성의 9개 `TopicType`과 54개 `QuestionFacet` 계약은 범용 Graph 노드로 그대로
복제하지 않고 현재 요청 계약에 맞게 변환할 수 있다. 취약점 분석용 Topic 10개와 Era
10개는 그대로 유지한다.

후보 조회는 물리 hop 수를 계약으로 사용하지 않는다. 난이도를 결정한 외부 계층은 승인된
`allowed_path_pattern_ids`, specificity, taxonomy distance 조건을 전달한다. Neo4j는
`path_pattern_catalog_version`의 축·방향·검증 규칙을 적용하고 pattern ID와 의미 거리를
반환한다. 이 결정은 Mermaid의 노드·edge 종류를 추가하지 않는다.

상위 DetailClass는 첫 구현에서 검색 지름길로 직접 연결하지 않고 검증된 세부 분류와
`SUBCATEGORY_OF` 계층으로 조회한다. 향후 성능상 지름길을 추가하더라도
`DERIVED_ANCESTOR`로 구분하고 taxonomy distance 계산에서는 제외한다.

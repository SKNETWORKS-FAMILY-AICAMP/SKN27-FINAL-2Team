# 08. 검증 기준과 구현 로드맵

> 기준일: 2026-07-16
> 상태: 문서 계약 갱신 완료, 코드·데이터 적용 전.

이 문서는 Neo4j·snapshot registry·RAG·모델 gateway·운영 DB가 맞물리는 지점의 검증
조건과 인수 순서를 정의하는 **경계 인터페이스 계약**이다. 각 외부 시스템의 내부 구현을
지시하거나 그 변경을 승인하는 문서가 아니다. 실제 변경은 시스템별 소유자 승인과 별도 구현
계획을 따른다.

이 문서에서 `question target`은 출제 기준 대상, `donor target`은 오답 재료를 제공하는 다른
canonical 대상이다. 일반 donor는 같은 exact primary parent를 직접 공유하는 2홉 경로에서만
시작한다.

## 1. 현재 준비도

| 영역 | 현재 상태 | 목표 계약과의 차이 |
|---|---|---|
| raw 3종 확보 | 있음 | 품질 gate와 snapshot manifest 필요 |
| 현재 Neo4j ETL | 있음 | 공통 canonical target, Fact, SemanticClass, QuestionUse 없음 |
| 현재 Neo4j 데이터 | 생성 CSV 있음, 라이브 상태 별도 | 목표 스키마로 재생성·적재되지 않음 |
| RAG | 챗봇용 자유 텍스트 검색 있음 | canonical_id + fact_id + EvidenceSpan 제한 검색 없음 |
| 문제 생성 앱 | 기존 저장 문항 선택 기능 있음 | Neo4j·RAG·API·sLLM 생성 파이프라인 없음 |
| 풀이·채점 | choice_id shuffle·서버 채점 있음 | 정답 정보 선노출과 이중 진실원 점검 필요 |
| 취약점 분석 | topic·era 문자열 기반 | taxonomy ID·version 고정 필요 |

따라서 현재 DB에서 Cypher 하나만 추가해 문제 생성 계약을 만족시킬 수 있는 상태는 아니다.

## 2. 단계별 구현 순서

### 단계 A. 계약과 카탈로그 고정

- 이 문서 묶음을 v1 기준으로 검토·승인한다.
- `TopicType`, `PredicateType`, `QuestionFacet` 카탈로그의 초기 범위를 정한다.
- 기존 “TopicType 9개, Facet 54개” 고정값을 제거한다.
- 취약점 분석 topic 10개, era 10개의 ID와 표시명을 분리한다.
- schema, registry, taxonomy version 규칙을 정한다.
- `GraphSnapshot` release manifest에 `graph_snapshot_id`, `schema_version`,
  `source_manifest_hash`, `snapshot_payload_hash`와 포함 revision manifest를 고정한다.
- 한 generation job의 모든 graph read가 같은 snapshot과 명시적 revision ID를 사용하고, 도중에 latest로
  전환하지 않는 규칙을 정한다.

완료 조건:

```text
같은 용어가 문서마다 다른 의미로 쓰이지 않음
모든 catalog item에 ID, version, status가 있음
승인된 GraphSnapshot이 source·schema·revision manifest hash를 유일하게 고정함
```

### 단계 B. raw ingestion 보완

- 세 원천의 파일 hash와 schema manifest를 만든다.
- `itkc_people.csv`를 독립 입력으로 추가한다.
- AKS·ITKC·시소러스 `SourceRecord` 합성 키를 만든다.
- parsing error, 중복, endpoint 누락을 격리한다.
- RAG document/chunk/span ID를 안정적으로 생성한다.
- source manifest와 graph release에 파일 hash·source version·content hash를 연결한다.

완료 조건:

```text
모든 dedup 논리 SourceRecord가 파일 역할·논리 ID·source version으로 유일함
모든 원시 행이 staging manifest의 논리 SourceRecord key로 추적됨
관계 endpoint source ID 누락이 0
RAG chunk ID와 content hash를 재실행해도 동일
동일 GraphSnapshot 안의 SourceRecord·EvidenceSpan이 같은 source release를 참조함
```

### 단계 C. canonical entity resolution

- AKS EID, ITKC person/event ID, 시소러스 term ID의 crosswalk를 만든다.
- 이름·한자·시대·자·호·본관·관계 이웃을 사용한다.
- 동명이인과 의미가 다른 동명 용어를 분리한다.
- accepted 결과만 `CanonicalEntity`로 적재한다.
- 정식명·별칭·한자명·이명은 `EntityName`으로 분리하고 name kind, script,
  `review_status`, SourceRecord provenance를 둔다. canonical 해소 판정은
  `REFERS_TO.match_status`가 소유한다.
- 한 이름이 여러 대상을 가리키면 ambiguity set으로 남기고 런타임에서 자동 선택하지 않는다.
- 합성 target은 승인된 provenance와 생성 규칙 version이 있는 항목만 canonical로 적재한다.
  문제 생성 런타임은 합성 target을 새로 만들거나 기존 대상을 합치지 않는다.
- 출제 가능 검증을 통과한 엔터티에만 `QuestionTarget` 역할 라벨을 붙인다.

완료 조건:

```text
`(graph_snapshot_id, canonical_id)` unique
동일 SourceRecord가 둘 이상의 active CanonicalEntity로 해소되지 않음
`match_status=candidate|conflict`인 항목은 문제 생성 조회에서 제외
alias EntityName 자체가 question target 또는 donor target으로 반환되지 않음
ambiguous name은 명시적 canonical ID 없이는 자동 해소되지 않음
approved synthetic target마다 provenance와 rule version이 있음
```

### 단계 D. donor용 분류 구축

- raw 분류와 `SemanticClass`의 versioned crosswalk를 만든다.
- parent와 subgroup을 구분한다.
- question target을 parent에 직접 연결한다.
- broad class는 `donor_eligible=false`로 둔다.
- `CLASSIFIED_AS` provenance와 review status를 보존한다.

완료 조건:

```text
모든 active QuestionTarget이 primary TopicType 1개를 가짐
donor 조회 parent가 specific이고 검증됨
subgroup은 parent로 SUBCLASS_OF됨
```

### 단계 E. Fact와 EvidenceSpan

- AKS 속성과 본문에서 FactCandidate를 추출한다.
- ITKC 인물 관계를 승인 Predicate로 변환한다.
- ITKC 사건-인물 관계는 역할 보강 전 answer ineligible로 둔다.
- subject/object canonical endpoint를 해소한다.
- 근거 span이 Predicate 의미와 endpoint를 모두 지지하는지 검증한다.
- verified Fact와 EvidenceSpan reference만 production에 적재한다.
- 정규화 Fact binding hash와 EvidenceSpan content hash를 GraphSnapshot manifest에 고정한다.
- Fact·endpoint·EvidenceSpan이 서로 다른 snapshot/revision을 섞어 참조하지 못하게 한다.

완료 조건:

```text
Fact마다 SUBJECT 1, PREDICATE 1
Fact마다 entity OBJECT 관계 또는 typed object value 중 정확히 하나
verified Fact마다 verified `SUPPORTED_BY` edge와 verified EvidenceSpan 1개 이상
EvidenceSpan의 RAG document/chunk/hash/version이 실제 저장소와 일치
Fact binding hash가 snapshot manifest와 일치하고 cross-snapshot 참조가 0
```

### 단계 F. QuestionUse와 교육과정 분류 binding 컴파일

- Facet Registry의 predicate·target role·answer role·answer shape 규칙을 읽는다.
- question target endpoint와 Fact endpoint 일치를 검증한다.
- `target_role`, `answer_role`, `answer_shape`, Predicate subject domain·object range,
  literal datatype·unit, qualifier·cardinality를 함께 검증한다.
- `answer_route=GENERIC_DONOR`일 때만 donor parent를 하나 선택하고 question target의
  직접 분류를 확인한다. 관계형 membership route에는 dummy parent를 만들지 않는다.
- active·verified QuestionUse만 Neo4j에 적재한다.
- 정책 레지스트리에 각 QuestionUse revision의 taxonomy-versioned
  `QuestionClassificationBinding`을 컴파일해 primary curriculum topic·era와 선택적
  secondary·detail 분류를 고정하고 binding hash를 만든다. 이 binding은 Neo4j 노드가 아니다.
- 런타임 seed에 topic/era 조건이 있으면 이 binding을 **hard filter**로 사용한다. 요청 taxonomy
  version과 binding version이 다르거나 분류가 없으면 추론·fallback하지 않고 제외한다.
- 규칙 version이 바뀌면 이전 QuestionUse의 `review_status=stale` 또는
  `status=retired`를 명시한다.

완료 조건:

```text
QuestionUse마다 TARGET/FACET/FACT가 각각 1개
GENERIC_DONOR QuestionUse만 PARENT가 정확히 1개
target_role endpoint가 실제 TARGET과 동일
role·shape·Predicate domain/range·typed value 계약 위반 0
active QuestionUse마다 정책 레지스트리에 같은 taxonomy version의 active QuestionClassificationBinding 1개
binding revision ID·payload hash가 정책 레지스트리 안에서 유일하고 재실행해도 동일
빈 근거의 QuestionUse가 없고 GENERIC_DONOR QuestionUse에는 빈 donor class가 없음
```

### 단계 G. donor 조회와 난이도

- exact primary parent를 직접 공유하는 2홉 donor repository query를 먼저 실행한다.
- 동일 TopicType·Facet·target role·answer role·answer shape·Predicate domain/range·verified Fact
  조건을 적용한다.
- 그 pool에서 alias·merged·상하위·사건/사건군·group membership·`PART_OF` 중복을 제외한다.
- 같은 `EntityGroup`의 직접 membership 공유와 question target-donor 사이의 직접 1홉
  `PART_OF`·`INSTANCE_OF`만 제외 판정에 사용한다.
- group membership을 직접 묻는 Facet은 Facet Registry의 별도 route·domain/range·proof 계약을
  사용하고, 일반 donor 부족 fallback으로 사용하지 않는다.
- subgroup·시대·정권 근접도 특징을 반환한다.
- 난이도별 4개 미만이면 resample/skip한다.

완료 조건:

```text
variable-length donor 경로 0
broad parent donor 0
donor Fact endpoint mismatch와 role·shape·domain mismatch 0
group/part-of 관계로 일반 donor pool을 확장한 결과 0
난이도 fallback 위장 0
```

### 단계 H. bounded RAG와 생성 서비스

- RAG query에 `question_target_entity_id`, QuestionUse·Fact·Predicate·EvidenceSpan revision
  IDs, Fact canonical hash와 source binding hash를 추가한다.
- 정답·donor evidence purpose를 분리한다.
- 지문 전용 API의 출력 schema를 고정한다.
- pre-validator 뒤 모든 `option_token`과 `correct_option_token`을 고정한다.
- donor별 mismatch rule ID·proof payload·proof hash·version을 생성 작업에 저장한다. 명시적
  `FALSE` verdict만 option에 사용하고 Graph에 관계가 없거나 증명되지 않은 경우는 `UNKNOWN`으로
  폐기한다.
- job-scoped opaque `option_token`을 사용하고, 정답 token·truth·question/donor 역할·mismatch
  verdict·semantic ID를 제거한 대칭 redacted view만 sLLM에 전달한다.
- sLLM은 받은 opaque token을 보존하면서 표현만 조립한다. token 매핑과 answer key는 서버만
  보유한다.
- post-validator와 deterministic shuffle을 구현한다.
- append-only `GenerationAttempt` 원장에 random seed, attempt index, retry/skip event, 선택된
  donor/reserve 순서, snapshot과 revision IDs, classification binding, Fact/Evidence/proof hash,
  option provenance, model 입출력 hash, rendered question·option hash를 저장한다.

완료 조건:

```text
RAG가 허용 target/Fact 밖의 근거를 반환하지 않음
모델이 새 Fact·option·정답 ID를 만들지 않음
모델 입력과 출력에 answer key 또는 내부 semantic 역할 정보가 없음
UNKNOWN proof가 option으로 채택된 사례 0
정답 유일성 검증 통과
동일 snapshot·revision IDs·seed에서 선택·retry·shuffle 순서 재현
모델 render 재실행 drift가 저장된 rendered hash로 검출됨
```

### 단계 I. 운영 DB와 취약점 분석

- 생성 문항 버전과 모든 provenance를 저장한다.
- 정답의 단일 진실원을 `correct_option_token`으로 정한다.
- 클라이언트 문제 시작 응답에서 정답 정보를 제거한다.
- `primary_curriculum_topic_id`, `secondary_curriculum_topic_ids`,
  `primary_curriculum_detail_topic_ids`, `secondary_curriculum_detail_topic_ids`,
  `primary_curriculum_era_id`, `secondary_curriculum_era_ids`,
  `primary_curriculum_detail_era_ids`, `secondary_curriculum_detail_era_ids`,
  `taxonomy_version`을 생성 시 고정한다.
- 선택 시 사용한 `QuestionClassificationBinding` ID·hash를 문항 버전에 고정하고 retry에서
  재분류하지 않는다.
- option별 source QuestionUse·Fact·EvidenceSpan·proof와 rendered hash를 provenance로 저장한다.
- 응답·풀이 시간·정답률을 저장된 분류로 집계한다.

## 3. 필수 Graph 불변식

배포 전에 다음을 전수 검사한다.

1. 모든 `*_revision_id`의 전역 중복이 0이고, CanonicalEntity·EntityName·Fact·EvidenceSpan의
   `(graph_snapshot_id, logical_id)` 중복이 0이다.
2. 승인된 `GraphSnapshot` 하나가 schema·source·revision manifest hash를 고정한다.
3. CanonicalEntity, EntityName, SemanticClass, Fact, EvidenceSpan, QuestionUse 사이
   cross-snapshot revision 참조가 0이다.
4. active EntityName은 canonical 대상 하나에 해소되며, ambiguous name은 자동 선택 대상이
   아니다. alias name record 자체에는 QuestionTarget 역할이 없다.
5. synthetic QuestionTarget은 승인 provenance와 생성 rule version을 갖고 원천 target과
   우발적으로 merge되지 않는다.
6. active QuestionTarget의 primary TopicType이 정확히 하나다.
7. parent/subgroup 관계 level과 `SUBCLASS_OF` 방향이 맞고 donor parent는 broad class가 아니다.
8. verified Fact의 subject·predicate와 entity/literal object 배타 cardinality가 맞다.
9. Predicate subject domain·object range, literal datatype·unit, qualifier·cardinality가 실제 Fact와
   일치한다.
10. verified Fact에 verified `SUPPORTED_BY` edge와 verified EvidenceSpan이 최소 하나이며
    Fact binding hash와 EvidenceSpan content hash가 pinned snapshot과 일치한다.
11. QuestionUse의 TARGET/FACET/FACT가 각각 하나이고, `GENERIC_DONOR` route만 PARENT가
    정확히 하나다. 모든 참조는 같은 snapshot의 revision이다.
12. QuestionUse `target_role` endpoint가 TARGET과 일치하고 `answer_role`·`answer_shape`·
    `answer_domain_id`가 Facet signature와 실제 answer binding에 모두 일치한다.
13. 일반 donor query는 같은 exact primary parent를 직접 공유하는 2홉에서 시작하고 question
    target과 donor target의 canonical ID가 다르다.
14. donor target은 alias·merged canonical, 상하위 개념, 사건/사건군, group membership,
    `PART_OF` 중복이 아니며 donor Fact endpoint가 donor canonical ID와 일치한다.
15. `SUBCLASS_OF*`, `RELATED_TO*`, `PART_OF*`를 일반 donor 확장에 사용하는 production query가
    없고, 제외 검사는 검증된 직접 membership·직접 1홉 관계로 제한된다.
16. group membership Facet은 Registry에 승인된 별도 route·role·shape·domain·proof 계약을
    사용한다.
17. option으로 승인된 mismatch proof verdict는 전부 `FALSE`이고 `UNKNOWN`은 0이다.

## 4. 서비스 검증 계층

### 4.1 사전 검증

- generation job이 승인된 GraphSnapshot과 필요한 revision IDs·manifest hash를 pin함
- 입력 이름이 EntityName resolution을 거쳐 유일한 canonical question target으로 해소됨
- seed topic/era가 있으면 같은 taxonomy version의 QuestionClassificationBinding을 hard filter로
  통과하고 해당 binding ID·hash가 retry 전체에 고정됨
- 정답 Fact·근거의 status와 payload/content hash가 검증됨
- question target과 donor target의 role·shape·Predicate domain/range·typed value 계약이 모두 맞음
- donor가 exact parent 2홉에서 왔고 group/part-of 제외 규칙을 통과함
- donor가 자신의 문맥에서 참임
- question target 문맥에 대입한 mismatch proof verdict가 명시적 `FALSE`임
- donor 수와 난이도 조건 충족
- option truth 분포와 정답 유일성 충족
- 모든 `option_token`과 `correct_option_token`을 모델 호출 전에 고정함

donor Fact가 다른 대상에게 참이라는 사실만으로 question target에게 자동으로 거짓은 아니다.
Graph에 같은 관계가 없다는 이유도 거짓 증명이 아니다. Predicate/Facet별 mismatch rule로
명시적 `FALSE`를 증명하지 못하면 `UNKNOWN`으로 폐기한다.

### 4.2 사후 검증

- 모델이 echo한 opaque token 집합이 입력과 같고 누락·중복·unknown token이 없음
- 내부 option/canonical/Fact ID, truth, 정답·question/donor 역할이 모델 입출력에 노출되지 않음
- 새 역사 주장 없음
- 지문에 answer leak 없음
- 중복·문법 단서·길이 편향 없음
- 발문의도·유형·answer shape 일치
- correct option 정확히 하나
- normalized rendered question·option hash와 option provenance가 완전함
- retry/skip event, attempt index, display order가 append-only 원장에 기록됨

## 5. 테스트 세트

### 5.1 원천·EntityName·canonical

- AKS/ITKC/시소러스가 같은 대상을 가리키는 positive crosswalk
- 동명이인과 동명 용어 negative crosswalk
- `itkc_people.csv`에만 존재하는 인물
- raw `relatedArticles`가 Fact로 승격되지 않는 사례
- 정식명과 별칭이 같은 canonical ID로 해소되는 사례
- 하나의 별칭이 여러 canonical 대상과 충돌해 자동 선택되지 않는 사례
- alias EntityName이 question target 또는 donor target으로 반환되지 않는 사례
- 승인된 synthetic target은 조회되지만 runtime synthetic 생성은 거부되는 사례
- synthetic target이 유사한 원천 target과 우발적으로 merge되지 않는 사례

### 5.2 Snapshot·Fact·QuestionUse

- 모든 조회가 같은 GraphSnapshot·revision에 pin되는 정상 사례
- job 도중 revision 변경 또는 pinned snapshot 부재 시 fail/skip하는 사례
- question target, donor target, Fact, EvidenceSpan의 cross-snapshot 조합이 거부되는 사례
- Fact payload 또는 EvidenceSpan content를 변조해 hash 검증이 실패하는 사례
- question target이 subject인 Fact
- question target이 object인 Fact
- 하나의 Fact가 두 Facet/역할로 투영되는 사례
- stale EvidenceSpan으로 QuestionUse가 비활성화되는 사례
- Predicate subject domain·object range mismatch가 거부되는 사례
- entity/literal 배타성, literal datatype·unit, qualifier·cardinality 위반이 거부되는 사례
- answer role 또는 answer shape가 Facet 계약과 다른 QuestionUse가 거부되는 사례

### 5.3 QuestionClassificationBinding

- seed topic·era hard filter를 모두 만족하는 positive 사례
- topic 또는 era가 달라 제외되는 negative 사례
- 요청 taxonomy version과 binding version 불일치가 거부되는 사례
- 분류가 없는 QuestionUse를 추론이나 broad fallback 없이 제외하는 사례
- retry 후에도 같은 binding ID·hash가 유지되는 사례
- binding payload 변조가 hash 검증에서 탐지되는 사례

### 5.4 donor와 group/part-of

- 정조와 다른 조선 국왕의 exact parent 2홉
- 정조와 영조의 subgroup 공유 랭킹
- 같은 alias/canonical ID 제외
- broad class만 공유하는 donor 제외
- donor Fact endpoint가 donor canonical ID와 다른 사례 제외
- exact parent 2홉 pool을 만든 뒤 group/part-of 중복을 제외하는 사례
- `SUBCLASS_OF*`, `RELATED_TO*`, `PART_OF*`로 일반 donor가 추가되지 않는 사례
- group membership Facet만 승인된 별도 route를 사용하고 일반 donor 부족 fallback은 거부하는 사례

### 5.5 mismatch proof

- donor에게 참이고 question target에는 규칙으로 `FALSE`가 증명되어 채택되는 사례
- Graph에 관계가 없을 뿐이어서 `UNKNOWN`으로 폐기되는 사례
- donor와 question target 모두에게 참일 수 있어 `UNKNOWN`으로 폐기되는 사례
- proof payload·rule version·proof hash 변조가 탐지되는 사례
- verdict가 누락되거나 `UNKNOWN`인 option이 pre-validator에서 거부되는 사례

### 5.6 생성·보안·재현

- donor 부족 시 skip event와 attempt index가 저장되는 사례
- 지문 API가 answer field를 반환하면 실패
- sLLM이 option을 추가하면 실패
- sLLM이 opaque token을 누락·중복·변조하면 실패
- sLLM redacted view에 correct ID, truth, question/donor 역할, semantic ID가 없음을 검사하는 사례
- shuffle 후 `correct_option_token` 유지
- 동일 snapshot·revision·version·seed에서 QuestionUse, donor/reserve, retry, shuffle 순서가 같은 사례
- 모델 재호출 결과가 달라질 때 stored rendered hash로 drift를 검출하는 사례
- 각 option의 QuestionUse·Fact·EvidenceSpan·proof provenance가 완전한 사례
- stimulus 폐기와 retry 전이 사유가 append-only 원장에 남는 사례
- taxonomy version별 취약점 집계 재현

## 6. MVP 범위

포함:

- raw 3종 SourceRecord, EntityName, canonical 해소
- versioned GraphSnapshot·revision·release manifest와 동일 snapshot read pin
- 제한된 TopicType·SemanticClass catalog
- binary atomic Fact와 EvidenceSpan
- 제한된 active Facet·QuestionUse·QuestionClassificationBinding
- FACT_STATEMENT·ENTITY answer shape
- positive single-answer `select_correct_statement`
- exact parent 2홉 donor 조회와 group/part-of 제외
- text stimulus
- 쉬움·보통·어려움 donor 정책
- bounded RAG, `FALSE` proof, opaque option token 고정·조립, 서버 채점
- append-only attempt ledger, rendered hash, option provenance

제외:

- 복합 n-ary Fact 전부
- 이미지·지도·연표 생성 전 범위
- PathPattern/PathInstance/Blueprint Graph 노드
- 모든 문제 유형 자동 생성
- `select_incorrect_statement`와 복합 truth 분포
- 모델이 만든 Fact 자동 승인
- broad taxonomy fallback

## 7. 배포 순서

1. 새 스키마를 기존 라이브 DB와 분리된 database 또는 namespace에 적재한다.
2. release hash와 catalog·taxonomy version이 고정된 GraphSnapshot manifest를 발행한다.
3. 제약, 불변식, Fact/Evidence hash, cross-snapshot 차단 전수 QA를 실행한다.
4. SourceRecord·EntityName crosswalk와 RAG EvidenceSpan 참조를 검증한다.
5. exact parent donor, group exclusion, role·shape·domain, classification binding, proof golden query를
   실행한다.
6. 생성 dry-run에서 redaction·opaque token·attempt ledger·render hash 계약 위반률을 측정한다.
7. 소량의 생성 문항을 검수한다.
8. 운영 DB에 immutable question version과 provenance를 저장한다.
9. 기존 문제 풀이 API와 연결한다.
10. 새 release 전환은 새 generation job 경계에서 원자적으로 수행하고 기존 job과 snapshot을
    섞지 않는다. 안정화 후에만 기존 Graph와 통합 또는 전환한다.

## 8. 완료 정의

다음을 모두 만족해야 “문제 생성용 Graph가 준비됐다”고 판단한다.

- raw 3종의 source ID가 canonical ID로 추적된다.
- 정식명·별칭은 EntityName을 통해 canonical ID로 해소되고 ambiguous name은 자동 선택되지
  않으며 synthetic target은 승인 provenance가 있다.
- generation job 전체가 하나의 GraphSnapshot·revision과 release hash에 pin되고 cross-snapshot
  조합이 차단된다.
- 검증된 Fact와 RAG EvidenceSpan이 연결된다.
- question target의 어느 Fact가 어느 Facet·role·shape로 출제 가능한지 QuestionUse로 조회되고
  Predicate domain/range까지 검증된다.
- seed topic·era hard filter가 QuestionClassificationBinding으로 적용되고 binding ID·hash가
  문항 버전에 고정된다.
- 같은 exact parent의 다른 canonical donor target에서 같은 Facet의 Fact를 2홉으로 찾은 뒤
  alias·계층·group/part-of 중복을 제외한다.
- group membership Facet은 승인된 별도 route를 사용하며 일반 donor 확장은 없다.
- donor Fact endpoint와 근거를 검증하고 question target mismatch가 명시적 `FALSE`일 때만
  option으로 채택한다. Graph absence는 `UNKNOWN`이다.
- 유형·난이도를 실제 가능한 조합 안에서 선택한다.
- 모델 호출 전에 `correct_option_token`이 고정되고, sLLM에는 answer key 없이 opaque token 기반 대칭
  redacted view만 전달된다.
- random seed, attempt index, retry/skip event, rendered hash, option provenance가 저장되어
  선택 과정과 결과 drift를 감사할 수 있다.
- 생성 문항과 채점·분석 provenance가 snapshot·binding·Fact/Evidence/proof hash까지 재현된다.

이 상태 전에는 현재 Neo4j를 문제 생성 계약을 만족하는 production Graph로 간주하지
않는다.

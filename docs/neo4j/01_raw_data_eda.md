# 01. 문제 생성용 raw 3종 EDA

> 상태: `EVIDENCE-SNAPSHOT`
> 기준일: 2026-07-16
> 원천 위치: `etl/raw_data`

## 1. 경로와 데이터군

프로젝트에 `preprocessing/raw_data`는 없다. 실제 raw는 `etl/raw_data`에 있고,
`etl/preprocessing/neo4j`는 처리 코드 영역이다.

문제 생성 Graph 설계에서 사용하는 3개 데이터군은 다음과 같다.

| 데이터군 | 파일 |
|---|---|
| 한국민족문화대백과사전 | `한국민족문화대백과사전/articles_list.jsonl`, `articles_detail.jsonl` |
| 한국고전종합DB 관계망 | `한국고전종합DB_관계망/itkc_people.csv`, `itkc_events.csv`, `itkc_person_relations.csv`, `itkc_event_relations.csv` |
| 한국역사용어시소러스 | `교육부 국사편찬위원회_한국역사용어시소러스 정보_20211028 (1).csv` |

대백과사전은 CSV가 아니라 JSONL이다.

`articles_errors.jsonl`, `itkc_errors.csv`, `itkc_raw_responses.csv`는 위 core 입력과
구분한다. 오류 파일은 수집·파싱 감사에, raw response는 URL 수집 상태 점검에만 쓰며
정상 역사 레코드나 authoritative 근거로 세지 않는다. `etl/raw_data`의 다른 자료군도
별도 승인 없이 이 v1 Graph의 네 번째 원천으로 편입하지 않는다.

## 2. 결론

세 원천만으로 다음 기반은 만들 수 있다.

- 원천별 안정 ID와 `SourceRecord`
- canonical entity 후보와 정규화된 `EntityName` 후보
- TopicType 후보
- 원천 분류·시대·연도 후보
- 사건군·조직군 membership과 직접 포함 관계 후보
- 관계와 구조화 속성 기반 `FactCandidate`
- RAG 문서와 EvidenceSpan 후보

반면 다음 값은 raw에 직접 존재하지 않는다.

- 검증 완료 `CanonicalEntity`
- 후보 검색용 parent/subgroup `SemanticClass`
- `QuestionFacet`
- `QuestionUse`
- `answer_shape`, `answer_role`, `target_role`
- `active`, `verified`, `donor_eligible` 상태
- 문제 유형·난이도 정책
- 취약점 분석용 topic 10개·era 10개 매핑
- 승인된 합성 target과 그 구성 규칙

따라서 raw를 그대로 Neo4j에 넣는 것과 문제 생성 Graph를 만드는 것은 다른 작업이다.

## 3. 한국민족문화대백과사전

### 3.1 규모와 키

| 파일 | raw 행 | 고유 `eid` |
|---|---:|---:|
| `articles_list.jsonl` | 75,935 | 75,835 |
| `articles_detail.jsonl` | 75,835 | 75,835 |

상세 JSON의 주요 필드는 다음과 같다.

```text
eid
headword
origin
field
primaryType
era
definition
body
reference
articleAliases
articleAttributes
relatedArticles
```

`eid`는 AKS 원천 ID다. list의 중복 행은 staging manifest에 남기고 논리 SourceRecord로
dedup한다. list와 detail은 같은 EID를 공유해도 파일 역할이 다른 별도 SourceRecord다.
EID는 최종 canonical ID의 강한 후보지만, ITKC와 시소러스의 같은 대상을 병합하려면
별도 crosswalk가 필요하다.

### 3.2 활용 가능 범위

| 원천 필드 | 활용 |
|---|---|
| `headword`, `origin`, `articleAliases` | 대표명·한자·별칭 후보 |
| `primaryType`, `field` | TopicType·분류 매핑 후보 |
| `era` | 시간·시대 후보 |
| `articleAttributes` | 출생·사망·저서·소재지 등 FactCandidate |
| `body`, `definition`, `reference` | RAG 문서와 근거 span |

`articleAttributes`의 값도 문자열 endpoint를 canonical entity로 해소하고 본문 근거를
검증한 뒤에야 Fact가 된다.

`headword`, `origin`, `articleAliases`는 배열 속성으로 `CanonicalEntity`에 복사하지
않는다. 각 표기를 provenance가 있는 `EntityName` 후보로 정규화하고, accepted 해소가
끝난 뒤 같은 실체를 가리키는 이름들을 하나의 canonical 대상에 연결한다.

### 3.3 사용 금지

`relatedArticles`는 관련 문서 링크일 뿐 관계 의미를 제공하지 않는다.

```text
관련 문서 A -> 관련 문서 B
```

이 정보만으로 `FOUNDED`, `CREATED`, `LED`, `CAUSED`를 만들지 않는다. 링크는 추출
후보를 찾는 보조 신호로만 사용한다.

## 4. 한국고전종합DB 관계망

### 4.1 규모

| 파일 | raw 행 | 고유 기준 |
|---|---:|---:|
| `itkc_people.csv` | 65,389 | `person_id` 65,303 |
| `itkc_events.csv` | 1,542 | `event_id` 600 |
| `itkc_person_relations.csv` | 206,764 | typed key 206,507 |
| `itkc_event_relations.csv` | 15,392 | event-person key 6,918 |

중복 행을 버리기 전에 URL, 이름 표기, 원래 범위를 provenance로 합친다.

### 4.2 인물 마스터

`itkc_people.csv`에는 인물 ID, 이름, 생몰년, 본관, 자, 호, 부친, 상세 URL 등이 있다.
관계 endpoint에 등장하지 않는 인물도 있으므로 이 파일을 독립 입력으로 읽어야 한다.

현재 `etl/preprocessing/neo4j/scripts/normalize_raw_data.py`는 시소러스·사건·관계 파일은
읽지만 `itkc_people.csv`를 직접 입력으로 사용하지 않는다. 현재 Person 생성도 관계
endpoint에 의존한다. 목표 ETL의 명시적인 보완 항목이다.

### 4.3 인물 관계

원천 관계 유형은 다음 16종이다.

```text
부, 생부, 모, 생모, 자, 조부, 증조부, 형제,
아내, 남편, 장인, 사위, 교유, 스승, 제자, 출자
```

방향·역관계·대칭 규칙을 사전으로 검증하면 typed FactCandidate로 만들 수 있다. 다만
동명이인, self-loop, 중복 방향, evidence URL 누락을 검토해야 한다.

### 4.4 사건과 사건-인물 관계

사건 원천은 사건명, 주제 분류, 고려·조선 구분, 날짜, 관련 사건 정보를 제공한다.

사건-인물 관계 15,392행의 관계명은 모두 `사건인물`이다. 참여자, 지휘자, 명령자,
피해자 중 어느 역할인지 구분할 수 없고 evidence URL도 비어 있다.

초기 상태는 다음처럼 낮은 의미 수준으로 둔다.

```text
predicate = ASSOCIATED_WITH_EVENT
status = needs_role_enrichment
answer_eligible = false
```

AKS 본문 또는 추가 권위 근거에서 실제 역할을 확인한 뒤에만 `PARTICIPATED_IN`,
`COMMANDED`, `LED` 등으로 승격한다.

사건-인물 관계를 찾는 것 자체를 금지하는 것은 아니다. 다만 역할이 확인되지 않은
`ASSOCIATED_WITH_EVENT`를 일반 오답 donor 자격이나 정답 Fact로 사용하지 않는다는 뜻이다.
사건군과 개별 사건, 조직과 구성 단위처럼 포함 구조가 확인되면 `EntityGroup`
membership 또는 직접 `PART_OF`·`INSTANCE_OF` 후보로 추출할 수 있다. 이 관계는
같은-parent 2홉 donor pool을 넓히지 않고, 중복 제외 또는 별도의 membership Facet에
사용한다.

### 4.5 raw response 파일

`itkc_raw_responses.csv`는 수집 URL 명세에 가깝고 로컬 저장 본문 경로가 채워져 있지
않다. 그 자체를 RAG 근거 본문으로 간주하지 않는다.

## 5. 한국역사용어시소러스

### 5.1 규모와 필드

| 항목 | 수치 |
|---|---:|
| 전체 행·고유 `term_id` | 62,409 |
| 실제 용어 `term_kind=2` | 61,598 |
| 분류 후보 `term_kind=1` | 794 |
| 최상위 분류 `term_kind=0` | 17 |
| 복수 `term_lk` 경로 | 98 |
| 동명 용어 그룹 | 3,944그룹, 9,658행 |

```text
term_id, topterm_id, term_name, term_kind, term_ch,
term_remark, term_attr, term_year, term_times,
term_lk, term_desc, term_user, term_created, term_reference
```

### 5.2 분류 경로

`term_lk`는 다음 순서로 파싱한다.

```text
1. >> 로 복수 경로 분리
2. 각 경로를 > 로 계층 분리
```

`topterm_id`는 직접 부모가 아니라 최상위 분류를 가리키는 성격이 강하므로 직접
`SUBCLASS_OF`로 쓰지 않는다.

### 5.3 활용 가능 범위

| 원천 값 | 활용 |
|---|---|
| `term_id` | Thesaurus SourceRecord key |
| `term_name`, `term_ch`, `term_remark` | 이름·한자·동명이인 구분 후보 |
| `term_lk` | 원천 분류 계층과 SemanticClass 매핑 근거 |
| `term_year`, `term_times` | 시간·시대 후보 |
| `term_desc` | RAG 보조 문서와 검색 힌트 |

시소러스 분류는 donor parent의 원재료다. 다음을 자동으로 하면 안 된다.

- `term_lk` leaf를 그대로 donor parent로 승인
- `정치·행정·법제` 같은 broad category를 후보 공유 노드로 사용
- `term_name`만으로 다른 원천과 병합
- `term_desc`만으로 상세 Fact를 verified 처리

## 6. 이름 통합과 원천 간 entity resolution

동일 이름은 후보를 만드는 신호일 뿐 병합 키가 아니다.

```text
NFKC 이름
+ 한자
+ TopicType 호환
+ 시대·생몰년 교집합
+ 자·호·본관
+ 설명 핵심 속성
+ 이미 해소된 관계 이웃
```

자동 승인, 수동 승인, 후보, 충돌, 거절 상태를 분리한다. 후보와 충돌 상태는
QuestionTarget과 QuestionUse 생성에서 제외한다.

동일 실체의 서로 다른 이름과 원천은 다음처럼 한곳으로 모은다.

```text
SourceRecord -[:HAS_NAME]-> EntityName -[:REFERS_TO {match_status=accepted}]-> CanonicalEntity
SourceRecord -[:RESOLVES_TO {match_status=accepted}]-> CanonicalEntity
```

예를 들어 같은 정조를 가리키는 대표명·묘호·한자 표기·원천별 표기는 하나의
`canonical_id`에 연결한다. 반대로 같은 문자열이 서로 다른 시대·유형의 대상을
가리키면 `EntityName` 문자열이 같더라도 별도 canonical 대상으로 해소할 수 있다.
따라서 이름 노드와 canonical 대상은 일대일이라는 전제를 두지 않는다.

`EntityName` 후보는 최소한 다음 값을 가져야 한다.

```text
entity_name_id
display_name
normalized_name
normalization_version
name_kind = canonical | alias | birth_name | hanja | ja | ho | former_name | source_variant
script
review_status = verified | pending | rejected
provenance = SourceRecord-[:HAS_NAME]->EntityName
```

`normalization_version`은 Unicode·공백·구두점·문자권 처리 규칙을 식별하며
`normalized_name`과 함께 snapshot에 고정한다.

합성 target은 raw 한 행에서 자동 생성하지 않는다. 여러 canonical 대상을 묶어야 하는
출제 대상은 안정 synthetic `canonical_id`, 구성 canonical ID, construction rule/version,
provenance와 검수를 갖춘 뒤에만 `QuestionTarget` 역할을 받을 수 있다.

## 7. 원천별 최종 판정

| 원천 정보 | 중간 산출물 | production 조건 |
|---|---|---|
| AKS/ITKC/시소러스 ID | SourceRecord | snapshot·hash 기록 |
| 이름·한자·별칭 | entity resolution feature | canonical 해소 승인 |
| 사건군·포함 구조 후보 | EntityGroup 또는 직접 canonical 관계 후보 | 의미·방향·근거 검증 |
| AKS `primaryType`, 시소러스 `term_lk` | 분류 후보 | mapping review 승인 |
| AKS 구조화 속성 | FactCandidate | endpoint·Predicate·근거 검증 |
| ITKC 인물 관계 | FactCandidate | 방향·중복·근거 검증 |
| ITKC 사건-인물 | 낮은 의미 관계 | 역할 보강 전 출제 금지 |
| 본문·정의 | RAG 문서 | stable document/chunk/span ID |
| verified Fact + Facet rule | QuestionUse 후보 | endpoint·shape·parent 검증 |

## 8. EDA 품질 gate

1. 모든 source record key가 원천 안에서 유일하다.
2. raw 파일 hash와 schema version이 기록된다.
3. parser error와 중복 처리 내역이 재현된다.
4. unresolved·conflict entity가 production target에 없다.
5. broad raw category가 donor parent로 자동 승격되지 않는다.
6. ITKC 사건-인물의 미확정 역할이 answer eligible Fact가 되지 않는다.
7. `relatedArticles`가 typed historical Fact로 직접 변환되지 않는다.
8. verified Fact마다 실제 RAG EvidenceSpan이 존재한다.
9. `itkc_people.csv`의 수집 coverage가 별도로 보고된다.
10. raw에 없는 Facet·QuestionUse·난이도 값을 원천 필드라고 설명하지 않는다.
11. 동일 canonical 대상의 승인 별칭이 별도 QuestionTarget으로 생성되지 않는다.
12. 동명이인이 이름 문자열만으로 자동 병합되지 않는다.
13. 사건군·집단 membership이 일반 donor 자격 경로를 넓히지 않는다.
14. 합성 target은 구성 규칙과 provenance 없이 자동 승인되지 않는다.
15. source file hash와 변환 정책 버전으로 `graph_snapshot_id`를 재현할 수 있다.

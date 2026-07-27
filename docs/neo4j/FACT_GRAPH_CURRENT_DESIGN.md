# 한국사 사실 그래프 현재 설계 정리

## 1. 결론

현재 목표는 기출문제의 오답을 그래프에 저장하는 것이 아니다.

검증된 역사 사실과 분류 관계를 연결하여, RAG가 정답 대상에서 출발해
같거나 유사한 경로를 공유하는 다른 `CanonicalEntity`를 찾을 수 있는
사실 그래프를 만드는 것이 목표다.

```text
사실 그래프 구축
    ↓
RAG가 정답 대상과 공유 경로를 가진 후보 탐색
    ↓
문제 생성 단계에서 오답 후보 선택
```

그래프에는 실제 역사적 사실만 저장한다. 오답 문장이나 거짓 역사 관계를
사실 관계로 적재하지 않는다.

---

## 2. 사용 목적

정답 대상과 다음 정보를 공유하는 다른 대상을 찾는다.

- 같은 `EntityType`
- 같은 부모 `SemanticClass`
- 같은 세부 `SemanticClass`
- 같은 시대
- 같은 지역·국가
- 같은 인물 역할
- 같은 주제
- 같은 질문 관점에서 사용할 수 있는 검증된 사실

예시는 다음과 같다.

```text
정조 ──CLASSIFIED_AS──> 조선 국왕 <──CLASSIFIED_AS── 영조
정조 ──CLASSIFIED_AS──> 조선후기 국왕 <──CLASSIFIED_AS── 영조
정조 ──IN_ERA─────────> 조선 후기 <────────IN_ERA── 영조
```

RAG는 `정조`를 기준으로 같은 분류·시대 경로를 공유하는 `영조`를
후보로 찾을 수 있다.

---

## 3. 사실과 오답의 분리

### 3.1 사실 그래프에 저장하는 것

공식 자료로 확인된 관계만 저장한다.

```text
병인양요 ──INVADING_POWER──> 프랑스
신미양요 ──INVADING_POWER──> 미국
```

각 관계에는 출처와 근거가 있어야 한다.

### 3.2 사실 그래프에 저장하지 않는 것

오답을 만들기 위한 거짓 관계는 저장하지 않는다.

```text
병인양요 ──INVADING_POWER──> 미국  # 저장 금지
```

미국이 병인양요의 오답 후보로 선택됐다는 사실은 문제 생성 또는
문제 사용 이력에서 관리한다. 역사 사실 그래프에는 넣지 않는다.

### 3.3 별도의 오답 사실 그래프는 만들지 않는다

현재 단계에는 `Problem`, `Choice`, `DistractorAnalysis`가 필요하지 않다.

문제 생성 단계에서 어떤 후보를 사용했는지 보존할 필요가 생기면
다음과 같은 얇은 사용 기록만 별도로 둔다.

```text
correct_target_id
candidate_target_id
fact_id
question_facet_id
graph_path
hop_count
difficulty
```

이 기록은 역사 관계가 아니라 문제 생성 이력이다.

---

## 4. 그래프 구성

### 4.1 출처와 Entity Resolution

#### `ExamTerm`

기출문제에서 추출한 용어의 기본 목록이다. 원천 매칭이 보류돼도 삭제하지 않는다.

```text
ExamTerm ──REFERS_TO {status: ACCEPTED}──> CanonicalEntity
```

검증된 연결이 없으면 `ExamTerm`만 유지한다. 이 상태의 용어에는 아직 사실 관계를
붙이지 않는다.

#### `SourceRecord`

AKS, 한국역사용어시소러스, ITKC 등 원천의 개별 레코드다.

주요 속성:

```text
source_record_id
source
source_key
source_release
source_metadata
```

#### `EntityName`

표제어, 별칭, 한자명 등 이름을 관리한다.

```text
SourceRecord ──HAS_NAME──> EntityName
EntityName ──REFERS_TO──> CanonicalEntity
```

#### `CanonicalEntity`

동일한 역사 대상을 가리키는 승인된 원천 레코드를 하나로 묶은 노드다.

```text
SourceRecord ──RESOLVES_TO {status: ACCEPTED}──> CanonicalEntity
```

동명이인이나 같은 이름의 다른 의미를 분리해야 한다. 승인되지 않은
후보는 `CanonicalEntity`의 사실 관계에 연결하지 않는다.

전체 후보 판정이 보류돼도 검증된 identity pair가 있으면 그 연결 성분만
`CanonicalEntity`로 승격한다. 나머지 후보는 보류 상태로 남긴다.

### 4.2 분류 노드

#### `EntityType`

대상의 기본 유형이다.

```text
Person
Event
Institution
Heritage
Work
Organization
Place
Polity
Concept
```

#### `SemanticClass`

RAG 후보 자격과 근접도를 판단하는 의미 분류다.

```text
조선 국왕
조선후기 국왕
고려 대외항쟁 인물
조선 수취제도
고려시대 역사서
조선 궁궐
```

부모 분류와 세부 분류를 구분한다.

```text
조선후기 국왕 ──SUBCLASS_OF──> 조선 국왕

정조 ──CLASSIFIED_AS {level: parent}──> 조선 국왕
정조 ──CLASSIFIED_AS {level: subgroup}──> 조선후기 국왕
```

#### 보조 Anchor

후보 근접도와 경로를 구성하는 노드다.

```text
Era
Region
Polity
PersonRole
Topic
```

`Era`, `정치`, `문화`처럼 너무 넓은 노드 하나만 공유하는 것은
후보 자격의 충분한 근거로 사용하지 않는다.

### 4.3 `Fact`

검증된 원자 역사 사실을 표현한다.

```text
Fact
  fact_id
  predicate
  verification_status
  valid_from_year
  valid_to_year
  source_policy_version
```

기본 연결은 다음과 같다.

```text
Fact ──SUBJECT──> CanonicalEntity
Fact ──OBJECT──> CanonicalEntity 또는 값 노드
Fact ──SUPPORTED_BY──> EvidenceSpan
```

하나의 `Fact`에는 하나의 원자 주장만 저장한다. 여러 사건과 관계를
한 문장 그대로 하나의 Fact에 넣지 않는다.

현재 MVP 구현은 별도 `Fact` 노드가 아니라 두
`CanonicalEntity` 사이의 `FACT_RELATION` 관계에
`canonical_relationship_id`, 관계 유형, 검증 상태와 근거 배열을
저장한다. 별도 `Fact` 노드 전환은 시간 범위·복합 provenance를 그래프에서
직접 조회해야 할 때 결정한다.

#### Fact 예시

```text
Fact F1
  SUBJECT   → 정조
  predicate → CREATED_OR_ESTABLISHED
  OBJECT    → 규장각
  status    → VERIFIED
```

### 4.4 `EvidenceSpan`

Fact를 뒷받침하는 공식 문서의 근거 범위다.

```text
evidence_id
source_record_id
source_url
source_text
start_offset
end_offset
```

근거가 없는 Fact는 최종 사실 그래프에 적재하지 않는다.

현재 MVP는 `EvidenceSpan` 노드를 아직 생성하지 않고 관계의
`evidence_sentences_json`, `evidence_urls_json`, `detail_urls_json`에
근거를 보존한다. 따라서 근거 offset 단위 조회는 아직 지원하지 않는다.

---

## 5. 후보 탐색 원칙

후보 탐색 자체는 RAG 또는 조회 계층의 역할이다. ETL은 탐색 가능한
사실과 분류 경로를 정확히 제공한다.

### 5.1 기본 후보 경로

가장 단순한 후보 경로는 공유 부모 분류를 이용한다.

```text
Target ──CLASSIFIED_AS──> ParentClass
Candidate ──CLASSIFIED_AS──> ParentClass
```

이를 방향을 무시하고 보면 target과 candidate 사이의 2홉 경로다.

```text
정조 → 조선 국왕 ← 영조
```

### 5.2 hop 수는 고정하지 않는다

후보 탐색 경로는 반드시 2홉으로 고정하지 않는다.

다만 무제한 탐색은 관련 없는 후보와 순환 경로를 늘리므로 다음 항목은
조회 정책으로 관리한다.

```text
minimum_hops
maximum_hops
allowed_relationship_types
excluded_anchor_types
relationship_weights
```

RAG가 반환할 때 실제 탐색 경로와 hop 수를 함께 반환해야 한다.

```text
target_entity_id
candidate_entity_id
graph_path
hop_count
shared_parent_classes
shared_subgroups
shared_eras
shared_polities
shared_roles
```

---

## 6. 후보 난이도

`hop_count`는 원본 그래프 거리로 저장하고, 최종 난이도는 여러 근접도
신호를 함께 사용해 문제 생성 단계에서 계산한다.

일반적으로 시험에서 헷갈리는 후보는 정답과 역사적으로 가까운 후보다.

### 쉬움

- 부모 분류만 같음
- 시대·국가·역할이 다름
- 세부 분류 공유가 적음

### 보통

- 부모 분류가 같음
- 시대·주제 중 일부가 같음
- 세부 분류가 일부 겹침

### 어려움

- 같은 세부 분류
- 같은 시대
- 같은 국가·지역
- 같은 역할 또는 주제
- 서로 다른 독립 canonical ID
- 교체해 만든 문장이 실제 사실은 아님

후보가 너무 가까워 교체된 문장도 사실이 되는 경우에는 오답 후보로
자동 사용하지 않는다.

---

## 7. QuestionFacet과 QuestionUse

`QuestionFacet`과 `QuestionUse`는 사실 그래프를 문제 생성에 안전하게
사용하기 위한 연결 계약이다. 오답 그래프가 아니다.

### `QuestionFacet`

같은 유형의 대상이라도 질문하는 관점을 구분한다.

```text
person.activity_achievement
person.active_period
person.writing_thought
event_movement.background_cause
event_movement.result_effect
policy_system.main_content
```

### `QuestionUse`

어떤 대상이 어떤 facet에서 어떤 Fact를 사용할 수 있는지 연결한다.

```text
QuestionUse
  TARGET       → 정조
  USES_FACET   → person.activity_achievement
  USES_FACT    → F1
  ANSWER_SHAPE → FACT_STATEMENT
```

같은 `QuestionFacet`, `answer_shape`, `answer_role`을 만족하는 후보만
비교하면 관계는 비슷하지만 마지막 대상이 다른 후보를 찾을 수 있다.

`QuestionUse`는 검증된 Fact가 있을 때만 만든다.

---

## 8. 현재 보유 데이터

### 8.1 기출 추출 용어

현재 추출 결과는 집계 단계에 따라 다음처럼 구분한다.

```text
unique_exam_terms.csv 행: 5,521개
표기 기준 고유 canonical_term: 5,268개
정규화·노이즈 제외 후 커버리지 모집단: 5,211개
이름 기준 원천 커버: 4,650개
미커버: 561개
커버율: 89.23%
```

이 커버율은 이름 기반 검색 결과다. 원천에 실제 자료가 없다는 뜻은
아니며, 부분 일치 오류와 동명이인 문제를 포함한다.

### 8.2 Entity Resolution 상태

2026-07-26 최종 identity 출력 기준:

```text
CanonicalEntity: 4,786개
ACCEPTED 기출 용어: 2,626개
기출 용어가 연결된 CanonicalEntity: 2,638개
사실 검색 Anchor가 있는 기출 CanonicalEntity: 745개
사실 검색 Anchor가 없는 기출 CanonicalEntity: 1,893개
```

현재 가장 큰 차단점은 canonical 생성 자체가 아니라, 기출 canonical
대상에 연결된 검증 사실과 세부 의미 분류의 부족이다.

### 8.3 AKS

현재 후보 기준:

```text
AKS 후보가 있는 case: 5,398건
사용 가능 정보: EID, 표제어, 별칭, 정의, 기본 유형, 시대, 원문 URL
상세 본문 원천: articles_detail.jsonl
```

AKS 정의와 본문은 분류 Anchor와 Fact를 만드는 주요 원천이다.

### 8.4 한국역사용어시소러스

현재 후보 기준:

```text
시소러스 후보가 있는 case: 5,299건
분류 경로가 있는 후보: 5,299 cases
원천 전체 용어: 62,409개
```

`term_lk`는 의미 분류 경로로 사용할 수 있다. `topterm_id`는 직속
부모가 아니라 최상위 분류이므로 직접적인 세부 계층으로 오해하면 안 된다.

### 8.5 ITKC

현재 원천:

```text
고유 인물: 65,303개
고유 사건: 600개
중복 제거 인물 관계: 206,507개
중복 제거 사건-인물 관계: 6,918개
```

현재 추출 case 중 ITKC 관계에 연결될 가능성이 있는 상한:

```text
인물 관계 후보 case: 531건
사건 관계 후보 case: 443건
```

이는 아직 canonical 승인 전 후보 기준이므로 최종 적재 수가 아니다.

### 8.6 현재 사실 검색 출력

`fact-retrieval-v1.1` 기준:

```text
Canonical 사실 관계: 1,234개
EntityAnchor: 7,472개
PRIMARY Anchor 사실: 1,234개
공식 ID 1-hop FALLBACK 사실: 10,984개
RAG 교체 후보: 1,464개
이미 참이라 차단: 228개
외부 검증 필요: 1,236개
중복 제거된 외부 검증 backlog: 938개
1회 외부 검증 batch: 300개
```

후보에는 실제 graph path, 관계 유형 배열, PRIMARY/FALLBACK 상태와
fallback edge 수를 함께 저장한다. 경로가 없거나 동일 관계 역할이 없는
대상은 후보에서 제외한다.

### 8.7 현재 원천 관계 전처리 결과

ITKC와 시소러스를 SourceRecord 단계에서 정리한 출력:

```text
SourceRecord: 128,312개
원천 관계: 275,817개
시소러스 분류 노드: 515개
시소러스 분류 소속 관계: 61,598개
시소러스 분류 계층 관계: 498개
```

이 결과는 provenance 및 관계 staging 데이터다. 승인된
`CanonicalEntity`의 최종 Fact 그래프와 동일하지 않다.

### 8.8 기출 용어 기반 NLP 관계 후보와 게이트 상태

2026-07-27 전체 공식 출처를 대상으로 실행한 결과:

```text
기출 용어 모집단: 5,211개
관계 후보가 있는 용어: 4,295개
용어 커버리지: 82.42%

출처별 관계 후보 단순 합계: 616,265개
출처 간 중복 병합 후 관계 후보: 612,074개
관계 근거: 641,110개
양쪽 endpoint가 등록된 관계 후보: 63,945개
양쪽 endpoint가 모두 미등록인 관계: 0개
```

관계 후보 61만여 개는 사실로 승인된 관계 수가 아니다. 명사구 경쟁 후보,
동명이인, 복합 문장의 다른 논항, 넓은 관계 의미가 함께 포함된 staging
결과다.

초기 게이트는 기존 `HIGH_CONFIDENCE` 판정을 다시 필수 조건으로 사용해
후보를 369개까지 줄이는 이중 필터 문제가 있었다. 현재 v1.1 게이트는
61만 후보와 64만 근거를 전부 다시 평가한다.

2026-07-27 재실행 결과:

| 등급 | 관계 수 | 의미 |
|---|---:|---|
| 복수 독립 근거 | 116 | 엄격 문법을 통과하고 근거가 둘 이상 |
| 단일 명시 근거 | 3,021 | 엄격 문법은 통과했으나 근거가 하나 |
| 엄격 후보 합계 | 3,137 | 위 두 등급의 합계 |
| 타입 검토 필요 | 7,062 | 문법은 통과했으나 endpoint 타입이 불명·불일치 |
| 전체 명시 관계 후보 | 10,199 | 엄격 후보와 타입 검토 후보의 합계 |

전체 명시 관계 후보가 직접 연결하는 기출 용어는 1,795개다. 양쪽
endpoint가 모두 미등록인 관계와 자기 자신을 잇는 관계는 0개다.

엄격 문법 조건은 다음을 계속 차단한다.

- 문장의 실제 목적어가 아닌 앞쪽 명사를 관계 대상으로 선택
- 출발지를 천도의 도착지로 선택
- 피동문의 대상을 행위자로 뒤집음
- 전기 문서의 주인공 대신 문장에 등장한 왕을 행위자로 선택
- `함종부사` 같은 문자열을 다른 등록 엔티티와 잘못 정렬
- 부정·추정·전승 문장을 확정 사실로 해석

`safe_relation_candidates.csv`에는 엄격 후보 3,137개만 들어간다.
`type_review_relation_candidates.csv`에는 추가 7,062개를 분리하고,
`all_explicit_relation_candidates.csv`에는 두 등급을 합친 10,199개를
저장한다. 모든 파일의 `auto_load_eligible`은 `false`이며 LLM 호출과
Neo4j 적재는 수행하지 않았다.

---

## 9. 현재 데이터로 가능한 범위

### 가능한 것

- 추출 용어의 AKS·시소러스·ITKC 후보 검색
- AKS 정의에서 유형과 시대 Anchor 제안
- 시소러스 분류 경로에서 `SemanticClass` 후보 생성
- ITKC 인물 관계와 사건-인물 관계를 Fact 후보로 변환
- AKS 상세 본문에서 기관·제도·문화재·문헌·지역 Fact 후보 추출
- 모든 Fact에 원천 레코드와 근거 URL 연결
- 같은 분류·시대·역할 경로를 공유하는 후보 조회

### 아직 바로 할 수 없는 것

- 승인되지 않은 SourceRecord를 canonical 노드로 간주
- ITKC SourceRecord 관계를 검증 없이 canonical 사실로 승격
- 미검증 LLM 제안을 `VERIFIED` Fact로 적재
- 범용 분류 하나만 공유한 대상을 가까운 후보로 간주
- 후보가 실제로도 정답인지를 검사하지 않고 오답으로 사용

### 가능성 판단

현재 데이터로 첫 번째 사실 그래프 MVP 산출물은 생성됐다.

다만 ITKC가 직접 제공하는 관계는 인물과 사건 중심이다. 제도, 기관,
문화재, 문헌, 지역 등은 AKS 정의와 상세 본문에서 Fact를 추출하고
근거를 확인하는 과정이 추가로 필요하다.

---

## 10. 구현 순서

### 1단계: canonical 대상 확정

- 추출 용어와 원천 후보 연결
- 동명이인 분리
- 정확한 AKS EID 또는 승인된 합성 target 선택
- 승인된 SourceRecord만 CanonicalEntity에 연결

### 2단계: 분류 그래프 생성

- `EntityType` 생성
- 시소러스 경로를 `SemanticClass`로 정규화
- 부모·세부 분류 생성
- AKS 시대를 `Era`로 정규화
- 필요한 `Region`, `Polity`, `PersonRole`, `Topic` Anchor 생성

### 3단계: Fact 생성

- ITKC 인물 관계 변환
- ITKC 사건-인물 관계 변환
- AKS 정의·본문에서 원자 Fact 후보 생성
- 복합 문장은 여러 원자 Fact로 분리

### 4단계: Fact 근거 연결

- Fact별 `EvidenceSpan` 생성
- SourceRecord와 원문 URL 보존
- 근거가 없는 Fact 제외

### 5단계: 최종 사실 그래프 적재

- 승인된 CanonicalEntity만 사용
- 검증된 Fact만 적재
- 분류와 Fact의 endpoint 무결성 검사
- 중복 Fact 제거

### 6단계: RAG 조회 계약

- target과 공유 경로를 가진 candidate 탐색
- 경로와 hop 수 반환
- 공유 분류·시대·역할 신호 반환
- 같은 facet에서 사용할 수 있는 candidate Fact 반환

---

## 11. 현재 우선순위

현재 최우선 과제는 다음 세 가지다.

1. 사실 관계 골드셋 100개를 검수해 패턴 사실의 정확도를 측정한다.
2. Anchor가 없는 기출 canonical 1,893개에 공식 근거 사실을 보강한다.
3. 외부 검증 backlog 938개의 결과를 배치별로 사실성 게이트에 반영한다.

오답 유형 분석, 후보 선택, 문장 생성은 이 사실 그래프를 조회하는
RAG 및 문제 생성 단계에서 수행한다.

현재 Neo4j ETL은 오답 관계를 만드는 방향이 아니라, RAG가 안전하게
탐색할 수 있는 사실·분류·근거 그래프를 만드는 방향으로 정리한다.

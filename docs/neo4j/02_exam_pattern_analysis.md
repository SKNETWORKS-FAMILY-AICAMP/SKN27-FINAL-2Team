# 02. 78회 문제 분석 원칙과 출제 정책

> 상태: `POLICY-INPUT`
> 기준일: 2026-07-16
> 주의: 문제지·해설지는 역사 Fact 원천이 아니다.

이 문서는 78회 50문항의 집계 결과를 주장하는 완성 EDA 보고서가 아니다. 현재 단계에서는
PDF를 어떤 기준으로 분석하고 그 결과를 어떤 계약으로 옮길지 정의한다. 실제 카탈로그를
active로 전환하기 전에는 문항별 50행 분석표와 집계표를 별도 evidence artifact로 만들고
검수해야 한다. 확인하지 않은 개수나 분포를 이 문서에서 만들어 내지 않는다.

## 1. PDF를 분석하는 목적

78회 문제지와 해설지는 다음을 알아내는 데만 사용한다.

- 어떤 발문의도가 반복되는가
- 한 문항이 어떤 단서와 답 형태를 요구하는가
- 정답과 오답이 어느 역할에서 교체되는가
- 어떤 유형에서 시간·이미지·사료·복합 판단이 필요한가
- 후보가 얼마나 비슷할수록 체감 난이도가 높아지는가

PDF의 문제 문장, 정답 번호, 해설 문장은 `Fact`나 `EvidenceSpan`의 승인 근거로
Neo4j에 적재하지 않는다. 역사 사실과 근거는 raw 3종과 승인된 RAG 문서에서 만든다.

## 2. 서로 다른 축

다음 값은 이름이 비슷해도 역할이 다르다.

| 축 | 질문 | 저장 위치 |
|---|---|---|
| `QuestionFacet` | 대상의 무엇을 묻는가 | Neo4j |
| `StemIntent` | 수험생에게 어떤 판단을 요구하는가 | 문제 생성 정책 레지스트리 |
| `QuestionType` | 지문·발문·선지를 어떤 형식으로 구성하는가 | 문제 생성 정책 레지스트리 |
| `answer_shape` | 정답 재료의 구조가 무엇인가 | `QuestionUse` |
| `DifficultyBand` | 후보와 단서가 얼마나 가까운가 | 문제 생성 정책과 런타임 계산 |
| `CurriculumTopic/Era` | 취약점 분석에서 어디에 집계하는가 | 교육과정 분류 레지스트리·운영 DB |

예를 들어 `person.activity_achievement`는 Facet이고, “옳은 것을 고르시오”는
StemIntent이며, 사료 제시형은 QuestionType이다. `문화`와 `조선`은 취약점 집계축이다.

## 3. Facet은 Predicate와 같지 않다

Predicate는 역사 Fact의 원자 관계다.

```text
정조 - FOUNDED - 규장각
정조 - IMPLEMENTED - 장용영
정조 - BUILT - 수원 화성
```

위 Predicate들은 모두 `person.activity_achievement` Facet으로 출제될 수 있다. 반대로
같은 Fact도 target 역할을 바꾸면 다른 질문축으로 사용할 수 있다.

```text
Fact: 정조 - FOUNDED - 규장각

QuestionUse A
  TARGET = 정조
  target_role = subject
  facet = person.activity_achievement
  answer_shape = FACT_STATEMENT
  answer_role = whole_fact

QuestionUse B
  TARGET = 규장각
  target_role = object
  facet = organization.founder
  answer_shape = ENTITY
  answer_role = subject
```

따라서 `Fact`와 `QuestionFacet`을 전역 규칙으로만 연결하면 부족하다. 어떤 target
역할로 사용할 수 있는지까지 묶는 `QuestionUse`가 필요하다.

### 3.1 역할과 응답 형태를 왜 함께 기록하는가

`QuestionUse`의 세 값은 같은 뜻이 아니다.

| 값 | 답하는 질문 | 허용 예 |
|---|---|---|
| `target_role` | question target이 Fact의 어느 endpoint인가 | `subject`, `object` |
| `answer_role` | Fact에서 실제 답으로 내보낼 부분은 무엇인가 | `subject`, `object`, `whole_fact`, `time` |
| `answer_shape` | 그 답을 어떤 구조로 전달하는가 | `ENTITY`, `FACT_STATEMENT`, `TIME_POINT`, `TIME_RANGE` |

허용 조합은 양방향으로 검증한다.

| `answer_shape` | 허용 `answer_role` | 추가 조건 |
|---|---|---|
| `ENTITY` | `subject` 또는 `object` | 해당 endpoint가 canonical entity이고 Facet의 answer domain과 일치 |
| `FACT_STATEMENT` | `whole_fact` | 검증된 원자 Fact 전체가 답 binding |
| `TIME_POINT` | `time` | 검증된 단일 시점 qualifier 존재 |
| `TIME_RANGE` | `time` | 검증된 시작·종료 또는 기간 qualifier 존재 |

예를 들어 `organization.founder`는 target이 organization/object이고 답이
person/subject/`ENTITY`여야 한다. 단순히 `answer_shape=ENTITY`만 같아도 되는 것이 아니라
`answer_domain_id=<person TopicType revision>`처럼 실제 허용 도메인 revision이 일치해야 한다.
Facet revision은 최소한 target TopicType, 허용 Predicate signature, 세 role/shape 값,
answer domain, 허용 mismatch rule, surface template를 함께 정의한다.

### 3.2 집단·사건 관계를 묻는 문항

집단이나 사건군 연결을 찾으면 안 되는 것이 아니다. 용도가 다르다.

- 일반 오답 donor: 같은 specific parent `SemanticClass`를 직접 공유하는 정확한 2홉에서
  먼저 찾고, 동일 사건군·직접 포함 관계는 중복 방지를 위해 제외하거나 순위를 낮춘다.
- membership 문항: “동학 농민 운동에 포함된 사건은?”처럼 구성 관계 자체가 답이면
  `event.group_membership` 같은 별도 Facet과 `MEMBER_OF_GROUP`/`PART_OF` Predicate
  signature를 사용한다.

집단 관계를 따라 일반 donor pool을 무제한 확장하지 않는다.

## 4. 문항별 EDA 산출물과 정책으로 옮길 값

활성 카탈로그를 승인하기 전에 50문항 각각에 다음 필드를 기록한다.

```text
exam_id, question_no
stem_intent_candidate, question_type_candidate
question_target_kind, target_role_candidate
question_facet_candidate
answer_role_candidate, answer_shape_candidate, answer_domain_candidate
required_clue_roles, required_media
option_transformation_pattern
positive_or_negative_polarity
difficulty_observations
review_status, reviewer_note
```

그 뒤 문항별 표에서 집계한 값만 아래 정책 레지스트리 후보로 옮긴다.

시험 문항 EDA 결과는 다음 정책 레지스트리의 입력이 된다.

| 정책 | 예 |
|---|---|
| Facet 후보 | 활동·업적, 주요 내용, 원인, 결과, 관련 인물, 시기, 장소 |
| StemIntent | 대상 식별, 옳은 사실 선택, 옳지 않은 사실 선택, 순서 판단 |
| QuestionType | 일반 지문형, 사료형, 시각 자료형, 연표형, 보기 조합형 |
| answer shape | `ENTITY`, `FACT_STATEMENT`, `TIME_POINT`, `TIME_RANGE` |
| compatibility | 특정 type이 요구하는 이미지·시간·후보 수·Fact 역할 |
| difficulty feature | 하위 분류 공유, 시대·정권 근접, 단서 직접성, 후보 표현 유사도 |

카탈로그 개수는 PDF 한 회분만 보고 고정하지 않는다. raw coverage와 검증 Fact 수를
같이 측정해 실제 생성 가능한 Facet만 `active`로 승격한다.

v1 런타임은 positive single-answer인 `select_correct_statement`만 active로 둔다.
`select_incorrect_statement`, 순서형, 보기 조합형은 분석 카탈로그에는 남기되 truth 분포와
TRUE companion 계약이 별도로 완성될 때까지 `draft`로 두고 무작위 선택에서 제외한다.

## 5. 출제 유형의 선택 시점

유형과 난이도는 “먼저 완전 랜덤으로 뽑고 나중에 재료를 억지로 찾는” 값이 아니다.

```text
키워드·발문의도
  -> canonical target과 active QuestionUse 후보
  -> 정답 Fact와 근거 존재 확인
  -> 동일 parent·동일 Facet 후보 수 확인
  -> 사용 가능한 유형·난이도 조합 산출
  -> 그 조합 안에서 무작위 선택
```

이미지가 없는 대상은 이미지형 후보에서 제외하고, 시간 범위가 모호하면 순서형 후보에서
제외한다. 어려움 후보가 4개 미만이면 난이도를 몰래 완화하지 않고 다른 조합을 다시
선택하거나 해당 생성 요청을 건너뛴다.

## 6. Neo4j에 들어가는 것과 들어가지 않는 것

PDF EDA의 결과 중 Neo4j에 들어가는 것은 다음 두 가지뿐이다.

- 승인된 `QuestionFacet` 카탈로그
- 검증 Fact를 특정 target·Facet에 투영한 `QuestionUse`

다음은 Neo4j에 넣지 않는다.

- 78회 문항 자체
- 문제별 정답 번호
- 지문 문장 템플릿 전문
- 문제 유형의 무작위 가중치
- 난이도 점수식
- 생성 프롬프트
- 취약점 통계

이 값들은 문제 생성 정책 또는 운영 DB가 관리한다.

## 7. 취약점 분석축

취약점 보고 상위 분류는 시험 표현 유형이나 TopicType과 별개다.

| 축 | 상위 값 |
|---|---|
| topic | 사건, 인물, 정치, 제도, 문화, 사회, 군사, 경제, 사상·종교, 외교 |
| era | 조선, 고려, 삼국시대, 개항기, 현대, 일제강점기, 남북국시대, 초기국가, 선사시대, 고조선 |

세부 분류가 필요하면 상위 ID를 유지한 채 하위 ID를 추가한다. 예를 들어
`수취 제도 -> 경제`, `조선 후기 -> 조선`으로 roll-up한다. 다만 상위/하위 관계와
primary/secondary는 다른 축이다. 세부 topic도 한 QuestionUse의 primary가 될 수 있고,
상위 topic이 secondary가 되는 식으로 임의 대체해서는 안 된다.

정책 레지스트리의 `QuestionClassificationBinding`이 각
`question_use_revision_id`에 taxonomy version, primary topic·era 하나, 선택적 secondary
topic·era, 선택적 detail topic·era를 연결한다. 생성 요청의 curriculum filter와 생성
문항의 취약점 분류는 같은 binding revision을 사용한다.

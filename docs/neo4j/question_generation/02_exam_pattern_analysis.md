# 02. 심화 78회 문항에서 도출한 문제 생성 패턴

> 목적: 문제 생성에 필요한 발문의도·의미 경로·오답 슬롯·난이도 조절 규칙을 도출한다.

## 1. 유형을 하나의 문자열로 만들면 안 되는 이유

78회 50문항을 분석하면 같은 역사 경로가 사료, 대화, 전시 화면, 지도, 이미지, 표 등 여러 표현으로 출제된다. 반대로 같은 이미지형이라도 인물, 시대, 문화유산, 장소 등 서로 다른 관계를 묻는다.

따라서 문항 정의를 다음 세 축으로 분리한다.

```text
QuestionType  = 어떤 그래프 구조를 묻는가
StemIntent    = 그 구조에서 무엇을 선택하게 하는가
Modifier      = 부정형·시간형·이미지형 등 표현과 연산을 어떻게 바꾸는가
```

예를 들어 25·43·47번의 “적절하지 않은 것”은 새로운 역사 경로가 아니다. 기존 시대·정부 특징형에 다음 값을 결합한 것이다.

```text
StemIntent = SELECT_EXCEPTION
polarity   = NEGATIVE
```

## 2. QuestionType·CompositionMode와 같은 경로의 의미

`QuestionType`은 최종 답을 판정하는 주 의미 구조이고, `CompositionMode`는 하나 이상의 경로를 한 문항에 조립하는 방식이다. 두 축을 분리해야 복합 문항이 단일 `question_type_id`와 충돌하지 않는다.

| 분류 축 | 값 | 78회 대표 문항 | 의미 경로 | 답 슬롯 | 주 오답 교체 슬롯 |
|---|---|---|---|---|---|
| QuestionType | `ANCHOR_ATTRIBUTE` | 1, 3, 5, 6, 12, 15, 25, 27, 36, 40, 43, 47 | `단서 → 시대·국가·정부 → 제도·생활·경제·사회상` | attribute | attribute 또는 anchor |
| QuestionType | `ACTOR_ACTIVITY` | 4, 11, 17, 18, 21, 24, 35, 41, 49 | `단서 → 인물·정부·기관 등 행위자 → 정책·작품·사상·활동` | activity | activity, work, policy, actor |
| QuestionType | `EVENT_CONTEXT` | 13, 22, 29, 31, 33, 38, 44, 46 | `단서 → 사건 → 대응·배경·동시 사실·원인·결과·후속 사실·탐구 대상` | context fact | response, background, concurrent fact, cause, result, related event |
| QuestionType | `TEMPORAL_RELATION` | 7, 8, 10, 16, 19, 23, 32, 37, 45 | `기준 사건·재위 → TimeSpan → 이전·이후·기간 내 사건` | candidate event | event |
| QuestionType | `CHRONOLOGY_ORDER` | 2 | `여러 사건 → 각 TimeSpan → 확정 가능한 정렬` | sequence | sequence operand |
| QuestionType | `ASSOCIATED_ASSET` | 9, 14, 26 | `국가·시대·작가 → 유물·유적·작품` | asset | asset 또는 owner/creator |
| QuestionType | `INSTITUTION_RULE` | 20, 28, 30, 34, 39, 42, 48 | `기관·조직·조약·신문 → 기능·조항·활동` | function/clause | function, clause, institution |
| QuestionType | `LOCATION_EVENT` | 50 | `장소 단서 → 장소 ← 사건 발생지` | place 또는 event | place 또는 event |
| CompositionMode | `MULTI_ANCHOR_COMPARE` | 3, 18 | `자료 A → 대상 A → 속성 A`와 `자료 B → 대상 B → 속성 B` | matching pair | anchor/attribute pair |
| CompositionMode | `MAPPING_MATCH` | 30 | 여러 `Entity → Attribute` 행을 각각 검증 | valid row | 행의 entity/value |

QuestionType 행의 1~50번은 각각 한 번만 배정한 primary type이다. CompositionMode 행의 중복 번호는 주 유형과 충돌하지 않는 복합 구성이다. 보조 경로가 필요한 대표 사례는 다음과 같다.

- 26번: primary `ASSOCIATED_ASSET`, primary path `PERSON_CREATED_WORK`; 작품·유배·서체 단서는 인물 식별 보조 경로
- 29번: primary `EVENT_CONTEXT`; `LOCATION_EVENT`는 장소 식별 보조 경로
- 41번: primary `ACTOR_ACTIVITY`, primary path `ORGANIZATION_PERFORMED_ACTIVITY`; 단체 전신·인물 가입 관계는 보조 경로
- 30번: primary `INSTITUTION_RULE`; `MAPPING_MATCH`로 여러 대응을 조립

런타임의 `QuestionBlueprint`는 주 답 경로 하나와 보조 단서 경로 0개 이상, QuestionType 하나, StemIntent 하나, CompositionMode 하나를 묶는다.

이 표의 “같은 경로”는 같은 구체 노드를 다시 사용한다는 뜻이 아니다. 같은 `PathPattern`을 만족하는 서로 다른 바인딩 묶음을 뜻한다.

```text
PathPattern: PERSON_CREATED_WORK

정답 바인딩
  anchor = 김정희
  answer = 세한도
  fact   = 김정희 CREATED 세한도

오답 후보 바인딩
  anchor = 정선
  answer = 인왕제색도
  fact   = 정선 CREATED 인왕제색도
```

김정희 문제의 오답 후보로 `인왕제색도`를 사용한다면 answer만 바꾸는 것이 아니다. 후보의 실제 creator, 근거 Fact, RAG 청크까지 하나의 묶음으로 가져와야 한다.

## 3. StemIntent 사전

| StemIntent | 발문 목적 | 필요한 검증 |
|---|---|---|
| `IDENTIFY` | 자료가 가리키는 인물·사건·국가를 식별 | 단서 집합이 답을 유일하게 식별 |
| `SELECT_ATTRIBUTE` | 대상의 제도·업적·생활상 선택 | anchor와 attribute의 승인 Fact |
| `SELECT_CAUSE` | 사건의 원인 선택 | 시간적 선행이 아닌 인과 근거 |
| `SELECT_RESULT` | 사건의 결과 선택 | 결과 방향과 범위가 명시된 근거 |
| `SELECT_BACKGROUND` | 사건의 성립 배경 선택 | 배경과 단순 선행 사건을 구분하는 근거 |
| `SELECT_CONCURRENT` | 사건 진행 중의 사실 선택 | 두 TimeSpan의 포함·교집합이 확정됨 |
| `SELECT_BEFORE` | 기준보다 앞선 사건 선택 | 시간 구간이 확실히 앞섬 |
| `SELECT_AFTER` | 기준보다 뒤의 사건 선택 | 시간 구간이 확실히 뒤임 |
| `SELECT_DURING` | 재위·정부·전쟁 기간 내 사실 선택 | 후보 구간이 기준 구간에 포함 |
| `ORDER_EVENTS` | 여러 사건의 순서 선택 | 모든 사건 쌍의 선후가 확정 가능 |
| `SELECT_ASSOCIATED` | 관련 인물·작품·유산 선택 | 관계 술어와 역할이 승인됨 |
| `SELECT_FUNCTION` | 기관·법·조약의 기능이나 조항 선택 | 대상·수혜자·시점 등 역할 검증 |
| `SELECT_EXCEPTION` | 해당하지 않는 사실 선택 | 네 개 TRUE와 한 개 FALSE 등 선택 규칙 검증 |
| `COMPARE` | 두 대상의 올바른 대응 선택 | 각 하위 주장별 독립 Verdict |
| `SELECT_RESEARCH_TARGET` | 자료에 맞는 탐구 대상을 선택 | 자료에서 직접 도출되는 연구 대상이 하나 |

QuestionType과 StemIntent의 조합은 무제한이 아니다. 예를 들어 `CHRONOLOGY_ORDER + SELECT_FUNCTION`은 의미가 없고, `INSTITUTION_RULE + SELECT_FUNCTION`은 자연스럽다. 이 호환성을 코드 분기로 하드코딩하지 않고 그래프의 정책 관계로 관리한다.

## 4. Modifier 사전

| 축 | 값 예시 | 역할 |
|---|---|---|
| `source_mode` | `PRIMARY_TEXT`, `DIALOGUE`, `IMAGE`, `MAP`, `TABLE`, `TIMELINE`, `LITERATURE`, `ADVERTISEMENT`, `NEWS`, `WEB_UI` | 지문 표현 방식 |
| `anchor_visibility` | `EXPLICIT`, `PARTIAL`, `IMPLICIT` | 정답 대상을 얼마나 직접 노출하는가 |
| `temporal_mode` | `NONE`, `BEFORE`, `AFTER`, `DURING`, `ORDER` | 시간 연산 |
| `answer_mode` | `ENTITY`, `STATEMENT`, `IMAGE`, `SEQUENCE`, `MATCH_SET` | 선지의 의미 구조 |
| `polarity` | `POSITIVE`, `NEGATIVE` | 참인 것 또는 거짓인 것 선택 |
| `anchor_count` | 1 이상 | 동시에 식별해야 하는 대상 수 |

이미지나 부정형을 QuestionType으로 만들지 않는 이유는 동일한 의미 경로 위에 독립적으로 붙을 수 있기 때문이다.

CompositionMode는 Modifier와 별도 축이며 `SINGLE_PATH`, `MULTI_ANCHOR_COMPARE`, `MAPPING_MATCH` 중 하나다. Modifier도 평면 문자열 목록이 아니라 축별로 정확히 하나의 값을 갖는 구조로 검증한다.

`answer_mode`는 단일 Entity ID로 고정하지 않는다. `STATEMENT`는 승인 Fact 묶음, `IMAGE`는 MediaAsset과 묘사 Entity, `SEQUENCE`는 순서가 있는 operand 목록, `MATCH_SET`은 검증된 pair 목록을 답 표현으로 사용한다. 각 형식의 ID와 근거 계약은 `QuestionMaterial.reference_binding`과 option candidate의 `OptionBinding`에 동일하게 보존한다.

polarity는 선지의 역사적 truth와 시험에서 선택할 선지를 뒤섞지 않는다.

| selection rule | 필수 truth 분포 | 선택되는 선지 |
|---|---|---|
| `SELECT_TRUE` | TRUE 1개 + FALSE `choice_count-1`개 | 유일한 TRUE |
| `SELECT_FALSE` | TRUE `choice_count-1`개 + FALSE 1개 | 유일한 FALSE |

`SELECT_FALSE`는 SELECT_TRUE용 FALSE 후보 여러 개를 그대로 재사용하지 않는다. correct anchor에 대해 참인 companion 선지를 충분히 모으고, mismatch proof가 있는 FALSE target 하나만 넣는 별도 CandidatePolicy가 필요하다.

## 5. 유형별 RAG 근거와 오답 교체 규칙

| 유형 | 정답 근거에 반드시 포함할 것 | 동일 패턴 오답의 참인 근거 |
|---|---|---|
| `ANCHOR_ATTRIBUTE` | anchor 식별 단서, 시대, 정답 속성 | 후보 속성이 실제로 속한 다른 시대·국가·정부 |
| `ACTOR_ACTIVITY` | 행위자 별칭·존속 기간, 정답 활동, 활동 시점과 역할 | 후보 활동을 실제 수행한 다른 행위자 |
| `EVENT_CONTEXT` | 사건 식별과 선택된 StemIntent에 맞는 배경·동시·인과·후속 context Fact | 같은 context 역할이 실제로 연결되는 다른 사건 |
| `TEMPORAL_RELATION` | 기준과 후보의 시작·종료 범위 | 후보 사건의 실제 시점과 기준과의 불일치 |
| `ASSOCIATED_ASSET` | 제작자·소유 시대·유형 | 후보 유산의 실제 제작자·국가·시대 |
| `INSTITUTION_RULE` | 기관·문서 식별, 기능·조항, 적용 시점 | 후보 기능·조항의 실제 기관·문서 |
| `LOCATION_EVENT` | 옛 지명·현재 지명·행정 범위, 사건 장소 | 후보 사건의 실제 장소 |

오답 근거는 “이 문장이 거짓이다”라는 생성 문장이 아니다. 다음처럼 다른 문맥에서 참인 Fact다.

```json
{
  "candidate_answer": "인왕제색도",
  "candidate_true_anchor": "정선",
  "predicate": "CREATED",
  "candidate_true_role": "creator",
  "evidence_query": "정선 인왕제색도 제작 작품 근거"
}
```

## 6. 난이도는 대상 하나의 속성이 아니다

78회 1·2·3점은 초기 참고 라벨이며 난이도 자체가 아니다. 표본 분포는 1점 10문항, 2점 30문항, 3점 10문항이다. 1점은 1·7·12·13·24·26·34·36·45·47번, 3점은 2·4·19·20·21·23·30·35·37·48번이고 나머지는 2점이다. 이 값은 cold-start 특징으로만 보존한다.

난이도는 다음 요소의 조합이다.

- `path_length`: 정답까지 필요한 의미 경로 길이
- `clue_count`: 결합해야 하는 단서 수
- `anchor_visibility`: 인물·사건명이 직접 나오는지
- `clue_indirectness`: 단서가 얼마나 우회적인지
- `temporal_reasoning`: 시간 구간 계산이 필요한지
- `operand_count`: 순서·비교 대상 수
- `distractor_similarity`: 같은 시대·국가·하위 유형인지
- `answer_obscurity`: 정답 엔터티의 학습 빈도와 인지도
- `visual_complexity`: 이미지·지도·표 해석 비용
- `negation_cost`: 부정 발문인지

같은 `ACTOR_ACTIVITY`라도 다음처럼 바뀐다.

```text
쉬움   인물명이 직접 제시됨 + 다른 시대 인물의 업적
보통   별칭과 대표 활동으로 인물 식별 + 같은 국가의 다른 인물 업적
어려움 복수 간접 단서 + 같은 시대·같은 역할 인물의 유사 업적
```

## 7. 유형과 난이도의 호환성

| 유형 | 쉬움 | 보통 | 어려움 | 생성 제한 |
|---|---:|---:|---:|---|
| `ANCHOR_ATTRIBUTE` | 가능 | 가능 | 조건부 | 어려움은 암묵적 anchor와 근접 오답 필요 |
| `ACTOR_ACTIVITY` | 가능 | 가능 | 가능 | 공동 활동은 답 유일성 확인 |
| `EVENT_CONTEXT` | 조건부 | 가능 | 가능 | 시간적 선후를 인과로 오인 금지 |
| `TEMPORAL_RELATION` | 가능 | 가능 | 가능 | 확정 가능한 시간 범위 필수 |
| `CHRONOLOGY_ORDER` | 비권장 | 가능 | 가능 | 동년·논쟁적 순서 제외 |
| `ASSOCIATED_ASSET` | 가능 | 가능 | 조건부 | 이미지 권리·식별·답 노출 검증 |
| `INSTITUTION_RULE` | 가능 | 가능 | 가능 | 여러 대상에 공통인 기능 제거 |
| `CompositionMode=MULTI_ANCHOR_COMPARE` | 비권장 | 가능 | 가능 | 모든 하위 주장 검증 |
| `LOCATION_EVENT` | 가능 | 가능 | 가능 | 공간 계층과 옛 지명 정규화 |
| `CompositionMode=MAPPING_MATCH` | 비권장 | 조건부 | 가능 | 전체 행 중 정답 하나 보장 |
| `NEGATIVE` modifier | 비권장 | 가능 | 가능 | 과다·연속 출제 제한 |

## 8. 랜덤 선택의 올바른 순서

유형과 난이도를 먼저 독립 난수로 선택하면 해당 anchor에서 만들 수 없는 조합이 자주 나온다. 다음 순서로 제한된 랜덤을 사용한다.

StemIntent 입력 모드는 `EXPLICIT`과 `AUTO`로 구분한다. `EXPLICIT`은 요청의 `stem_intent_id`를 필수 제약으로 사용하고, `AUTO`일 때만 호환 intent를 후보로 만들어 추첨한다.

1. 선택된 정답 바인딩에서 승인된 PathPattern을 찾는다.
2. 각 패턴이 지원하는 QuestionType과 StemIntent를 찾되 입력 모드를 적용한다.
3. selection rule의 target truth 분포, 정답 유일성과 최소 후보 수를 만족하는 조합만 남긴다.
4. 각 조합이 지원하는 DifficultyBand만 남긴다.
5. 최근 출제 이력과 세트 분포를 반영한 가중치를 적용한다.
6. 난수 시드를 기록하고 하나를 추첨한다.
7. 유형이 요구하는 source mode를 선택한 뒤 지문을 생성한다.

후보가 부족하면 무리하게 오답을 만들지 않는다. 가능한 유형 또는 난이도를 다시 선택하고, 그래도 불가능하면 해당 정답 바인딩의 생성을 중단한다.

## 9. PDF 분석 결과가 Neo4j에 들어가는 형태

들어가는 것은 50개 문항 본문이 아니라 다음 정책 노드다.

```text
QuestionType
StemIntent
Modifier
CompositionMode
QuestionBlueprint
PathPattern
PatternSlot
DifficultyBand
CandidatePolicy
ValidationRule
```

78회 문항 번호는 설계 근거 문서에만 남긴다. 런타임 그래프의 역사 지식과 생성 후보 검색에는 사용하지 않는다.

문제지와 해설지의 문장도 Fact의 승인 근거로 사용하지 않는다. 특히 분석한 해설지는 AI 활용 참고 자료이며 오류 가능성을 명시하고 있으므로, 유형·발문의도·오답 구조를 파악하는 용도로만 사용한다. 역사 Fact와 정답·오답 근거는 대백과사전 등 별도의 권위 원천과 RAG 청크에서 검증한다.

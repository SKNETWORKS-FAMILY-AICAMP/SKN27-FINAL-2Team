# Entity Resolution 골든셋 CSV 검수 안내

이 폴더의 두 CSV는 검색 결과를 그대로 승인하는 파일이 아니라, 사람이 원천 문맥을 읽고
정답을 작성하는 골든셋 검수본이다.

- `human_review_candidates.csv`: 원천 후보 606행을 각각 판정한다.
- `human_review_cases.csv`: 후보 판정을 모두 끝낸 뒤 용어 case 100건의 최종 상태를 정한다.
- 처음에는 `gold_case_order`가 1~10인 행부터 작성한다.
- 자동 생성 컬럼은 확인만 하고 수정하지 않는다.
- `*_json` 컬럼은 쉼표·따옴표·줄바꿈을 포함하므로 셀 내용을 직접 고치지 않는다.

## 1. 권장 작성 순서

1. `human_review_candidates.csv`에서 같은 `gold_case_id`의 후보를 모두 찾는다.
2. 기출문제 문맥과 각 후보의 원천 문맥을 비교한다.
3. 각 후보의 마지막 7개 검수 컬럼을 작성한다.
4. 같은 역사 실체를 직접 설명하는 후보끼리 같은 `gold_alternative_key`로 묶는다.
5. 해당 case의 모든 candidate를 판정한 뒤 `human_review_cases.csv`의 마지막 5개 컬럼을 작성한다.
6. 확신할 수 없는 행은 억지로 합치거나 버리지 말고 `NEEDS_DISCUSSION`으로 남긴다.
7. 작성 중에는 partial importer를 실행해 누락·모순을 확인한다.

## 2. `human_review_candidates.csv`

한 행은 검색으로 회수된 원천 레코드 후보 하나다. 이 파일에서는 해당 레코드가 용어가
가리키는 역사 실체 자체인지, 다른 실체의 문서인지, 검색 오탐인지 판정한다.

### 2.1 자동 생성 컬럼 — 수정 금지

| 컬럼 | 의미 | 들어 있는 값·해석 |
|---|---|---|
| `gold_case_order` | 사람이 검수할 순서 | `1`~`100`. 처음에는 1~10부터 작성한다. |
| `gold_case_id` | 골든셋 내부 case 식별자 | `gold-case:...` 형식. 두 CSV를 묶는 기본 키다. |
| `term_review_task_id` | 용어 단위 의미 판정 task 식별자 | `term-review-task:...` 형식. LLM 평가 결과와 연결할 때 사용한다. |
| `resolution_case_id` | 전체 Entity Resolution case 식별자 | `resolution-case:...` 형식. 전체 파이프라인 staging 결과와 연결한다. |
| `canonical_term` | 기출문제에서 추출된 대표 용어 | 예: `고종`, `거친무늬 거울`, `동북 9성`. 아직 canonical entity가 확정됐다는 뜻은 아니다. |
| `category` | 용어 추출 단계의 원본 15분류 | `인물`, `사건`, `국가`, `왕조`, `제도`, `정책`, `단체`, `기관`, `문헌`, `문화재`, `조약`, `사상`, `지명`, `유물`, `유적`. 원본 분류이므로 보존한다. |
| `problem_context_samples_json` | 용어가 등장한 기출문제 문맥 | `problem_id`, `full_text`를 가진 JSON 배열. 후보가 어떤 의미로 사용됐는지 판단하는 핵심 근거다. |
| `source_candidate_id` | 이번 판정 패키지 내부의 후보 식별자 | `source-candidate:...` 형식. 후보 역할과 identity 그룹을 기록할 때 사용하는 ID다. |
| `source_record_id` | 실제 원천 레코드의 안정 식별자 | `AKS:ARTICLE:...`, `THESAURUS:TERM:...`, `ITKC:PERSON:...`, `ITKC:EVENT:...` 형식. 원천 provenance를 보존한다. |
| `source` | 후보가 나온 원천 종류 | `AKS`, `THESAURUS`, `ITKC_PERSON`, `ITKC_EVENT`. |
| `candidate_rank` | 검색 단계에서의 후보 순위 | `1` 이상의 정수. 정답 우선순위가 아니라 검색 정렬 순서일 뿐이다. |
| `matched_name` | 검색에 걸린 후보 레코드의 대표명 | 이름 검색이면 일치한 표제명·별칭, 설명 검색이면 그 설명을 가진 레코드의 표제명이다. |
| `matched_field` | 용어가 검색된 필드 | `name`: 이름·별칭에서 검색, `description`: 설명 본문에서 검색. `description`이라고 무조건 `EVIDENCE_ONLY`인 것은 아니다. |
| `retrieval_method` | 후보를 회수한 검색 방식 | `exact`, `bidirectional_containment`, `name_ngram`, `description_ngram`. 정확 일치부터 이름·설명 확장 검색까지의 경로를 나타낸다. |
| `retrieval_score` | 검색 문자열 유사도 | `0`~`1` 사이 실수. `1.0`은 정확 일치다. 역사적 동일성의 확률이나 정답 confidence가 아니다. |
| `category_compatibility` | 추출 category와 원천 유형의 코드 비교 결과 | `COMPATIBLE`, `CONFLICT`, `UNKNOWN`. 참고용 자동 제안이며 원천 문맥보다 우선하지 않는다. |
| `normalized_names_json` | 후보에서 수집한 정규화 이름·별칭 | 문자열 JSON 배열. 띄어쓰기 제거형, 한자 병기형 등이 들어갈 수 있다. |
| `hanja_json` | 후보의 한자 표기 | 문자열 JSON 배열. 정보가 없으면 `[]`다. 이름이 같을 때 실체를 구분하는 주요 신호다. |
| `era_values_json` | 후보가 지지하는 시대 | 문자열 JSON 배열. 예: `["선사", "청동기"]`. 동일 이름 후보의 시대 충돌 여부를 확인한다. |
| `birth_year` | 인물 후보의 출생 연도 | 정수 연도 또는 빈칸. 다른 인물과의 병합 여부를 판정하는 강한 신호다. |
| `death_year` | 인물 후보의 사망 연도 | 정수 연도 또는 빈칸. 생몰년이 충돌하면 같은 실체로 묶지 않는다. |
| `bonkwan_json` | 인물 후보의 본관 | 문자열 JSON 배열. 정보가 없으면 `[]`다. 동명이인 구분에 사용할 수 있다. |
| `source_entity_type_proposal` | 원천 필드로부터 코드가 추정한 EntityType | 9개 EntityType 중 하나 또는 빈칸. 최종 정답이 아니며 사람이 원천 문맥을 보고 다시 판단한다. |
| `source_context_json` | 원천 레코드의 실제 판정 문맥 | AKS의 표제어·유형·시대·정의, 시소러스의 한자·시대·설명, ITKC 인물·사건 필드가 JSON 객체로 들어 있다. |
| `candidate_pair_signals_json` | 같은 case의 다른 후보와 비교한 병합 신호·충돌 | `normalized_name_match`, `hanja_match`, `era_overlap`, `bonkwan_match` 등의 신호와 `entity_type_conflict`, `birth_year_conflict`, `death_year_conflict` 등의 충돌이 들어 있다. 코드 제안이지 사람 정답이 아니다. |

### 2.2 사람이 작성하는 마지막 7개 컬럼

#### `gold_candidate_role`

후보 레코드의 의미 역할을 다음 중 하나로 입력한다.

- `IDENTITY_MEMBER`: 이 레코드가 해당 역사 실체 자체를 직접 설명한다.
  - 예: `거친무늬 거울` 용어에 대한 AKS의 「거친무늬 거울」 문서
  - 같은 실체를 설명하는 AKS·시소러스·ITKC 레코드는 모두 identity member가 될 수 있다.
- `EVIDENCE_ONLY`: 용어를 언급하거나 관계 근거를 제공하지만 문서의 주 대상은 다른 실체다.
  - 예: `황궁`을 설명에 포함하지만 표제어와 주 대상은 `여정궁`인 문서
- `REJECTED`: 문자열 검색 오탐이거나 category·시대·대상이 일치하지 않는다.
- `AMBIGUOUS`: 현재 기출문제와 원천 문맥만으로 위 세 역할 중 하나를 확정할 수 없다.

입력 후보는 누락 없이 정확히 한 역할로 분류해야 한다.

#### `gold_alternative_key`

- `IDENTITY_MEMBER`일 때만 작성한다.
- 같은 실체를 설명하는 후보에는 같은 case-local 키를 사용한다.
- 첫 실체는 `ALT_001`, 다른 실체는 `ALT_002`, 그다음은 `ALT_003`처럼 작성한다.
- case가 바뀌면 다시 `ALT_001`부터 시작해도 된다.
- `EVIDENCE_ONLY`, `REJECTED`, `AMBIGUOUS`에서는 반드시 빈칸으로 둔다.

예를 들어 고려 고종 후보와 조선 고종 후보가 함께 있다면 다음처럼 분리한다.

```text
고려 고종을 직접 설명하는 후보들 -> ALT_001
조선 고종을 직접 설명하는 후보들 -> ALT_002
두 고종 중 어느 쪽인지 판단 불가  -> AMBIGUOUS, 대안 키는 빈칸
```

#### `gold_display_name`

- `IDENTITY_MEMBER`일 때만 작성한다.
- 사람이 보았을 때 다른 실체와 구분되는 표시명을 쓴다.
- 예: `고종(고려)`, `고종(조선)`, `삼시 협정(미쓰야 협정)`
- 같은 `gold_alternative_key`에 속한 행은 철자와 띄어쓰기까지 정확히 같은 표시명을 써야 한다.
- 다른 역할에서는 빈칸으로 둔다.

#### `gold_entity_type`

- `IDENTITY_MEMBER`일 때만 작성한다.
- 같은 `gold_alternative_key`의 모든 행에 같은 타입을 입력한다.
- 다른 역할에서는 빈칸으로 둔다.

| 값 | 의미·대표 대상 |
|---|---|
| `Person` | 인물 |
| `Event` | 역사 사건, 전쟁, 협정 체결 사건 등 |
| `Institution` | 제도 또는 공식 기관 |
| `Heritage` | 유물, 유적, 문화재 |
| `Work` | 문헌, 저술, 기록물, 작품 |
| `Organization` | 단체, 조직 |
| `Place` | 지명, 장소 |
| `Polity` | 국가, 왕조, 정치체 |
| `Concept` | 정책, 사상, 추상 개념 |

#### `gold_reason`

- 모든 candidate 역할에서 작성한다.
- `candidate_review_status=COMPLETE`일 때 필수다.
- 이름만 적지 말고 판정에 사용한 구분 신호를 짧게 남긴다.
- 좋은 예:
  - `표제명과 한자가 일치하고 청동기시대 거울을 직접 설명하므로 동일 실체`
  - `본문에서 용어를 언급하지만 표제어와 주 대상은 다른 사건이므로 EVIDENCE_ONLY`
  - `이름 일부만 유사하고 시대와 EntityType이 달라 REJECTED`

#### `reviewer`

- 검수자 이름 또는 식별자를 입력한다.
- 예: `MK`
- `candidate_review_status=COMPLETE`일 때 필수다.

#### `candidate_review_status`

| 값 | 의미 |
|---|---|
| `NOT_STARTED` | 아직 검수를 시작하지 않음 |
| `IN_PROGRESS` | 일부 컬럼을 작성 중 |
| `COMPLETE` | 역할·근거·필수 대안 정보까지 확정 완료 |
| `NEEDS_DISCUSSION` | 단독 판단이 어려워 다른 검수자와 논의 필요 |

`COMPLETE`가 아닌 행은 최종 gold decision으로 사용할 수 없다.

## 3. `human_review_cases.csv`

한 행은 하나의 추출 용어 case다. 같은 case에 속한 candidate를 모두 판정한 뒤, 이 파일에서
원천 연결의 최종 상태와 문항별 동음이의어 판정 필요 여부를 작성한다.

### 3.1 자동 생성 컬럼 — 수정 금지

| 컬럼 | 의미 | 들어 있는 값·해석 |
|---|---|---|
| `gold_case_order` | 사람이 검수할 순서 | `1`~`100`. candidate 파일의 같은 번호와 함께 본다. |
| `gold_case_id` | 골든셋 case 식별자 | candidate 파일의 같은 ID 행들을 이 case에 연결한다. |
| `term_review_task_id` | 용어 판정 task 식별자 | 모델 판정과 사람 정답을 비교할 때 사용한다. |
| `resolution_case_id` | 전체 ER case 식별자 | 전체 staging 결과와 연결한다. |
| `canonical_term` | 판정 대상 대표 용어 | 아직 canonical entity가 확정됐다는 의미는 아니다. |
| `category` | 추출 단계의 원본 15분류 | 후보를 보는 출발점이지만 원천 문맥과 충돌하면 검수 근거에 기록한다. |
| `entity_type_proposal` | category를 기준으로 코드가 제안한 EntityType | 9개 타입 중 하나. 최종 `gold_entity_type`과 달라도 된다. |
| `problem_count` | 해당 용어가 등장한 기출문제 수 | `1` 이상의 정수. 여러 문항에서 의미가 달라지는지 확인한다. |
| `candidate_count` | 이 case의 원천 후보 수 | candidate 파일에서 같은 `gold_case_id`를 가진 행 수다. |
| `source_count` | 후보가 나온 서로 다른 source 값의 수 | AKS·시소러스·ITKC 인물·ITKC 사건 중 몇 종류가 회수됐는지 나타낸다. |
| `code_alternative_count` | 코드가 사전에 제안한 동일 실체 그룹 수 | `0` 이상의 정수. 사람 정답과 달라도 되며 그대로 따라 쓰지 않는다. |
| `candidate_count_bucket` | 후보 수 구간 | `C01_02`, `C03_05`, `C06_09`, `C10`, `C11_PLUS`. 표본 층화와 평가에 사용한다. |
| `retrieval_profile` | 후보 검색 구성 | `EXACT_ONLY`, `EXACT_AND_EXPANDED`, `EXPANDED_ONLY`. 정확 검색 후보가 포함됐는지를 나타낸다. |
| `multi_source_supported` | 코드 제안 대안 중 여러 원천이 함께 지지하는 대안 존재 여부 | `True` 또는 `False`. `True`여도 자동 정답으로 간주하지 않는다. |
| `conflict_pair_count` | 강한 충돌이 발견된 후보 쌍 개수 | `0` 이상의 정수. 생몰년·유형 충돌 후보를 같은 대안으로 합치지 않았는지 확인한다. |
| `problem_context_samples_json` | 해당 용어가 등장한 기출문제 문맥 | `problem_id`, `full_text`를 가진 JSON 배열. 문항별 실체가 달라질 가능성을 판단한다. |

### 3.2 사람이 작성하는 마지막 5개 컬럼

#### `gold_link_status`

| 값 | 사용 기준 |
|---|---|
| `ACCEPTED` | 하나 이상의 역사 실체 identity 그룹을 확정했다. candidate 파일에 `IDENTITY_MEMBER`가 하나 이상 있어야 한다. |
| `AMBIGUOUS` | 현재 근거로 일부 후보 역할 또는 실체를 확정할 수 없다. candidate 파일에 `AMBIGUOUS` 역할이 하나 이상 있어야 한다. |
| `UNRESOLVED` | 역사 용어 자체는 유효하지만 현재 후보 중 해당 실체를 직접 설명하는 원천을 찾지 못했다. `IDENTITY_MEMBER`가 없어야 한다. |
| `REJECTED` | 추출 노이즈이거나 역사 엔티티로 유지할 필요가 없는 case다. `IDENTITY_MEMBER`가 없어야 한다. |

서로 다른 실체가 두 개라는 사실을 확인한 경우는 `AMBIGUOUS`가 아니다. 각 실체를
`ALT_001`, `ALT_002`로 분리하고 case는 `ACCEPTED`로 작성한 뒤 문항별 재판정을 요청한다.

#### `requires_problem_review`

- `YES`: 동일 용어에 확정된 실체가 여러 개이고 기출문제마다 어느 실체인지 골라야 한다.
- `NO`: 문항별 추가 선택이 필요하지 않다.
- identity 대안이 두 개 이상이면 반드시 `YES`여야 한다.
- 예: 고려 고종과 조선 고종을 각각 확정했다면 `YES`다.

#### `gold_decision_reason`

- case 전체의 `gold_link_status`와 `requires_problem_review`를 선택한 이유를 적는다.
- `case_review_status=COMPLETE`일 때 필수다.
- 예:
  - `AKS와 시소러스가 같은 한자·시대의 유물을 직접 설명하므로 ACCEPTED`
  - `고려와 조선의 서로 다른 인물이 모두 확인되어 문항별 재판정 필요`
  - `후보가 모두 다른 실체를 설명하지만 용어 자체는 유효하므로 UNRESOLVED`

#### `reviewer`

- case 최종 판정자 이름 또는 식별자를 입력한다.
- 예: `MK`
- `case_review_status=COMPLETE`일 때 필수다.

#### `case_review_status`

| 값 | 의미 |
|---|---|
| `NOT_STARTED` | 아직 case 결론을 작성하지 않음 |
| `IN_PROGRESS` | candidate 또는 case 판정 작성 중 |
| `COMPLETE` | 모든 candidate 판정과 case 결론이 완전함 |
| `NEEDS_DISCUSSION` | 최종 상태 또는 문항별 재판정 여부를 논의해야 함 |

같은 case의 candidate가 모두 `COMPLETE`가 된 뒤 case도 `COMPLETE`로 변경한다.

## 4. 반드시 지켜야 하는 일관성 규칙

1. 같은 `gold_alternative_key`의 candidate는 `gold_display_name`과 `gold_entity_type`이 정확히 같아야 한다.
2. `IDENTITY_MEMBER`는 대안 키·표시명·EntityType을 모두 작성해야 한다.
3. `IDENTITY_MEMBER`가 아닌 역할은 대안 키·표시명·EntityType을 모두 빈칸으로 둔다.
4. `ACCEPTED` case에는 identity 대안이 하나 이상 있어야 한다.
5. `UNRESOLVED`와 `REJECTED` case에는 identity 대안이 없어야 한다.
6. `AMBIGUOUS` case에는 `AMBIGUOUS` candidate가 하나 이상 있어야 한다.
7. case가 `AMBIGUOUS`가 아니면 candidate 역할에도 `AMBIGUOUS`가 남아 있으면 안 된다.
8. identity 대안이 두 개 이상이면 `requires_problem_review=YES`여야 한다.
9. 시대·생몰년·EntityType이 강하게 충돌하는 후보를 같은 대안으로 묶지 않는다.
10. 이름만 같다는 이유로 서로 다른 원천 후보를 자동으로 같은 실체로 합치지 않는다.

## 5. 대표 작성 예시

### 같은 실체를 여러 원천이 설명하는 경우

```text
AKS 정답 문서       -> IDENTITY_MEMBER / ALT_001 / 같은 표시명 / 같은 EntityType
시소러스 정답 문서 -> IDENTITY_MEMBER / ALT_001 / 같은 표시명 / 같은 EntityType
다른 문서의 언급   -> EVIDENCE_ONLY / 대안 관련 3개 컬럼은 빈칸
검색 오탐          -> REJECTED / 대안 관련 3개 컬럼은 빈칸
case               -> ACCEPTED / requires_problem_review=NO
```

### 동음이의어가 확정된 경우

```text
고려 고종 후보들 -> IDENTITY_MEMBER / ALT_001 / 고종(고려) / Person
조선 고종 후보들 -> IDENTITY_MEMBER / ALT_002 / 고종(조선) / Person
case              -> ACCEPTED / requires_problem_review=YES
```

### 정답 원천을 찾지 못한 경우

```text
용어는 유효하지만 후보가 모두 다른 대상 -> 후보별 EVIDENCE_ONLY 또는 REJECTED
case                                  -> UNRESOLVED / requires_problem_review=NO
```

## 6. 작성 중 검증 명령

프로젝트 루트에서 다음 명령을 실행한다. `--allow-partial`은 미완료 행을 오류로 중단하지 않고
현재 진행 상태와 작성 모순을 검사한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/import_gold_set.py `
  etl/preprocessing/neo4j/goldset/human_review_csv `
  etl/preprocessing/neo4j/goldset/internal/source_snapshot/gold_review_tasks.jsonl `
  etl/preprocessing/neo4j/goldset/internal/validation `
  --allow-partial
```

검증 결과는 `goldset/internal/validation`에 생성된다. 사람이 직접 작성하는 두 CSV는
검증 명령으로 덮어쓰지 않는다.

## 7. CSV 편집 주의사항

- JSON 컬럼의 내부 따옴표와 줄바꿈을 수정하지 않는다.
- 행을 추가·삭제하거나 순서를 임의로 변경하지 않는다.
- ID, 용어, category, 원천 문맥과 검색 feature 컬럼은 수정하지 않는다.
- 문자열 앞뒤에 불필요한 공백을 넣지 않는다.
- 같은 대안의 표시명과 EntityType은 복사해 정확히 동일하게 맞춘다.
- 가능하면 Cursor의 CSV 표 편집 확장 또는 CSV를 안전하게 처리하는 표 형태 편집기를 사용한다.

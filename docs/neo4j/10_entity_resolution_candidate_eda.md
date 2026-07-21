# 10. 기출 용어 Entity Resolution 후보 EDA

> 상태: `IMPLEMENTATION-EDA`
> 실행일: 2026-07-21
> 후보 정책: `entity-resolution-candidate-v2.4`
> 입력 용어: 기존 `exam_history_terms.csv` 4,803행
> 주의: 이 문서의 수치는 `ACCEPTED` 해소율이 아니라 검증 전 `PROPOSED` 후보 분포다.

## 1. 목적

기출 용어를 AKS·한국역사용어시소러스·ITKC 레코드와 연결할 때 특정 용어별 예외를
두지 않고 다음을 확인한다.

- 이름 변형과 설명 검색으로 실제 정답 후보를 회수할 수 있는가
- 짧은 일반어 후보를 얼마나 억제할 수 있는가
- 문항별 동음이의 판별에 필요한 입력을 손실 없이 만들 수 있는가
- 현재 데이터만으로 자동 `ACCEPTED`할 수 있는 범위가 어디까지인가
- 최종 Neo4j 적재 전에 어떤 검증 데이터가 더 필요한가

이번 실행에서는 LLM을 호출하지 않았다. 기존 용어 CSV를 사용해 후보 생성과 staging
패키지만 만들었다.

## 2. 입력과 재현 조건

| 입력 | 레코드 수 | 사용 필드 |
|---|---:|---|
| 추출 용어 집계 | 4,803 | `canonical_term`, `category`, `count`, `problem_ids` |
| AKS 상세 | 75,835 | `eid`, 표제어, 객체·문자열형 이칭, 유형, 시대, 정의 |
| 시소러스 | 62,409 | `term_id`, 표제어, 한자, 시대, 분류 경로, 설명 |
| ITKC 인물 | 이름 키 44,173 | `person_id`, 이름·한자, 생몰년, 본관 |
| ITKC 사건 | 1,542 | `event_id`, 사건명, 분류, 기간 |

원천 release는 파일 내용 SHA-256의 앞 16자리로 만들었다. SourceRecord 후보 ID는 다음
형식이다.

```text
AKS:ARTICLE:{eid}:{source_release}
THESAURUS:TERM:{term_id}:{source_release}
ITKC:PERSON:{person_id}:{source_release}
ITKC:EVENT:{event_id}:{source_release}
```

정규화·점수·후보 상한·category 호환표·15분류→EntityType 매핑은
`etl/preprocessing/neo4j/config/resolution_policy.json`에서 읽는다. 특정 용어에 대한
치환표나 예외 분기는 두지 않았다.

## 3. 후보 검색 정책 변화

| 정책 | 후보 존재율 | 미회수 | 실행 시간 | 해석 |
|---|---:|---:|---:|---|
| 초기 고재현율 | 98.9% | 54 | 약 399초 | 짧은 부분 문자열과 일반어 오탐이 많음 |
| n-gram 재계산 제거·평가 상한 적용 | 98.9% | 54 | 약 133초 | 회수율 유지, 실행 시간 감소 |
| v2.3 포함 길이 보정 | 97.9% | 99 | 약 133~145초 | 일반어 오탐 감소, 핵심 회귀 사례 유지 |

실행 시간은 동일 PC의 단일 실행 관측값이며 정식 성능 벤치마크가 아니다. JSON 저장과
파일 캐시 상태에 따라 달라질 수 있다.

v2.3은 포함 일치를 이진값으로 보지 않고 `짧은 이름 길이 / 긴 이름 길이`의 제곱을
사용한다. 이 변경으로 긴 질의의 일부에 불과한 `협정`, `선언`, `반도` 같은 후보 점수가
낮아졌다.

## 4. 회귀 사례

| 추출 용어 | 회수된 실제 후보 | 관측 결과 |
|---|---|---|
| 동북 9성 | 시소러스 `term_id=979`, `9성` | 후보 유지 |
| 미쓰야 협정 | 시소러스 `삼시협정` 2건 | 일반어 `협정`보다 상위 |
| 범금 8조 | AKS `E0059778 팔조법금`, 시소러스 `8696 8조법금` | 양쪽 모두 유지 |
| 한반도 비핵화 공동 선언 | AKS `E0078904` | 최상위 유지, `반도·선언·화승` 오탐 제거 |

이 결과는 identity 확정이 아니라 정답이 후보 집합에 포함됐다는 뜻이다.

## 5. 이름·설명 후보 결과

비노이즈 4,762개 중 하나 이상의 이름 후보를 가진 용어는 4,663개로 97.9%였다.
이름 후보가 없던 99개를 AKS definition으로 검색한 결과는 다음과 같다.

| 항목 | 건수 |
|---|---:|
| definition 검색 대상 | 99 |
| definition 후보 발견 | 15 |
| 후보 1건 | 11 |
| 후보 전부 category–AKS 유형 충돌 | 8 |
| definition에서도 미발견 | 84 |

definition 후보 15개를 곧바로 해결된 용어로 계산하지 않는다. 유형 충돌 8개가 있고,
나머지도 definition 문자열 유사도만 확인된 `PROPOSED` 후보이기 때문이다.

## 6. ER staging 패키지 결과

생성 파일은 다음과 같다.

```text
cases_requiring_review.csv
internal/entity_cases.csv
internal/candidate_source_records.csv
internal/candidate_comparison_features.csv
internal/candidate_pair_merge_signals.csv
internal/proposed_entity_groups.csv
internal/proposed_entity_group_members.csv
internal/exam_problem_contexts.csv
internal/exam_problem_entity_assignments_draft.csv
```

| 테이블·상태 | 건수 |
|---|---:|
| resolution case | 4,772 |
| SourceRecord 후보 | 41,274 |
| 동일 case 내 후보 쌍 | 181,726 |
| 병합 적격 제안 쌍 | 6,677 |
| canonical 대안 cluster | 28,164 |
| 복수 원천 지지 cluster | 4,563 |
| 문제 원문 | 1,597 |
| 문항-용어 assignment | 13,696 |
| review queue | 13,547 |
| `AMBIGUOUS` case | 4,638 |
| `UNRESOLVED` case | 84 |
| `REJECTED` case | 50 |
| `ACCEPTED` case | 0 |

`REJECTED` 50건은 노이즈 의심 용어 41개와 허용 category 밖 추출 9개다. 노이즈는 review
queue에서 제외하고, 비허용 category는 `INVALID_EXTRACTION_CATEGORY`로 남겨 문항별
재추출 대상으로 보낸다.

입력 4,803행 중 정규화 키와 category가 같은 31쌍은 하나의 resolution case로 병합했다.
숫자 한글·아라비아 표기, 가운데점 종류, 공백·하이픈, 두음 표기 차이가 주된 원인이다.
대표 표기만 남기지 않고 `term_variants_json`과 모든 `problem_id`를 보존했다.

### 6.1 비허용 category

| 용어 | 기존 category | 문제 수 |
|---|---|---:|
| 삼백 산업 | 산업 | 4 |
| 금 모으기 운동 | 운동 | 4 |
| 무신 집권기 | 시기 | 3 |
| 인민 | 일반명사 | 2 |
| 민립 대학 설립 운동 | 운동 | 2 |
| 사회주의자 | 제외 | 1 |
| 구석기 | 시대 | 1 |
| 좌우 합작 운동 | 운동 | 1 |
| 강씨 | 제외 | 1 |

이 값을 임의로 15분류 중 하나에 매핑하지 않는다. 최신 추출 정책으로 다시 판정해야 한다.

### 6.2 후보 수 분포

| case당 후보 수 | case 수 |
|---|---:|
| 0 | 90 |
| 1 | 109 |
| 2 | 150 |
| 3~5 | 457 |
| 6~10 | 3,293 |
| 11~20 | 672 |
| 21 이상 | 1 |

평균 후보 수는 8.65, 중앙값은 10, 최댓값은 57이다. 최댓값이 정책 상한보다 큰 이유는
동명이인 손실 방지를 위해 정확 일치 후보는 상한을 초과해도 모두 보존하기 때문이다.

### 6.3 후보 원천과 검색 방식

| 원천 | 후보 수 |
|---|---:|
| THESAURUS | 20,172 |
| AKS | 19,491 |
| ITKC_PERSON | 1,021 |
| ITKC_EVENT | 590 |

| 검색 방식 | 후보 수 |
|---|---:|
| exact | 8,352 |
| bidirectional_containment | 17,162 |
| name_ngram | 14,248 |
| description_ngram | 1,512 |

AKS category 호환을 판정할 수 있는 후보 중 `COMPATIBLE`은 11,259건, `CONFLICT`는
8,232건이었다. 시소러스와 ITKC는 전체 category 호환 판정표가 아직 없으므로 21,783건을
`UNKNOWN`으로 유지했다.

### 6.4 canonical 대안 제안 결과

| 제안 역할·cluster 상태 | 건수 |
|---|---:|
| `IDENTITY_MEMBER` SourceRecord | 9,441 |
| `ALTERNATIVE_ENTITY` SourceRecord | 3,100 |
| `AMBIGUOUS` SourceRecord | 20,501 |
| `REJECTED` SourceRecord | 8,232 |
| 1개 SourceRecord cluster | 23,601 |
| 2개 SourceRecord cluster | 4,248 |
| 3개 SourceRecord cluster | 315 |

`IDENTITY_MEMBER`는 서로 다른 원천의 레코드가 이름과 추가 구분 신호를 함께 만족해 같은
실체일 가능성이 높다는 **제안**이다. `ALTERNATIVE_ENTITY`는 독립적인 단일 후보,
`AMBIGUOUS`는 의미 판별이 더 필요한 후보다. `EVIDENCE_ONLY`는 본문 의미 판별 전에는 코드가
억지로 부여하지 않는다. 모든 역할과 cluster 상태는 `PROPOSED`이며 아직 Neo4j의
`RESOLVES_TO {match_status: ACCEPTED}`를 만들 수 없다.

### 6.5 후속 단계 입력 계약

| CSV | 역할 | 후속 사용 |
|---|---|---|
| `cases_requiring_review.csv` | 미확정 검토 작업 | 결정 결과 기록·재실행 |
| `internal/entity_cases.csv` | 정규화 용어·category·문항 집합 | `EntityName` 생성 기준 |
| `internal/candidate_source_records.csv` | 검색으로 회수한 원천 레코드 | `SourceRecord` 후보와 원천 메타데이터 |
| `internal/candidate_comparison_features.csv` | 이름·한자·시대·생몰년·유형 feature와 제안 역할 | 검증 gate 입력 |
| `internal/candidate_pair_merge_signals.csv` | 후보 쌍의 독립 신호·충돌 | 병합 재현·감사 |
| `internal/proposed_entity_groups.csv` | 같은 용어가 가리킬 수 있는 실체 대안 | 문항별 entity 판별 선택지 |
| `internal/proposed_entity_group_members.csv` | 대안별 복수 SourceRecord 구성 | 승인 후 `RESOLVES_TO` 생성 기준 |
| `internal/exam_problem_entity_assignments_draft.csv` | 문항–용어–대안 목록 | 동음이의 문항 판별 |
| `internal/exam_problem_contexts.csv` | 문항 원문 | 문항 단위 LLM 입력 |

이 패키지만으로 Neo4j 전체를 즉시 채우지는 않는다. 승인된 identity 결과가
`CanonicalEntity`·`SourceRecord`·`EntityName`·`RESOLVES_TO`·`REFERS_TO`를 만들고,
Anchor 관계는 별도 카탈로그, 역사 관계와 `EvidenceSpan`은 AKS 본문·ITKC 근거 원문 추출
결과를 추가로 조인해야 한다.

## 7. 핵심 해석

### 7.1 후보 수와 엔티티 대안 수는 다르다

한 case의 AKS·시소러스·ITKC 후보 여러 건이 모두 서로 다른 엔티티라는 뜻은 아니다.
같은 역사 실체를 설명하는 서로 다른 SourceRecord일 수 있다. 따라서 검증기가 후보 한 건만
고르는 구조면 다른 원천 provenance를 잃는다.

다음 단계는 두 층을 구분해야 한다.

```text
원천 레코드 후보
  → 같은 실체로 묶을 수 있는 SourceRecord cluster 제안
  → 서로 다른 canonical 대안 비교
  → 문항별 canonical 대안 배정
```

원천 간 이름만 같은 경우에는 cluster를 자동 확정하지 않는다. 현재 코드는 다음 조건을 모두
만족할 때만 병합 적격 쌍을 만든다.

- 정규화 이름 일치
- 전체 독립 신호 2개 이상
- 한자·생몰년·시대·본관 중 구분 신호 1개 이상
- EntityType·출생년·사망년 강한 충돌 0개
- 서로 다른 원천 시스템

cluster는 단순 연결 요소가 아니라 complete-link 방식으로 만든다. A–B와 B–C가 맞더라도
A–C가 적격이 아니면 세 레코드를 한 묶음으로 만들지 않는다. 따라서 간접 연결에 의한
동음이의 오병합을 막는다.

### 7.2 현재 자동 ACCEPTED 범위

정확 일치 후보는 8,352건, 정확 일치가 있는 case는 3,250개다. AKS에서
`exact + category compatible`인 후보는 2,988건이지만, 다른 원천의 정확 일치 후보까지
포함해 전체 정확 후보가 단 하나뿐인 case는 274개다.

274개도 아직 자동 승인하지 않았다. v2.4에서 한자·원천 시대 토큰·정수형 생몰년·본관·
EntityType의 쌍 신호와 충돌 검사를 구현했지만, 다음 검사는 아직 남아 있기 때문이다.

- 시대명과 절대 연도의 양립 여부
- AKS 서술에서 재위·생몰년을 구조화하는 작업
- 자·호·부친과 ITKC 관계망을 이용한 인물 구분
- 동일 이름의 다른 polity·왕조 구분
- 설명에서만 회수된 후보의 `EVIDENCE_ONLY` 판정
- LLM 제안에 대한 코드 gate와 사람 검토 표본

따라서 현재 `ACCEPTED=0`은 실패가 아니라 자동 병합 금지 원칙을 지킨 결과다.

## 8. canonical ID와 동음이의 처리

`resolution_case_id`는 staging 재현을 위해 용어·category·정규화 버전으로 계산한다.
이 ID는 `canonical_id`가 아니다.

`canonical_id`는 검증된 SourceRecord cluster 또는 기존 canonical registry를 선택한 뒤
영구 UUID로 발급해야 한다. 주 원천 ID가 바뀌어도 기존 UUID는 유지하고 SourceRecord 링크만
갱신한다.

`고종`처럼 한 표기가 여러 실체를 가리키면 다음을 별도로 보존한다.

```text
동일 resolution case
  ├─ problem A → 고종(고려)
  └─ problem B → 고종(조선)
```

이를 위해 `internal/exam_problem_entity_assignments_draft.csv`는 문항마다 별도 행을 가지며,
`canonical_alternative_ids_json`에 선택 가능한 cluster 목록을 보존한다. 문항 판별 전에는
`selected_canonical_alternative_id`와 `canonical_id`를 비워 둔다. SourceRecord 하나를
고르는 컬럼은 사용하지 않는다.

## 9. 모델 사용 결정

- 대량 용어 추출: `gpt-5.6-terra`
- SourceRecord 의미 역할 판별: `gpt-5.6-terra`
- 문항별 복수 canonical 대안 판별: `gpt-5.6-sol`
- 모든 LLM 판정 상태: `PROPOSED`
- 코드 gate 통과 후에만 `ACCEPTED`

이번 EDA에서는 두 모델 모두 호출하지 않았다. 기존 체크포인트는 모델·추출 정책 메타데이터가
없으므로 새 실행에서 자동 재사용하지 않는다. 전체 1,500문항 재추출 전에 50~100문항
골든셋으로 정밀도·재현율·category 정확도를 비교해야 한다.

### 9.1 term-level 검토 task

`AMBIGUOUS` case를 대상으로 다음 파일을 생성했다.

```text
output/04_llm_review/internal/term_identity_review_tasks.jsonl
```

| 항목 | 값 |
|---|---:|
| task 수 | 4,638 |
| 파일 크기 | 65,736,879 bytes |
| task당 평균 SourceRecord 후보 | 8.84 |
| task당 최대 SourceRecord 후보 | 57 |
| task JSON 평균 길이 | 약 12,050자 |
| task JSON 최대 길이 | 약 75,233자 |

모델은 후보 하나를 선택하지 않는다. 같은 실체를 직접 설명하는 후보들을
`identity_member_source_candidate_ids` 배열로 묶고, 나머지를 `EVIDENCE_ONLY`,
`REJECTED`, `AMBIGUOUS`로 모두 분류한다. 출력은 항상 `PROPOSED`다.

term gate는 다음을 검사한다.

- 입력 후보의 누락·중복·외부 ID 사용
- 모델·prompt·정책 버전
- category 충돌 후보의 identity 편입
- 대안 내부 모든 후보 쌍의 강한 충돌
- complete-link 병합 근거
- 미해소 `AMBIGUOUS` 존재 여부

구조 오류와 강한 충돌은 `INVALID`, 독립 신호 부족과 미해소 후보는
`NEEDS_MANUAL_REVIEW`, 전부 통과한 결정만 `VERIFIED`다.

### 9.2 문항 선택과 canonical registry

term gate가 검증한 canonical 대안이 한 개면 코드가 문항에 결정적으로 연결한다. 대안이
여러 개일 때만 문항 원문과 대안 목록을 problem-level task로 만들며, 모델은 SourceRecord가
아니라 canonical 대안 ID를 선택한다.

최종 자동 `ACCEPTED`는 검증된 identity member가 2건 이상이고 merge gate를 통과한 대안에만
허용한다. 단일 원천 대안은 `single_source_entities_requiring_approval.csv`로 보낸다.
`canonical_id`는 최초 승인 시 다음 형식의 UUID로 발급한다.

```text
canonical:{entity_type 소문자}:{uuid}
```

재실행에서는 기존 registry와 identity SourceRecord가 겹치는 canonical을 재사용한다. 여러
기존 canonical과 동시에 겹치거나 EntityType이 충돌하면 자동 병합하지 않고 검토 대상으로
남긴다.

## 10. 다음 구현 순서

1. 생성된 100개 term task 검수본에 사람이 정답 역할·대안 묶음을 작성한다.
2. importer로 완전성과 모순을 검사하고, 독립 재검수와 이견 조정이 끝난 행만 확정한다.
3. 골든 task를 `gpt-5.6-terra`로 실행하고 사람 정답과 역할·cluster 정확도를 비교한다.
4. 실제 평가값으로 승인 기준을 정한 뒤, 통과 시 전체 term task를 실행하고 term gate를 적용한다.
5. 생성된 복수 대안 문항 task를 `gpt-5.6-sol`로 판별하고 problem gate를 적용한다.
6. finalizer로 canonical registry와 identity import CSV를 생성한다.
7. 승인된 canonical endpoint에 한해 Anchor와 AKS·ITKC 관계를 보강한다.

term LLM 실행기, 골든셋 평가기, 모든 gate·finalizer는 구현됐다. 현재 사람 정답과 실제 LLM
decision 파일이 없으므로 `reviewed_canonical_alternatives`, 영구 `canonical_id`, 최종
`ACCEPTED` 관계는 아직 생성하지 않았다. 평가 승인 기준도 측정 전 임의 숫자로 고정하지
않는다.

## 11. 단계별 실행 파일

```powershell
# 1. term review task 생성
python etl/preprocessing/neo4j/entity_resolution/semantic_review.py `
  etl/preprocessing/neo4j/output/03_entity_resolution `
  etl/preprocessing/neo4j/output/04_llm_review

# 2. API 호출 전 실행 규모와 checkpoint 확인
python etl/preprocessing/neo4j/entity_resolution/execute_term_review.py `
  etl/preprocessing/neo4j/output/04_llm_review/internal/term_identity_review_tasks.jsonl `
  etl/preprocessing/neo4j/output/04_llm_review/internal/model_predictions `
  --dry-run

# 3. 골든셋 평가를 통과한 뒤 전체 term decision 실행
python etl/preprocessing/neo4j/entity_resolution/execute_term_review.py `
  etl/preprocessing/neo4j/output/04_llm_review/internal/term_identity_review_tasks.jsonl `
  etl/preprocessing/neo4j/output/04_llm_review/internal/model_predictions

# 4. LLM이 작성한 term decision 검증
python etl/preprocessing/neo4j/entity_resolution/semantic_review.py `
  etl/preprocessing/neo4j/output/03_entity_resolution `
  etl/preprocessing/neo4j/output/04_llm_review `
  --decisions etl/preprocessing/neo4j/output/04_llm_review/internal/model_predictions/term_identity_model_decisions.jsonl

# 5. 검증된 term 결과로 문항별 대안 선택 task 생성·검증
python etl/preprocessing/neo4j/entity_resolution/problem_review.py `
  etl/preprocessing/neo4j/output/03_entity_resolution `
  etl/preprocessing/neo4j/output/04_llm_review `
  --decisions etl/preprocessing/neo4j/output/04_llm_review/internal/model_predictions/problem_entity_model_decisions.jsonl

# 6. registry 및 Neo4j identity import CSV 생성
python etl/preprocessing/neo4j/entity_resolution/finalize_entity_resolution.py `
  etl/preprocessing/neo4j/output/03_entity_resolution `
  etl/preprocessing/neo4j/output/04_llm_review `
  etl/preprocessing/neo4j/output/05_final_identity `
  --registry etl/preprocessing/neo4j/output/05_final_identity/canonical_entity_registry.csv
```

결정 JSON Schema와 판정 prompt는 각각 `config/schemas`, `config/prompts`에 있으며 특정 역사
용어별 예외 규칙을 포함하지 않는다.

Anchor·AKS 관계 추출·ITKC 관계 보강은 위 identity gate를 통과한 canonical endpoint만
대상으로 수행한다.

## 12. 골든셋 검수본 생성 결과

`entity-resolution-gold-v1` 정책으로 term task 4,638건에서 100건을 결정적으로 뽑았다.
특정 역사 용어를 강제로 포함하는 예외 목록은 사용하지 않았다. 표본은 15개 category를
최소 3건씩 포함하고, 후보 수 구간·정확 검색 포함 여부·다원천 지지 여부·충돌 존재 여부를
복합 층으로 사용한다.

| 항목 | 결과 |
|---|---:|
| 모집단 term task | 4,638 |
| 표본 case | 100 |
| 표본 SourceRecord 후보 | 606 |
| category 수 | 15 |
| 왕조 표본 | 모집단 전부인 3건 |
| 후보 수 구간 | 1~2: 22, 3~5: 30, 6~9: 20, 10: 18, 11 이상: 10 |
| 검색 구성 | exact only: 7, exact+확장: 38, 확장 only: 55 |

표본은 운영 분포 추정을 위한 단순 무작위 표본이 아니라 오류 유형을 넓게 드러내는 진단용
균형 표본이다. 전체 처리량을 추정할 때 표본 비율을 그대로 사용하면 안 된다. 상세 계약과
실행 방법은 `11_entity_resolution_gold_set.md`를 따른다.

## 13. 전체 실행과 소량 테스트 실행 분리

운영 파이프라인의 최종 진입점은 다음 파일이다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_neo4j_preprocessing.py
```

기본 출력은 `etl/preprocessing/neo4j/output/{json,csv,review}`에 저장된다. 소량 실행은 운영
runner와 같은 `run_preprocessing_pipeline()`을 재사용하는 별도 진입점을 사용한다.

```powershell
# 경로·모델·실행량만 확인하며 LLM과 파일 출력을 실행하지 않음
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_neo4j_preprocessing_test.py `
  --dry-run

# 설정 기본값 또는 CLI에서 지정한 소량 문항 실행
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_neo4j_preprocessing_test.py `
  --limit 20 `
  --batch-size 10
```

테스트 결과는 `etl/preprocessing/neo4j/output/test_run/{json,csv,review}`에만 저장된다. 테스트
runner는 운영 `output` 루트를 직접 지정하거나 상위 경로로 이탈하는 하위 폴더명을 거부한다.
기본 테스트 실행량과 하위 폴더명은 `resolution_policy.json`의 `test_run`에서 관리한다.
`--dry-run`이 아닌 실행은 실제 용어 추출 모델을 호출할 수 있다.

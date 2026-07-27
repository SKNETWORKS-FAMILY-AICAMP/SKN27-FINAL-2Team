# Entity Resolution 골든셋 설계와 검수

> 레거시 문서: 이전 모집단·골든셋 상태 기준 문서다. 현재 운영 기준으로 사용하지 않는다.
>
> 상태: `ANNOTATION-READY`
> 생성 정책: `entity-resolution-gold-pilot-v2`
> 입력 정책: `entity-resolution-candidate-v2.4`
> 기준일: 2026-07-21

## 1. 현재 결과의 의미

현재 생성된 `goldset/human_review_csv`의 case·candidate CSV는 사람이 정답을 입력할
**골든셋 검수본**이다. 모델 또는 기존 규칙의 출력을 정답으로 복사하지 않았다. 검수자가 후보
역할과 동일 실체 묶음을 작성하고, 독립 재검수와
이견 조정이 끝나야 실제 골든셋이 된다.

골든셋의 기본 단위는 용어 한 행이 아니라 다음 두 수준이다.

1. case 수준: 이 용어의 최종 link status와 문항별 추가 판정 필요 여부
2. candidate 수준: 각 SourceRecord가 정답 실체인지, 근거 전용인지, 오탐인지, 미확정인지

이 구조를 써야 `고종`처럼 하나의 표기가 여러 CanonicalEntity를 가리키는 경우를 보존할 수
있다. 후보 다섯 개 중 하나만 고르는 방식은 사용하지 않는다.

## 2. 표본 추출 정책

입력은 `term_identity_review_tasks.jsonl`의 `AMBIGUOUS` task 4,638건이다. 특정 용어명이나
source ID를 코드에 넣지 않고 다음 특성으로 결정적 층화 표본을 만든다.

| 층화 특성 | 값 |
|---|---|
| category | 추출 15분류 |
| 후보 수 | 1~2 / 3~5 / 6~9 / 10 / 11 이상 |
| 검색 구성 | `EXACT_ONLY` / `EXACT_AND_EXPANDED` / `EXPANDED_ONLY` |
| 다원천 지지 | `MULTI_SOURCE_SUPPORTED` 존재 여부 |
| 강한 충돌 후보 쌍 | 존재 여부 |

먼저 각 category에서 최대 1건을 서로 다른 복합 층에서 순환 선택한다. 남은 수량은 전체
복합 층에서 한 건씩 순환해 채운다. 층 내부 정렬은 설정의 seed와 task ID를 SHA-256으로
해시한 값이므로 입력 행 순서가 바뀌어도 같은 task가 선택된다.

파일럿에서는 사람 검수 부담을 제한하기 위해 후보 10개 이하 case를 우선 선택한다. 특정
category에 해당 case가 없거나 전체 표본 수가 부족할 때만 후보 수가 가장 적은 case부터
보충한다. 이 제한은 `review_goldset.json`에서 관리하며 용어명을 하드코딩하지 않는다.

이 표본은 어려운 검색 유형과 작은 category도 관찰하기 위한 진단용 표본이다. 모집단
비율과 같지 않으므로 다음 두 평가를 분리한다.

- macro 평가: category·후보 수 구간·검색 구성별 동일 가중치
- weighted 평가: 모집단 분포 가중치 적용

## 3. 생성된 표본

| 항목 | 값 |
|---|---:|
| 모집단 case | 실행 시점의 최신 term review task 수 |
| 표본 case | 20 |
| 표본 candidate | 선택된 20개 case의 정제된 후보 합계 |
| category | 모집단에 존재하는 category를 우선 1건씩 포함 |

실제 category·후보 수·검색 구성 분포는 재생성된
`internal/source/gold_sample_distribution.csv`와 manifest를 기준으로 확인한다.
이전 100건 스냅샷의 수치를 새 파일럿에 재사용하지 않는다.

## 4. 검수 컬럼 계약

### 4.1 `human_review_cases.csv`

| 입력 컬럼 | 허용값·의미 |
|---|---|
| `gold_link_status` | `ACCEPTED / AMBIGUOUS / UNRESOLVED / REJECTED` |
| `requires_problem_review` | 동일 용어의 문항별 실체가 달라질 수 있으면 `YES`, 아니면 `NO` |
| `gold_decision_reason` | 최종 상태와 문항별 판정 여부의 근거 |
| `reviewer` | 검수자 식별자 |
| `case_review_status` | `NOT_STARTED / IN_PROGRESS / COMPLETE / NEEDS_DISCUSSION` |

### 4.2 `human_review_candidates.csv`

| 입력 컬럼 | 허용값·의미 |
|---|---|
| `gold_candidate_role` | `IDENTITY_MEMBER / EVIDENCE_ONLY / REJECTED / AMBIGUOUS` |
| `gold_alternative_key` | 같은 실체 후보끼리 같은 case-local 키 사용. 예: `ALT_001` |
| `gold_display_name` | 동음이의어를 구분하는 표시명 |
| `gold_entity_type` | 목표 스키마의 9개 CanonicalEntity 보조 label |
| `gold_related_entity_key` | `EVIDENCE_ONLY` 문서의 주 대상인 별도 관련 엔티티를 묶는 case-local 키. 예: `REL_001` |
| `gold_related_display_name` | 관련 엔티티의 표시명 |
| `gold_related_entity_type` | 관련 엔티티의 9개 CanonicalEntity 보조 label |
| `gold_reason` | 후보별 추가 설명이 필요할 때만 입력. 빈칸이면 case 공통 근거 사용 |

`gold_alternative_key`, `gold_display_name`, `gold_entity_type`은 `IDENTITY_MEMBER`에만 쓴다.
같은 대안 그룹에서 표시명과 타입은 한 행에만 적어도 된다. 한 case에 정답 실체가 여러
개면 `ALT_001`, `ALT_002`를 모두 유지한다. case를 `COMPLETE`로 확정할 때 역할이 빈
후보는 자동 `REJECTED`가 된다.

`gold_related_entity_*`는 `EVIDENCE_ONLY`에만 사용한다. importer는 같은 관련 엔티티 키의
근거 원천을 묶어 `proposed_related_entities`로 보존한다. 이 값은 관계 대상 후보이며,
실제 관계 술어와 `VERIFIED` 여부는 이후 원문 관계 추출·검증 단계에서 결정한다.

최종 import는 `related_entity_resolution_tasks.jsonl`도 생성한다. 각 task는 원래 용어의
resolution case, 관련 엔티티의 case-local 키, 사람이 확인한 seed SourceRecord를 보존한다.
같은 표시명만으로 서로 다른 원래 case의 관련 엔티티를 자동 병합하지 않는다.

## 5. 블라인드 검수와 승인 절차

`internal/source/rule_based_baseline.csv`에는 기존 규칙이 제안한 역할과 대안 묶음이
별도로 들어 있다. 1차 검수자는 이 파일을 보지 않고 원천 문맥만으로 판정한다. 기존 코드
결과를 사람 작성 CSV에 함께 두면 제안값에 끌려가므로 분리했다.

권장 승인 순서는 다음과 같다.

1. 1차 검수자가 20개 case의 후보를 읽고 정답·근거·애매 후보만 표시한다.
2. 역할이 빈 검색 오탐은 case 완료 시 자동 `REJECTED`가 된다.
3. 각 case의 identity member가 하나 이상의 대안으로 완전하게 묶였는지 확인한다.
4. 다른 검수자가 전체 또는 합의한 재검수 표본을 독립 판정한다.
5. 역할·대안 묶음·link status가 다른 case를 `NEEDS_DISCUSSION`으로 모은다.
6. 이견 조정이 끝난 case만 `COMPLETE`로 바꾼다.

## 6. 모델 평가 지표

단순히 “후보 하나를 맞혔는가”만 측정하면 잘못된 병합을 감지할 수 없다. 최소 평가값은
다음과 같다.

| 수준 | 지표 |
|---|---|
| candidate role | 역할별 precision / recall / F1, macro F1 |
| identity cluster | 후보 쌍 기준 pairwise precision / recall / F1 |
| case | link status accuracy, problem review flag accuracy |
| 안전성 | 강한 충돌 후보의 오병합 수, 동음이의어 오병합 수 |
| coverage | category·후보 수 구간·검색 구성별 정답률 |

잘못된 병합 비용이 더 크므로 identity cluster precision과 강한 충돌 오병합 건수를 우선
gate로 사용한다. 모델 출력은 계속 `PROPOSED`이며 골든셋과 일치해도 기존 검증 gate를
통과하기 전에는 `VERIFIED` 또는 `ACCEPTED`가 아니다.

## 7. 실행 방법

`.venv`를 활성화한 프로젝트 루트에서 다음 파일을 실행한다.

```powershell
python etl/preprocessing/neo4j/goldset/build_gold_set.py `
  etl/preprocessing/neo4j/output/internal/model_review/term_identity_review_tasks.jsonl `
  etl/preprocessing/neo4j/goldset/internal/source
```

설정은 `config/resolution_policy.json`의
`entity_resolution.semantic_review.gold_set`에서 관리한다. 표본 수, category 최소 수량,
후보 수 구간, seed, 판정 어휘와 출력 파일명을 코드 밖에서 바꿀 수 있다.

출력은 다음과 같다.

| 파일 | 역할 |
|---|---|
| `gold_review_tasks.jsonl` | 선택된 원문 task 전체 |
| `gold_case_labels_template.csv` | case-level 정답 입력 구조 참고 |
| `gold_candidate_labels_template.csv` | candidate-level 정답 입력 구조 참고 |
| `rule_based_baseline.csv` | 기존 코드 제안값 비교용 |
| `gold_sample_distribution.csv` | 모집단과 표본 분포 비교 |
| `gold_sample_manifest.json` | 입력 파일 해시와 정책 버전 감사 |

`input_task_sha256`가 달라지면 같은 seed라도 입력 snapshot이 달라진 것이다. 생성기는
사람 입력이나 시작 상태가 있는 검수 CSV를 기본적으로 덮어쓰지 않는다. 정말 교체할 때만
`--force-overwrite-review`를 명시한다.

## 8. 검수 CSV importer

검수 완료 후 전체 골든셋 흐름은 다음 한 명령으로 실행한다. 이 명령은 검수 CSV import,
골든셋 모델 판정·평가, `EVIDENCE_ONLY` 관련 엔티티의 2차 원천 검색과 모델 판정·검증을
순서대로 수행한다.

```powershell
# 먼저 API 호출 없이 검증
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_neo4j_preprocessing.py `
  --goldset `
  --dry-run

# 검증 통과 후 전체 실행
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_neo4j_preprocessing.py `
  --goldset
```

검증 오류가 한 건이라도 있으면 API 호출 전에 종료한다. 각 단계의 기존 실행 파일은 삭제하지
않으며, 장애 확인이나 특정 단계 재실행 용도로만 사용한다.

검수 중 진행 상태와 오류를 확인할 때는 `--allow-partial`을 사용한다. 이 모드도 검증을
생략하지 않으며, 오류가 없는 완료 case만 decision으로 내보낸다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/import_gold_set.py `
  etl/preprocessing/neo4j/goldset/human_review_csv `
  etl/preprocessing/neo4j/goldset/internal/source/gold_review_tasks.jsonl `
  etl/preprocessing/neo4j/goldset/internal/evaluation `
  --allow-partial
```

최종 확정 때는 `--allow-partial`을 빼고 실행한다. 미완료 또는 오류가 한 건이라도 있으면
검증 파일을 저장한 뒤 종료 코드 1을 반환한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/import_gold_set.py `
  etl/preprocessing/neo4j/goldset/human_review_csv `
  etl/preprocessing/neo4j/goldset/internal/source/gold_review_tasks.jsonl `
  etl/preprocessing/neo4j/goldset/internal/evaluation
```

importer는 다음을 검사한다.

- 두 CSV의 case·candidate ID가 원본 gold task snapshot과 정확히 일치하는지
- 수정 금지 필드인 task ID, resolution case ID, SourceRecord ID, category가 바뀌지 않았는지
- case가 `COMPLETE`인지. 미완료 case의 빈 후보는 오류로 반복하지 않음
- 완료 case에서 역할이 빈 candidate를 `REJECTED`로 변환했는지
- 역할·link status·entity type이 고정 어휘에 속하는지
- identity member의 대안 키·표시명·entity type이 완전하고 같은 대안에서 일치하는지
- 비 identity 후보에 대안 필드가 잘못 입력되지 않았는지
- 복수 대안 case가 문항별 검토 대상으로 표시됐는지
- `ACCEPTED`, `AMBIGUOUS`, `UNRESOLVED`, `REJECTED`와 후보 역할이 모순되지 않는지

출력은 gold decision JSONL, case outcome CSV, validation error CSV, 입력 CSV·task 해시와
정책 버전을 담은 manifest다. gold decision의 `decision_status=PROPOSED`는 기존 decision JSON
구조와의 호환을 위한 값이다. `review_model=human_gold_adjudication`과 전용 prompt version으로
사람 정답임을 구분하며 production LLM gate 입력으로 사용하지 않는다.

현재 미작성 검수본을 partial 검증하면 case당 `CASE_REVIEW_NOT_COMPLETE` 한 건만 기록한다.
후보 행마다 미완료 오류를 반복해서 만들지 않는다.

## 9. 골든 task 모델 실행과 평가

먼저 dry-run으로 선택 건수와 기존 checkpoint 재사용 수를 확인한다. 이 명령은 OpenAI
client를 만들지 않으며 API를 호출하지 않는다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/execute_term_review.py `
  etl/preprocessing/neo4j/goldset/internal/source/gold_review_tasks.jsonl `
  etl/preprocessing/neo4j/goldset/internal/model `
  --dry-run
```

dry-run의 선택 건수는 현재 파일럿 표본 20건을 기준으로 한다. 실제 소량 호출은
같은 명령에서 `--dry-run`을 빼고 `--limit 5`처럼 범위를 제한한다. 성공한 task는 즉시
checkpoint JSONL에 기록되므로 같은 모델·prompt·정책 버전으로 재실행할 때 다시 호출하지
않는다. 실패 task만 정책의 재시도 횟수만큼 다시 시도하며 성공한 응답을 누락하지 않는다.

사람 검수본의 최종 import가 끝나고 모델 decision이 생성되면 다음 평가기를 실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/evaluate_term_review.py `
  etl/preprocessing/neo4j/goldset/internal/evaluation/human_gold_decisions.jsonl `
  etl/preprocessing/neo4j/goldset/internal/model/term_identity_model_decisions.jsonl `
  etl/preprocessing/neo4j/goldset/internal/source/gold_review_tasks.jsonl `
  etl/preprocessing/neo4j/goldset/internal/evaluation/human_gold_case_outcomes.csv `
  etl/preprocessing/neo4j/goldset/internal/evaluation
```

평가기 출력은 전체 지표 JSON과 case·candidate role·층별 지표 CSV, 평가 오류 CSV다. 역할
정확도와 macro F1 외에 identity 후보 쌍의 precision·recall·F1, 오병합 쌍 수, 오분리 쌍 수,
link status와 문항별 재판정 여부 정확도를 별도로 계산한다. LLM 원본 `PROPOSED` 지표는
`proposal_*`, 검증 게이트 통과 결과는 `verified_*`로 구분한다. 게이트가 막은 오병합은
`blocked_proposal_false_merge_pair_count`에 기록한다. case CSV에는 검증 상태와 오류 코드,
게이트 통과 후 pair 결과가 함께 들어간다. 게이트 보류로 아직 확정되지 않은 정답 pair는
오분리가 아니라 `deferred_gold_identity_pair_count`로 집계한다. macro F1은 실제 gold support가 있는 역할만
평균한다. category·후보 수·검색 구성·다원천 지지·충돌 여부별 결과도 함께 저장한다.

`proposal_identity_pair_recall`은 모델 원본 제안이 gold identity pair를 빠뜨리지 않은
비율이다. gold pair를 모두 제안하면서 오병합 pair를 추가해도 recall은 1.0이 될 수 있으므로
proposal precision과 false merge를 함께 확인한다.

`verified_identity_pair_recall`은 `VERIFIED` case 내부의 조건부 recall이다. 게이트가 보류한
gold pair는 FN이 아니라 `deferred_gold_identity_pair_count`로 분리되므로 전체 자동 병합
coverage가 아니다. 전체 자동 승인 pair recall은 다음 식으로 해석한다.

```text
verified TP / (verified TP + verified FN + deferred gold pair)
```

평가 JSON은 이 값을 `auto_accepted_identity_pair_recall`로 직접 기록한다.
자동 승인 안전성은 `auto_accepted_identity_pair_precision`, 보류 비율은
`deferred_gold_identity_pair_rate`로 확인한다. 기존 `verified_identity_pair_*`는
호환성을 위해 유지하고, 같은 조건부 의미를 `conditional_verified_identity_pair_*`로도
기록한다.

identity pair gate는 정책으로 두 방식을 지원한다.

| 방식 | 의미 |
|---|---|
| `complete_graph` | 대안 안의 모든 pair가 직접 `merge_eligible=true`여야 함 |
| `connected_graph` | 강한 충돌 없이 양성 edge로 모든 멤버가 연결되면 통과 |

현재 활성 방식은 `connected_graph`다. 강한 pair 충돌과 pair 행 누락은 두 방식 모두
`INVALID`이며, 연결 규칙은 category 충돌·term alignment·EntityType 검토를 완화하지 않는다.

기출문제 용어는 상류에서 전달받은 입력이므로 이 단계에서 OCR을 수행하거나 원문을 덮어쓰지
않는다. 모델이 입력 표기를 다른 역사 용어로 교정해 제안한 경우에도 입력 용어와 최소 한 개
identity member 원천명 사이에 강한 이름 정합성이 없으면
`TERM_SOURCE_ALIGNMENT_REVIEW_REQUIRED`로 보류한다. 정규화 exact, 양방향 포함, 공백 제거 후
문자 재배열, 순서가 유지된 수식어 삽입은 자동 검증 대상으로 인정하되 단순 유사 철자만으로는
`VERIFIED`하지 않는다.

승인 임계값은 현재 설정하지 않았다. 사람 정답과 첫 모델 결과를 보기 전에 임의 수치를
하드코딩하지 않고, 특히 identity pair precision과 오병합 사례를 확인한 뒤 정책 파일에
합의한 gate를 추가한다. 모델 결과는 이 평가와 무관하게 항상 `PROPOSED`이고 production
verification gate 통과 전에는 `VERIFIED` 또는 `ACCEPTED`가 아니다.

## 10. 관련 엔티티 2차 Entity Resolution

골든셋 import가 성공한 뒤 다음 명령을 실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_related_entity_resolution.py
```

이 runner는 `related_entity_resolution_tasks.jsonl`을 읽어 관련 표시명을 AKS·시소러스·
ITKC에서 재검색한다. 사람이 지정한 EVIDENCE_ONLY SourceRecord는 seed 후보로 반드시
유지하며, 결과를 `goldset/internal/related_entity` 아래의 표준 ER staging CSV와
`related_entity_review_tasks.jsonl`로 저장한다. 이후 review task는 기존
`execute_term_review.py`로 판정할 수 있다.

## 11. Neo4j 적재와의 연결

골든셋은 Anchor나 역사 관계를 직접 채우는 파일이 아니다. `IDENTITY_MEMBER` 대안이 검증
gate를 통과하면 다음 identity 구조를 만들 수 있게 하는 평가·승인 기준이다.

```text
SourceRecord-[:RESOLVES_TO]->CanonicalEntity
EntityName-[:REFERS_TO]->CanonicalEntity
```

Anchor, AKS 본문 관계, ITKC 관계 근거는 이 identity endpoint가 확정된 뒤 별도 추출한다.
따라서 term-level 골든셋만으로 목표 Graph 전체를 채운다고 해석하면 안 된다. 이 단계의
책임은 잘못된 원천 병합을 막고 안정적인 canonical endpoint를 확정하는 것이다.

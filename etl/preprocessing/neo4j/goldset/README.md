# Entity Resolution 골든셋 안내

## 실행 순서

사람 검수 CSV 저장이 끝나면 아래 세 단계를 순서대로 실행한다. 각 단계는 먼저
`--dry-run`으로 입력과 예정 건수를 확인한 뒤 실제 실행한다.

```powershell
# 1. HUMAN_REVIEW import → 골든셋 모델 실행/재사용 → 평가
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/runners/import_and_evaluate_goldset.py `
  --dry-run
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/runners/import_and_evaluate_goldset.py

# 2. EVIDENCE_ONLY 관련 엔티티 재검색 → LLM 판정 → 검증 게이트
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/runners/run_related_entity_resolution.py `
  --dry-run
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/runners/run_related_entity_resolution.py

# 3. VERIFIED 관련 엔티티만 최종 identity CSV로 승격
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/runners/finalize_related_entities.py `
  --dry-run
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/runners/finalize_related_entities.py
```

검증 오류가 있으면 LLM API를 호출하기 전에 중단한다. 성공한 모델 응답은 checkpoint에
저장되므로 같은 정책·모델로 재실행해도 완료 건을 다시 호출하지 않는다. 1단계가 만든
관련 엔티티 queue를 2단계가 사용하고, 2단계의 검증 결과를 3단계가 사용한다.

## 표본 생성·확장

현재 term task에서 활성 검수본을 정책 목표인 100개까지 안전하게 확장할 때는 인자 없이
실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/goldset/build_gold_set.py
```

이 명령은 기존 20개 회귀 case와 사람이 입력한 모든 셀을 보존한다. 기존
`term_review_task_id`를 현재 모집단에서 제외하고 부족한 수만 `NOT_STARTED`로 추가하며,
이미 100개이면 파일을 수정하지 않는다.

| 이 명령의 출력 | 의미 |
|---|---|
| `human_review_csv/human_review_cases.csv` | 사람이 검수할 case. 기존 행 뒤에 신규 행이 추가된다. |
| `human_review_csv/human_review_candidates.csv` | 각 case의 원천 후보와 사람 판정 입력란 |
| `internal/source/gold_review_tasks.jsonl` | 기존 회귀 task snapshot과 신규 task snapshot |
| `internal/source/gold_*_template.csv` | 사람 입력을 제외한 재생성용 빈 양식 |
| `internal/source/rule_based_baseline.csv` | 코드 제안 baseline. 블라인드 검수 전에는 보지 않는다. |
| `internal/source/gold_sample_distribution.csv` | 현재 모집단과 100개 표본의 층별 분포 |
| `internal/source/gold_sample_manifest.json` | 입력 hash, 보존·추가 건수, 생성 정책과 경로 |

활성 검수본을 건드리지 않고 새 100개 표본만 비교하려면 입력과 별도 출력 폴더를 함께
명시한다. 이 모드는 지정 폴더에 snapshot과 빈 양식만 만든다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/goldset/build_gold_set.py `
  etl/preprocessing/neo4j/output/internal/model_review/term_identity_review_tasks.jsonl `
  etl/preprocessing/neo4j/goldset/internal/source_v2
```

`--force-overwrite-review`는 활성 검수본 전체를 새 표본으로 교체할 때만 사용하는
명시적 초기화 옵션이다. 일반적인 표본 확대에는 사용하지 않는다.

사람이 실제로 작성할 파일은 `human_review_csv` 안에 있다. 최초 골든셋 검수는 두 CSV로
진행한다. 사람 gold와 모델의 역할 충돌은 별도 검수 CSV로 자동 생성된다.

| 위치 | 의미 |
|---|---|
| `human_review_csv/human_review_cases.csv` | 용어 case 100건의 최종 상태·공통 근거·검수자 입력. 실제 진행 상태는 `case_review_status`로 확인 |
| `human_review_csv/human_review_candidates.csv` | 정답·근거·애매 후보의 역할, 동일 실체 묶음, `EVIDENCE_ONLY`의 별도 관련 엔티티 입력. 빈 역할은 case 완료 시 `REJECTED` |
| `human_review_csv/role_conflict_manual_review.csv` | 사람 gold와 모델의 `EVIDENCE_ONLY`·`REJECTED` 상호 오분류 재검토 큐. 평가 뒤 생성 |
| `final_identity` | seed 원천이 속한 검증 대안만 승격한 관련 엔티티 canonical registry와 Neo4j identity CSV |
| `internal/source` | 표본 task, 빈 CSV 양식, 코드 baseline, 표본 생성 기록 |
| `internal/model` | 골든 case 모델 판정과 checkpoint |
| `internal/evaluation` | CSV 검증·사람 gold decision·LLM 제안과 검증 게이트 통과 결과의 정확도·오병합 평가 |
| `internal/related_entity` | `EVIDENCE_ONLY` 관련 엔티티의 재검색·ER·모델 판정·검증 결과 |

`internal`은 직접 작성하지 않는 파이프라인 산출물이다. 특히 `internal/source`는
원본 증거이므로 수정하지 않는다. 1차 사람 검수가 끝나기 전에는
`rule_based_baseline.csv`를 보지 않는다. 검수 중 확인은 다음 명령으로 수행한다.

`internal/evaluation/model_vs_gold_metrics.json`은 LLM 원본 제안 지표와 검증 게이트 통과
후 지표를 함께 기록한다. `proposal_false_merge_pair_count`는 LLM이 제안한 오병합이고,
`verified_false_merge_pair_count`는 게이트까지 통과한 실제 위험 오병합이다.
역할 평가는 원래 `candidate_role_macro_f1`을 유지하면서, 표본 수를 반영한
`candidate_role_weighted_f1`, 정책상 희소 역할을 제외한
`candidate_role_macro_f1_without_excluded_roles`, 역할별 표본 수인
`candidate_role_support_counts`를 함께 기록한다. 보조지표는 원래 macro F1을 대체하지 않는다.
`model_vs_gold_case_results.csv`의 `gate_verification_status`,
`gate_error_codes_json`, `accepted_pair_false_positive`,
`blocked_proposal_false_merge_count`에서 case별 차단 결과를 확인한다.
게이트가 보류해 아직 확정하지 않은 정답 pair는 오분리로 세지 않고
`deferred_gold_identity_pair_count`로 별도 집계한다.

`proposal_identity_pair_recall`은 원본 모델 제안이 gold pair를 누락하지 않았는지
측정한다. 정답 pair를 모두 포함하면서 잘못된 pair도 추가하면 recall은 1.0이고 precision은
낮아질 수 있으므로 두 값을 함께 본다.

`verified_identity_pair_recall`은 `VERIFIED` case 내부의 조건부 recall이다. 게이트가
보류한 gold pair는 FN이 아니라 `deferred_gold_identity_pair_count`로 빠지므로 이 값을
전체 자동 병합 recall로 해석하지 않는다. 전체 자동 승인 coverage는 다음처럼 계산한다.

```text
verified TP / (verified TP + verified FN + deferred gold pair)
```

현재 v6 결과는 `42 / 63 = 0.666667`이다. verified precision 1.0은 자동 승인된
pair의 안전성을 뜻하고, 66.7%는 전체 gold pair 중 자동 승인된 범위를 뜻한다.

이 계산은 `model_vs_gold_metrics.json`의
`auto_accepted_identity_pair_precision`, `auto_accepted_identity_pair_recall`,
`auto_accepted_identity_pair_f1`, `deferred_gold_identity_pair_rate`에 직접 기록된다.
`conditional_verified_identity_pair_*`는 기존 `verified_identity_pair_*`가 조건부
지표임을 명확히 하는 호환 필드다. 보류 오류는 같은 JSON의
`deferred_gold_pair_error_case_counts`와 `deferred_gold_pair_error_pair_counts`에서
case 수와 영향받은 pair 수를 각각 확인한다.

identity pair evidence gate의 활성 방식은 `connected_graph`다. 강한 충돌이 없는
`merge_eligible=true` edge로 identity 멤버 전체가 연결되면 pair evidence를 통과한다.
모든 pair 직접 근거를 요구하는 기존 방식은 `complete_graph`이며 정책값으로 회귀할 수
있다. v6부터 pair 승인은 case 최종 승인과 분리한다. 안전한 pair는 독립적으로
`model_identity_pair_gate_results.csv`에 기록하지만, case가 보류되면 canonical entity와
EntityType 확정은 계속 차단한다.
metrics JSON의 `identity_pair_gate_policy_version`과
`identity_pair_gate_evidence_mode`가 실제 평가에 적용된 게이트 정책을 기록한다.

`role_conflict_manual_review.csv`는 현재 모델과 gold가 `EVIDENCE_ONLY`·`REJECTED`를 반대로
고른 후보만 모은다. 검수자는 target 문맥과 후보 원천의 주 대상을 비교해
`reviewed_role`, `review_status`, `manual_reason`, `reviewer`를 작성한다. 이 파일은 gold를
자동 변경하지 않는다. gold가 잘못된 것으로 결론 나면 같은 후보를
`human_review_candidates.csv`에서 수정한 뒤 다시 import한다.

상류에서 전달받은 기출문제의 용어 표기는 원문 그대로 보존한다. 모델이 유사한 정답 표기로
교정해 제안하더라도, 입력 용어와 identity member 원천명 사이에 정규화 exact·포함 관계·
문자 재배열·순서 보존 확장 중 하나가 없으면 `TERM_SOURCE_ALIGNMENT_REVIEW_REQUIRED`로
자동 승격을 막는다.
이는 OCR을 이 파이프라인에서 다시 수행하는 규칙이 아니라 상류 입력 표기 오류나 비직접
이칭을 사람이 확인하게 하는 검증 게이트다.

후보 CSV에서는 모든 행을 채우지 않는다. `IDENTITY_MEMBER`, `EVIDENCE_ONLY`, `AMBIGUOUS`
후보만 표시하고 검색 오탐은 빈칸으로 둔다. 한 case의 후보를 모두 읽은 다음 case CSV의
`case_review_status`를 `COMPLETE`로 바꾸면 빈 후보가 일괄 `REJECTED`로 확정된다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/import_gold_set.py `
  etl/preprocessing/neo4j/goldset/human_review_csv `
  etl/preprocessing/neo4j/goldset/internal/source/gold_review_tasks.jsonl `
  etl/preprocessing/neo4j/goldset/internal/evaluation `
  --allow-partial
```

완료된 `EVIDENCE_ONLY` 관련 엔티티는 import 시
`internal/evaluation/related_entity_resolution_tasks.jsonl`에 별도 queue로 생성된다. 이어서
다음 명령으로 관련 표시명을 AKS·시소러스·ITKC에서 다시 검색하고 기존 ER review task로
변환한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/runners/run_related_entity_resolution.py
```

기본 출력은 `goldset/internal/related_entity`이다. 같은 표시명의 관련 엔티티가
여러 원래 case에서 나와도 이름만으로 자동 병합하지 않고 각각 독립 case로 유지한다.

관련 엔티티는 자동 검증 게이트에서 `VERIFIED`된 대안만
`runners/finalize_related_entities.py` 실행에서 `final_identity`로 승격한다.
`NEEDS_MANUAL_REVIEW`와 `INVALID`는 승격하지 않는다.
동명이인 대안이 여러 개여도 사람이 관련 엔티티로 지정했던 seed SourceRecord가 속한 대안만
선택한다. 선택 결과는 `related_entity_canonical_selections.csv`에서 확인한다.

다중 원천과 병합 게이트를 통과한 대안은 `canonical_entity_registry.csv` 및 `neo4j_*` CSV로
승격한다. 단일 원천은 일반적으로 자동 승인하지 않지만, 사람이 골든셋에서 관련 엔티티
seed로 직접 지정한 그 SourceRecord가 유일한 검증 대안에 속하면
`verified_related_entity_seed` 근거로 승격한다. 그 외 단일 원천은
`single_source_entities_requiring_approval.csv`에 남는다. 관련 엔티티는 기출문항에 직접
배정된 용어가 아니므로 `exam_problem_entity_assignments_final.csv`가 빈 것은 정상이다.

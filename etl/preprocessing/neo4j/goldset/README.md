# Entity Resolution 골든셋 안내

## 한 번에 실행

사람 검수 CSV 저장이 끝났다면 최종 진입점 하나만 실행한다.

```powershell
# API 호출과 파일 생성 없이 검수 상태·예정 건수 확인
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_neo4j_preprocessing.py `
  --goldset `
  --dry-run

# 검수본 import → 골든셋 모델 평가 → 관련 엔티티 재검색·모델 판정
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_neo4j_preprocessing.py `
  --goldset
```

검증 오류가 있으면 LLM API를 호출하기 전에 중단한다. 성공한 모델 응답은 checkpoint에
저장되므로 같은 정책·모델로 재실행해도 완료 건을 다시 호출하지 않는다. 아래 개별 명령은
특정 단계만 다시 확인할 때 사용한다.

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
진행하고, 관련 엔티티 자동 게이트가 보류한 건이 있으면 세 번째 CSV가 자동 생성된다.

| 위치 | 의미 |
|---|---|
| `human_review_csv/human_review_cases.csv` | 용어 case 100건의 최종 상태·공통 근거·검수자 입력. `1`~`20`은 완료, `21`~`100`은 신규 검수 대상 |
| `human_review_csv/human_review_candidates.csv` | 정답·근거·애매 후보의 역할, 동일 실체 묶음, `EVIDENCE_ONLY`의 별도 관련 엔티티 입력. 빈 역할은 case 완료 시 `REJECTED` |
| `human_review_csv/related_entity_manual_review.csv` | 관련 엔티티 모델 판정 중 자동 게이트가 보류한 case 승인·수정. 최초 `--goldset` 실행 뒤 생성 |
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
`model_vs_gold_case_results.csv`의 `gate_verification_status`,
`gate_error_codes_json`, `accepted_pair_false_positive`,
`blocked_proposal_false_merge_count`에서 case별 차단 결과를 확인한다.
게이트가 보류해 아직 확정하지 않은 정답 pair는 오분리로 세지 않고
`deferred_gold_identity_pair_count`로 별도 집계한다.

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
  etl/preprocessing/neo4j/run_related_entity_resolution.py
```

기본 출력은 `goldset/internal/related_entity`이다. 같은 표시명의 관련 엔티티가
여러 원래 case에서 나와도 이름만으로 자동 병합하지 않고 각각 독립 case로 유지한다.

관련 엔티티 결과가 `NEEDS_MANUAL_REVIEW`이면
`human_review_csv/related_entity_manual_review.csv`에 행이 생성된다. 모델 제안이 맞으면
`manual_status=VERIFIED`, `manual_reason`, `reviewer`만 작성하고 다시 `--goldset`을 실행한다.
완료된 사람 판정은 `verification_method=HUMAN_REVIEW`와 검수자·시각을 함께 기록한다.

모든 관련 엔티티 판정이 끝나면 같은 `--goldset` 실행에서 `final_identity`도 생성한다.
동명이인 대안이 여러 개여도 사람이 관련 엔티티로 지정했던 seed SourceRecord가 속한 대안만
선택한다. 선택 결과는 `related_entity_canonical_selections.csv`에서 확인한다.

다중 원천과 병합 게이트를 통과한 대안은 `canonical_entity_registry.csv` 및 `neo4j_*` CSV로
승격한다. 단일 원천은 일반적으로 자동 승인하지 않지만, 사람이 골든셋에서 관련 엔티티
seed로 직접 지정한 그 SourceRecord가 유일한 검증 대안에 속하면
`verified_related_entity_seed` 근거로 승격한다. 그 외 단일 원천은
`single_source_entities_requiring_approval.csv`에 남는다. 관련 엔티티는 기출문항에 직접
배정된 용어가 아니므로 `exam_problem_entity_assignments_final.csv`가 빈 것은 정상이다.

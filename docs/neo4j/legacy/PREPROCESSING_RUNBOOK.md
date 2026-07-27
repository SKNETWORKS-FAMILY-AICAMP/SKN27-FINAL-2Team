# Neo4j 전처리 실행·산출물 안내서

이 문서는 `etl/preprocessing/neo4j` 파이프라인을 **어떤 순서로 실행하고, 각 단계에서
무슨 파일이 어디에 생기며, 그 파일을 어떻게 판정해야 하는지** 설명한다.

명령은 프로젝트 루트인 `C:\dev\project\SKN27-FINAL-2Team`에서 Windows PowerShell로
실행하는 것을 기준으로 한다.

## 1. 먼저 알아야 할 결론

전체 흐름은 다음과 같다.

```text
소량 테스트
  -> 7단계 전처리 결과 확인
  -> 골든셋 평가 결과 확인 및 전체 term LLM 실행 승인
  -> 운영 1,600문항 전처리
  -> term-level LLM 판정 및 검증 gate
  -> problem-level 판정 및 검증 gate
  -> canonical registry·Neo4j import CSV 생성
  -> 별도 Neo4j 적재 단계
```

현재 저장소에서 자동 실행이 연결된 범위와 연결되지 않은 범위는 구분해야 한다.

- `run_preprocessing_test.py`: 소량 전처리 1~7단계를 실행한다.
- `run_neo4j_preprocessing.py`: 운영 전처리 1~7단계를 실행한다.
- `execute_term_review.py`: term-level task를 LLM으로 판정한다.
- `semantic_review.py`: term-level LLM 판정을 검증한다.
- `problem_review.py`: problem-level task를 생성하고, 이미 존재하는 decision을 검증한다.
- `execute_problem_review.py`: problem-level task를 LLM으로 판정한다.
- `finalize_entity_resolution.py`: 최종 canonical registry와 Neo4j import CSV를 만든다.
- `load_final_identity.py`: 승인된 최종 identity CSV를 Neo4j에 upsert한다.
- `run_full_neo4j_pipeline.py`: 위 단계를 안전 게이트와 함께 순서대로 실행한다.

전처리만 끝냈다고 Neo4j 적재까지 완료된 것은 아니다. 통합 runner도 기본 동작은
dry-run이며, 실제 실행에는 `--execute`, DB 적재에는 `--load-neo4j`가 추가로 필요하다.

## 2. 폴더를 어디부터 보면 되는가

운영 결과와 테스트 결과는 분리된다.

```text
etl/preprocessing/neo4j/
├─ output/
│  ├─ review/                  # 운영 실행 후 사람이 먼저 확인할 파일
│  ├─ internal/                # 운영 중간 산출물·checkpoint·모델 task
│  ├─ final_identity/          # 검증 완료 뒤 만드는 최종 identity CSV
│  └─ test_run/                # 소량 테스트 전용 결과
│     ├─ review/
│     ├─ internal/
│     └─ final_identity/
├─ goldset/                    # 사람 정답·모델 평가·관련 엔티티 검증
├─ config/                     # 모델·정책·prompt·JSON Schema
├─ entity_resolution/          # ER 검토·검증·확정 실행 파일
├─ terms/                      # 문항 텍스트·용어·원천 후보 처리
├─ run_preprocessing_test.py
└─ run_neo4j_preprocessing.py
```

확인 우선순위는 다음과 같다.

1. 소량 테스트는 `output/test_run/review`를 먼저 본다.
2. 상세 원인 추적은 `output/test_run/internal`을 본다.
3. 운영 실행은 `output/review`를 먼저 본다.
4. 모델 검토 과정은 `output/internal/model_review`를 본다.
5. 최종 확정 결과는 `output/final_identity`를 본다.

`final_identity` 폴더는 전처리 실행 중 미리 생성될 수 있다. **폴더가 존재하거나 비어 있는
것은 정상이며, 최종 완료 여부는 아래 8개 CSV의 생성과 검증 결과로 판단한다.**

## 3. 실행 전 준비

### 3.1 기본 입력 경로

인자를 생략하면 runner가 다음 파일을 찾는다.

| 입력 | 기본 위치 | 용도 |
|---|---|---|
| 기출문제 JSON | `ai/ml/ML_han_v1.json` | 문항·지문·질문·선지 |
| 역사 용어 시소러스 | `etl/raw_data`의 `*20211028*.csv` 한 개 | 용어 정규화·후보 검색 |
| AKS 백과사전 | `etl/raw_data/한국민족문화대백과사전/articles_detail.jsonl` | 표제어·이칭·정의·본문 후보 |
| ITKC 인물 | `etl/raw_data/한국고전종합DB_관계망/itkc_people.csv` | 인물 SourceRecord 후보 |
| ITKC 사건 | `etl/raw_data/한국고전종합DB_관계망/itkc_events.csv` | 사건 SourceRecord 후보 |

시소러스 후보가 없거나 두 개 이상이면 자동으로 하나를 고르지 않고 중단한다. 이때는
`--thesaurus-csv` 또는 운영 runner의 두 번째 위치 인자로 경로를 명시한다.

AKS·ITKC 3개 입력 중 하나라도 없으면 1~3단계까지만 실행되고 4~7단계는 건너뛴다.
Entity Resolution staging까지 필요하면 세 입력이 모두 있어야 한다.

### 3.2 환경과 API 키

용어 추출과 모델 검토는 OpenAI API를 사용한다. 프로젝트 가상환경과 `.env`의
`OPENAI_API_KEY`를 준비한다. API 호출 전에 반드시 `--dry-run`으로 입력 경로와 호출 대상을
확인한다.

현재 정책의 API 구간은 다음과 같다.

| 구간 | 설정 모델 | reasoning effort | 호출 여부 |
|---|---|---|---|
| 문항별 역사 용어 추출 | `gpt-5.6-terra` | `none` | 테스트·운영 runner의 1단계에서 호출 |
| term-level identity 판정 | `gpt-5.6-terra` | `high` | `execute_term_review.py`에서 별도 호출 |
| problem-level 대안 선택 | `gpt-5.6-sol` | `high` | 정책에는 있으나 현재 전용 executor 없음 |

전처리 runner를 한 번 실행했다고 term-level과 problem-level 모델까지 자동으로 호출되는 것은
아니다. 각 구간의 비용·checkpoint·승인 지점은 서로 분리되어 있다.

### 3.3 코드 단위 테스트

파이프라인 실행 전에 Neo4j 전처리 단위 테스트를 확인할 수 있다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover `
  -s test\MK\test_neo4j `
  -p "test_*.py"
```

이 테스트는 로직 회귀를 확인하는 것이며 실제 20문항 데이터 실행을 대신하지 않는다.

## 4. 권장 실행 순서

이 절은 위에서 아래로 순서대로 읽는다. 각 단계 안에 실행 명령, 생성 파일, 파일의 의미,
확인 방법과 다음 단계 진행 조건을 함께 적었다. 뒤쪽 절은 공통 정책과 장애 대응용이다.

### 4.1 소량 테스트 경로만 확인

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_preprocessing_test.py `
  --dry-run
```

이 명령은 API를 호출하지 않고 다음 항목만 콘솔에 출력한다.

- 실제로 선택된 입력 파일 절대 경로
- 테스트 출력 경로
- 문항 수, batch 크기, 재시도 횟수, 커버리지 임계값
- 정책 파일과 공유 시소러스 경로

파일은 새로 만들지 않는다.

### 4.2 소량 테스트 실행·산출물 확인

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_preprocessing_test.py
```

기본 설정은 20문항, LLM batch 크기 10, 재시도 1회다. 산출물은 운영 폴더가 아니라
`etl/preprocessing/neo4j/output/test_run` 아래에 생성된다.

실행 직후 사람이 확인할 파일은 다음 네 개다.

| 생성 파일 | 의미 | 확인할 내용 |
|---|---|---|
| `output/test_run/review/unique_exam_terms.csv` | 20문항에서 추출·정규화한 고유 역사 용어 | 한글 깨짐, 비용어, 이상 category |
| `output/test_run/review/source_coverage_report.json` | 추출 용어의 시소러스·AKS 후보 커버리지 | `coverage_percent`, `meets_threshold`, 미커버 용어 |
| `output/test_run/review/cases_requiring_review.csv` | 자동 확정하지 않은 AMBIGUOUS·UNRESOLVED case | `review_reason`, 후보 수, source |
| `output/test_run/internal/entity_resolution/exam_problem_contexts.csv` | 20개 problem ID와 실제 LLM 입력 텍스트 감사 | 행 수, ID 유일성, 텍스트 상태 |

나머지는 재실행과 후속 Entity Resolution에 사용하는 내부 산출물이다.

| 생성 위치·파일 | 의미 |
|---|---|
| `output/test_run/internal/term_extraction/term_extraction_checkpoint.jsonl` | 성공한 용어 추출 batch와 모델·정책 버전 |
| `output/test_run/internal/term_extraction/exam_terms_by_problem.json` | 모든 테스트 problem ID별 추출 용어 |
| `output/internal/shared/normalized_history_thesaurus.json` | 테스트·운영이 공유하는 정규화 시소러스 |
| `output/test_run/internal/entity_resolution/*.csv` | SourceRecord 후보·비교 신호·제안 실체 그룹·문항 배정 초안 |
| `output/test_run/internal/model_review/term_identity_review_tasks.jsonl` | 후속 term-level LLM 판정 입력 |

특정 문항 수만 확인하려면 다음처럼 실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_preprocessing_test.py `
  --limit 5 `
  --batch-size 5
```

테스트 실행이 끝난 뒤 최소한 다음을 확인한다.

```powershell
$testRoot = "etl/preprocessing/neo4j/output/test_run"
$problemTerms = Get-Content -Raw -Encoding UTF8 `
  "$testRoot/internal/term_extraction/exam_terms_by_problem.json" |
  ConvertFrom-Json
$contexts = Import-Csv -Encoding UTF8 `
  "$testRoot/internal/entity_resolution/exam_problem_contexts.csv"
$checkpointCount = (Get-Content -Encoding UTF8 `
  "$testRoot/internal/term_extraction/term_extraction_checkpoint.jsonl").Count
$coverage = Get-Content -Raw -Encoding UTF8 `
  "$testRoot/review/source_coverage_report.json" |
  ConvertFrom-Json

[pscustomobject]@{
  problem_term_rows = $problemTerms.Count
  context_rows = $contexts.Count
  unique_problem_ids = ($contexts.problem_id | Sort-Object -Unique).Count
  checkpoint_batches = $checkpointCount
  coverage_percent = $coverage.coverage_percent
  coverage_pass = $coverage.meets_threshold
}
```

기본 20문항 테스트와 현재 중복 없는 입력을 기준으로 다음이 정상이다.

- `problem_term_rows = 20`
- `context_rows = 20`
- `unique_problem_ids = 20`
- `checkpoint_batches = 2`
- `coverage_pass = True`이면 설정한 커버리지 임계값을 통과한 것

`checkpoint_batches`는 문항 수가 아니라 실제 저장된 LLM batch 수다. 완전히 같은
`extraction_text`가 있으면 LLM 호출은 공유되므로 문항 수와 단순 비례하지 않을 수 있다.

텍스트 감사 상태는 다음 명령으로 본다.

```powershell
$contexts |
  Group-Object input_text_match_status |
  Select-Object Name, Count

$contexts |
  Where-Object { $_.input_text_match_status -ne "EXACT" } |
  Select-Object problem_id, input_text_match_status, duplicate_text_group_id
```

다음 조건을 모두 만족하면 골든셋 gate 확인으로 넘어간다.

- runner 종료 코드가 0이다.
- 기본 실행이면 문항별 추출 JSON과 context가 각각 20행이다.
- `problem_id`가 모두 고유하다.
- 한글이 UTF-8로 정상 표시된다.
- coverage JSON 수치가 콘솔 출력과 일치한다.
- ER staging CSV 8개와 term task JSONL이 생성됐다.
- 7단계 성공 후 `candidate_retrieval` cache가 비어 있는 것은 정상이다.

### 4.3 골든셋 gate 확인

전체 term review LLM을 호출하기 전에 골든셋 평가 상태를 확인한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/import_and_evaluate_goldset.py `
  --dry-run
```

현재 활성 검수본은 100개 case다. 실제 완료 상태는
`goldset/human_review_csv/human_review_cases.csv`의 `case_review_status`로 확인한다.
미완료 case가 있으면 dry-run의 `BLOCKED_BY_GOLD_VALIDATION`과
`CASE_REVIEW_NOT_COMPLETE`가 정상적으로 실행을 막는다.

주요 확인 위치는 다음과 같다.

| 확인 파일 | 의미 | 진행 조건 |
|---|---|---|
| `goldset/internal/evaluation/goldset_evaluation_manifest.json` | HUMAN_REVIEW import·모델 평가 상태와 건수 | `status`와 validation 건수 확인 |
| `goldset/internal/evaluation/goldset_validation_errors.csv` | 사람 정답의 누락·모순 | 처리하지 않은 오류가 없어야 함 |
| `goldset/internal/evaluation/model_vs_gold_metrics.json` | 모델 대 사람 정답 전체 지표 | 합의한 승인 기준으로 판단 |
| `goldset/internal/evaluation/model_vs_gold_case_results.csv` | case별 오병합·오분리 원인 | 대표 실패 사례 직접 확인 |
| `goldset/internal/evaluation/model_vs_gold_candidate_role_metrics.csv` | 후보 역할별 precision·recall·F1 | 취약 역할 확인 |
| `goldset/human_review_csv/role_conflict_manual_review.csv` | 사람과 모델의 `EVIDENCE_ONLY`·`REJECTED` 충돌 후보 | target·원천 문맥을 재검토하고 `PENDING` 해소 |

현재 정책에는 production 전체 호출을 자동 승인하는 고정 숫자 임계값이 없다. 특히 다음을
사람이 보고 전체 term LLM 실행 여부를 승인해야 한다.

- identity pair precision·recall·F1
- false merge와 false split 사례
- 후보 역할 정확도, 원래 macro F1, weighted F1, 희소 역할 제외 macro F1과 역할별 support
- link status 정확도
- validation error가 비어 있는지

pair 지표는 다음처럼 구분해 읽는다.

| 지표 | 의미 | 주의점 |
|---|---|---|
| `proposal_identity_pair_recall` | 모델 원본 제안이 gold pair를 빠뜨리지 않은 비율 | 오병합이 있어도 recall은 1.0일 수 있으므로 precision과 함께 본다. |
| `proposal_identity_pair_precision` | 모델 원본 제안 pair 중 gold와 일치한 비율 | raw proposal을 자동 병합할 수 있는지 판단하는 핵심 지표다. |
| `verified_identity_pair_precision` | 게이트가 실제 승인한 pair의 정확도 | 자동 병합 안전성 지표다. |
| `verified_identity_pair_recall` | `VERIFIED` case 내부에서 승인된 gold pair의 기존 조건부 recall | 보류 pair는 분모에서 제외되므로 전체 자동처리율이 아니다. |
| `conditional_verified_identity_pair_recall` | 위 조건부 recall을 의미가 드러나는 이름으로 다시 기록 | 기존 필드와 같은 값이다. |
| `auto_accepted_identity_pair_recall` | 전체 gold pair 중 게이트가 자동 승인한 정답 pair 비율 | 실제 무인 자동 병합 coverage로 사용한다. |
| `auto_accepted_identity_pair_precision` | 자동 승인된 pair 중 gold와 일치한 비율 | 자동 병합 안전성으로 사용한다. |
| `deferred_gold_identity_pair_count` | 게이트가 확정하지 않은 gold pair 수 | 전체 gold pair와 함께 자동 병합 coverage를 계산한다. |
| `deferred_gold_identity_pair_rate` | 전체 gold pair 중 보류된 비율 | 자동화되지 않은 범위를 직접 보여 준다. |

`model_identity_pair_gate_results.csv`는 case 최종 상태와 분리된 pair별
`VERIFIED`, `NEEDS_MANUAL_REVIEW`, `INVALID` 결과와 차단 사유를 기록한다. case가
EntityType이나 다른 후보 때문에 보류되어도 이 파일의 안전한 pair는 독립 승인될 수 있다.
단, 보류 case의 canonical entity와 EntityType을 확정하는 용도로는 사용하지 않는다.

평가 JSON의 `auto_accepted_identity_pair_recall`은 다음 식으로 계산한다.

```text
자동 승인 pair recall
= verified true positive
  / (verified true positive + verified false negative + deferred gold pair)
```

v3 평가에서는 `24 / (24 + 0 + 35) = 0.406780`이다. 따라서
`verified_identity_pair_recall=1.0`은 자동 승인 범위 내부에서는 오분리가 없다는 뜻이고,
전체 gold pair의 약 40.7%가 자동 승인됐다는 뜻은 아니다.

보류 원인은 `deferred_gold_pair_gate_status_counts`,
`deferred_gold_pair_error_case_counts`, `deferred_gold_pair_error_pair_counts`에서 별도
CSV 없이 집계해 확인한다.

현재 identity pair gate는 `config/review_goldset.json`의
`identity_pair_gate.active_evidence_mode=connected_graph`를 사용한다. 3개 이상 멤버에서
모든 pair가 직접 양성일 필요는 없지만, 강한 충돌이 없는 `merge_eligible=true` edge로
전체 멤버가 하나의 연결 그래프를 이뤄야 한다.
실제 평가에 적용된 값은 metrics JSON의 `identity_pair_gate_policy_version`과
`identity_pair_gate_evidence_mode`에서 확인한다.

- 강한 pair 충돌과 pair 행 누락은 연결 여부와 관계없이 계속 `INVALID`다.
- 2개 멤버 대안은 edge가 하나뿐이므로 기존 완전 그래프와 동일하다.
- 회귀 비교가 필요하면 정책값을 `complete_graph`로 바꿔 같은 decision을 재평가한다.
- v3 기준 연결 그래프 적용 후 자동 승인 precision은 1.0을 유지했고,
  자동 승인 recall은 `0.406780 → 0.457627`로 증가했다.

실제 평가는 API 호출과 파일 갱신을 포함하므로 승인 후에만 `--dry-run`을 뺀다.
프롬프트 버전이나 프롬프트 본문이 바뀌면 기존 checkpoint를 재사용하지 않으므로 dry-run의
`pending_task_count`를 확인한 뒤 실제 호출을 승인한다.

`import_and_evaluate_goldset.py`가 평가를 끝내면
`role_conflict_manual_review.csv`가 갱신된다. 큐만 다시 만들 때는 다음 명령을 실행한다.
같은 task·candidate에 이미 작성한 사람 입력 컬럼은 보존된다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/role_conflict_review.py
```

이 큐는 gold를 자동 변경하지 않는다. 재검토 결과가 gold와 다르면
`human_review_candidates.csv`의 같은 `term_review_task_id`·`source_candidate_id` 행을
수정하고 검증을 다시 실행한다.

`goldset/build_gold_set.py`는 gate 확인 명령이 아니라 표본 생성·확장 명령이다.
운영 전처리에서 term task가 생성된 뒤 활성 검수본을 정책 목표 100건까지 맞출 때 다음처럼
인자 없이 실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/goldset/build_gold_set.py
```

이 실행과 산출물의 관계는 다음과 같다.

| 생성·갱신 파일 | 의미 | 기본 실행 동작 |
|---|---|---|
| `goldset/human_review_csv/human_review_cases.csv` | 사람이 작성하는 case 검수본 | 기존 행·입력값을 보존하고 신규 case만 `NOT_STARTED`로 추가 |
| `goldset/human_review_csv/human_review_candidates.csv` | case별 원천 후보 검수본 | 기존 후보 행·입력값을 보존하고 신규 case 후보만 추가 |
| `goldset/internal/source/gold_review_tasks.jsonl` | 평가에 고정할 task snapshot | 기존 20개 회귀 snapshot을 유지하고 현재 모집단 신규 snapshot 추가 |
| `goldset/internal/source/gold_*_template.csv` | 사람 입력이 없는 빈 양식 | 전체 100개 기준으로 다시 생성 |
| `goldset/internal/source/rule_based_baseline.csv` | 코드 제안 baseline | 전체 100개 기준으로 다시 생성 |
| `goldset/internal/source/gold_sample_distribution.csv` | 모집단 대비 표본 분포 | 전체 100개 기준으로 다시 계산 |
| `goldset/internal/source/gold_sample_manifest.json` | 입력 hash·보존/추가 건수·경로 | 확장 실행 기록 |

기존 `term_review_task_id`는 추가 대상에서 제외한다. 활성 case가 이미 목표 100건 이상이면
모든 파일을 그대로 두고 종료하므로 같은 명령을 재실행해도 검수본이 바뀌지 않는다.
`--force-overwrite-review`는 활성 검수본 전체를 새 표본으로 교체할 때만 사용하는 초기화
옵션이므로 일반 확장에는 사용하지 않는다.

기존 골든셋이 현재 운영 term task와 같은 모집단에서 만들어졌는지는 다음으로 확인한다.

```powershell
$currentTasks = "etl/preprocessing/neo4j/output/internal/model_review/term_identity_review_tasks.jsonl"
$goldManifest = Get-Content -Raw -Encoding UTF8 `
  "etl/preprocessing/neo4j/goldset/internal/source/gold_sample_manifest.json" |
  ConvertFrom-Json

[pscustomobject]@{
  current_task_count = (Get-Content -Encoding UTF8 $currentTasks).Count
  current_task_sha256 = (Get-FileHash -Algorithm SHA256 $currentTasks).Hash.ToLower()
  gold_population_count = $goldManifest.population_case_count
  gold_input_sha256 = $goldManifest.input_task_sha256
}
```

해시가 다르다는 사실만으로 기존 골든셋이 잘못된 것은 아니다. 기본 확장은 기존 회귀
snapshot을 보존한다. 현재 모집단만으로 완전히 새 표본을 만들어 비교하려는 경우에는
다음처럼 **새 출력 폴더**를 명시한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/goldset/build_gold_set.py `
  etl/preprocessing/neo4j/output/internal/model_review/term_identity_review_tasks.jsonl `
  etl/preprocessing/neo4j/goldset/internal/source_v2
```

이 선택 명령의 산출물은 다음과 같다.

| 생성 파일 | 의미 | 다음 작업 |
|---|---|---|
| `source_v2/gold_review_tasks.jsonl` | 새 골든셋의 원본 task snapshot | 수정하지 않고 보존 |
| `source_v2/gold_case_labels_template.csv` | case 단위 빈 사람 검수 양식 | 사람이 link status·사유 작성 |
| `source_v2/gold_candidate_labels_template.csv` | 후보 단위 빈 사람 검수 양식 | 사람이 후보 역할·대안 작성 |
| `source_v2/rule_based_baseline.csv` | 규칙 기반 기준 결과 | 모델 지표와 비교 |
| `source_v2/gold_sample_distribution.csv` | category·후보 수 등 표본 분포 | 모집단 대표성·취약 구간 확인 |
| `source_v2/gold_sample_manifest.json` | 입력 hash·정책·표본 수·출력 경로 | 재현성과 입력 일치 확인 |

별도 출력 폴더 모드는 표본과 빈 양식만 만든다. 기본 활성 확장 모드도 사람 검수,
importer 검증, 모델 실행과 평가는 수행하지 않는다.

### 4.4 운영 1,600문항 전처리 실행·산출물 확인

테스트 결과를 승인한 뒤 실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_neo4j_preprocessing.py
```

기본 batch 크기는 20이고 `--limit 0`은 전체 문항을 뜻한다. 현재 1,600문항의 완전 동일한
`extraction_text`가 없다면 용어 추출 LLM은 약 80 batch를 호출한다. 재실행할 때 모델·reasoning
effort·추출 정책·텍스트 정책 버전이 같은 checkpoint는 재사용한다.

운영 결과는 `output/test_run`이 아니라 다음에 생성된다.

- 사용자 확인: `output/review`
- 내부 추적: `output/internal`
- 최종 확정 후: `output/final_identity`

콘솔의 `[1/7]`~`[7/7]`과 생성 파일은 다음처럼 대응한다.

| 콘솔 단계 | 처리 내용 | 생성 파일 | 의미·확인 항목 |
|---|---|---|---|
| `[1/7]` | 텍스트 재구성·역사 용어 추출 | `internal/term_extraction/term_extraction_checkpoint.jsonl` | 성공 batch·재실행 기준 |
| `[1/7]` | 문항별 용어 저장 | `internal/term_extraction/exam_terms_by_problem.json` | 1,600 problem ID가 모두 있는지 |
| `[1/7]` | 고유 용어 집계 | `review/unique_exam_terms.csv` | 깨진 한글·비용어·category 확인 |
| `[2/7]` | 시소러스 정규화 | `internal/shared/normalized_history_thesaurus.json` | 운영·테스트 공용 표준화 결과 |
| `[3/7]` | 원천 커버리지 계산 | `review/source_coverage_report.json` | 커버리지·임계값·미커버 용어 |
| `[4/7]` | 이름 후보 검색 | `internal/candidate_retrieval/name_match_candidates.json` | 7단계 전까지만 쓰는 cache |
| `[5/7]` | AKS definition 보강 | `internal/candidate_retrieval/definition_match_candidates.json` | 7단계 전까지만 쓰는 cache |
| `[6/7]` | AKS 본문 언급 보강 | `internal/candidate_retrieval/body_mention_candidates.json` | 7단계 전까지만 쓰는 cache |
| `[7/7]` | ER staging 생성 | `internal/entity_resolution/*.csv` | 후속 검토의 구조화 입력 |
| `[7/7]` | 보류 case 분리 | `review/cases_requiring_review.csv` | 사람이 확인할 AMBIGUOUS·UNRESOLVED case |
| `[7/7]` | term task 생성 | `internal/model_review/term_identity_review_tasks.jsonl` | 다음 term-level LLM 입력 |

4~6단계 cache 3개는 7단계가 성공하면 자동 삭제된다. 실행 후
`internal/candidate_retrieval`이 비어 있는 것은 정상이다.

운영 실행 직후 사람이 먼저 확인할 파일은 다음 세 개다.

| 파일 | 의미 | 확인할 내용 |
|---|---|---|
| `output/review/unique_exam_terms.csv` | 전체 문항의 고유 추출 용어 | 한글·비용어·category·빈 값 |
| `output/review/source_coverage_report.json` | 시소러스·AKS 이름 후보 커버리지 | `meets_threshold`와 미커버 예시 |
| `output/review/cases_requiring_review.csv` | 자동 확정하지 않은 ER case | `review_reason`, `link_status`, 후보 분포 |

상세 원인 추적이 필요할 때만 다음 ER staging 8개를 본다.

| 파일 | 한 행의 단위 | 의미 |
|---|---|---|
| `entity_cases.csv` | 정규화 용어·category case | 어떤 실체로 볼지 결정할 중심 case |
| `candidate_source_records.csv` | case의 원천 후보 | AKS·시소러스·ITKC 후보와 검색 근거 |
| `candidate_comparison_features.csv` | 후보의 비교 feature | 이름·한자·시대·생몰년·본관·유형 정합성 |
| `candidate_pair_merge_signals.csv` | 후보 두 개의 pair | 동일 실체 병합 근거와 충돌 신호 |
| `proposed_entity_groups.csv` | 제안 canonical 대안 | 동일 실체로 보이는 후보 cluster |
| `proposed_entity_group_members.csv` | 대안과 후보 membership | 후보 역할과 제안 cluster 소속 |
| `exam_problem_contexts.csv` | 원래 problem ID | LLM 입력 텍스트와 `input_text` 감사 상태 |
| `exam_problem_entity_assignments_draft.csv` | 문항과 case 연결 | 아직 확정되지 않은 문항별 배정 초안 |

운영 문항 보존 상태는 바로 확인한다.

```powershell
$outputRoot = "etl/preprocessing/neo4j/output"
$problemTerms = Get-Content -Raw -Encoding UTF8 `
  "$outputRoot/internal/term_extraction/exam_terms_by_problem.json" |
  ConvertFrom-Json
$contexts = Import-Csv -Encoding UTF8 `
  "$outputRoot/internal/entity_resolution/exam_problem_contexts.csv"
$coverage = Get-Content -Raw -Encoding UTF8 `
  "$outputRoot/review/source_coverage_report.json" |
  ConvertFrom-Json

[pscustomobject]@{
  problem_term_rows = $problemTerms.Count
  context_rows = $contexts.Count
  unique_problem_ids = ($contexts.problem_id | Sort-Object -Unique).Count
  duplicate_groups = ($contexts |
    Where-Object { $_.duplicate_text_group_id } |
    Select-Object -ExpandProperty duplicate_text_group_id -Unique).Count
  coverage_percent = $coverage.coverage_percent
  coverage_pass = $coverage.meets_threshold
}

$contexts |
  Where-Object { $_.input_text_match_status -ne "EXACT" } |
  Select-Object problem_id, input_text_match_status, duplicate_text_group_id
```

현재 원본 기준으로 `problem_term_rows`, `context_rows`, `unique_problem_ids`는 모두 1,600이고
완전 동일 텍스트 중복 그룹은 0이어야 한다. 알려진 예외는 다음과 같다.

- `cj_v41_img_57_15`: `CONTENT_CONFLICT`
- `cj_v41_img_71_16`: `WHITESPACE_EQUIVALENT`
- `cj_v41_0519`, `cj_v41_img_57_50`: 선지가 다르므로 둘 다 별도 문항

위 수치, coverage, 미커버 예시, review queue를 확인한 뒤 term-level 호출 규모 확인으로 넘어간다.

### 4.5 term-level task 실행 규모 확인

7단계가 성공하면 term task는 이미 생성되어 있다. API 호출 전에 확인한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/execute_term_review.py `
  etl/preprocessing/neo4j/output/internal/model_review/term_identity_review_tasks.jsonl `
  etl/preprocessing/neo4j/output/internal/model_review `
  --dry-run
```

dry-run 결과에서 선택 task 수, checkpoint 재사용 수, 실제 호출 예정 수를 확인한다.
이 명령은 API를 호출하지 않고 파일도 새로 만들지 않는다.

### 4.6 term-level LLM 판정·산출물 확인

골든셋 결과에 대한 명시적 승인 후 실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/execute_term_review.py `
  etl/preprocessing/neo4j/output/internal/model_review/term_identity_review_tasks.jsonl `
  etl/preprocessing/neo4j/output/internal/model_review
```

처음에는 `--limit 5`처럼 소량으로 응답 형태와 실패 파일을 확인할 수 있다. 성공 task는
checkpoint에 즉시 저장되므로 같은 조건으로 재실행하면 성공분을 다시 호출하지 않는다.

다음 파일은 모두 `output/internal/model_review`에 생성된다.

| 생성 파일 | 의미 | 다음 단계 전 확인 |
|---|---|---|
| `term_identity_review_checkpoint.jsonl` | 재실행 시 재사용하는 성공 task 기록 | 성공 checkpoint 수 |
| `term_identity_model_decisions.jsonl` | term-level LLM의 원본 제안 | task와 decision 수 |
| `term_identity_model_failures.csv` | 재시도 후에도 실패한 task와 원인 | 실패가 있으면 먼저 처리 |
| `term_identity_model_run_manifest.json` | 모델·prompt·schema·task·실행 통계 | 모델·정책 버전과 완료 건수 |

원본 decision은 아직 최종 승인 결과가 아니다.

### 4.7 term-level 검증 gate·산출물 확인

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/semantic_review.py `
  etl/preprocessing/neo4j/output/internal/entity_resolution `
  etl/preprocessing/neo4j/output/internal/model_review `
  --decisions `
  etl/preprocessing/neo4j/output/internal/model_review/term_identity_model_decisions.jsonl
```

다음 파일은 모두 `output/internal/model_review`에 생성된다.

| 생성 파일 | 의미 | 다음 단계 전 확인 |
|---|---|---|
| `verified_term_review_summary.csv` | case별 검증 상태와 term-level 결론 | verification status 분포 |
| `verified_entity_alternatives.csv` | 검증 gate를 거친 canonical 대안 | 비어 있는 대안·중복 ID |
| `verified_candidate_roles.csv` | 각 SourceRecord 후보의 검증된 역할 | `AMBIGUOUS`·보류 역할 |
| `term_review_validation_errors.csv` | 누락·모순·정합성 보류 사유 | 오류 코드별 건수와 사례 |

`term_review_validation_errors.csv`에 오류가 남았으면 바로 finalizer로 넘어가지 않는다.
`NEEDS_MANUAL_REVIEW`도 자동 확정으로 간주하지 않는다.

### 4.8 problem-level task 생성·산출물 확인

검증된 term 결과로 문항별 대안 선택 task를 만든다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/problem_review.py `
  etl/preprocessing/neo4j/output/internal/entity_resolution `
  etl/preprocessing/neo4j/output/internal/model_review
```

주요 산출물은 `problem_entity_choice_tasks.jsonl`이다. 하나의 term case에서 복수 canonical
대안이 가능한 문항만 모델 또는 사람의 문맥 판정 대상으로 들어간다. 명확한 문항은
deterministic assignment로 처리된다.

같은 명령이 다음 파일을 `output/internal/model_review`에 만든다.

| 생성 파일 | 의미 | 이 시점의 해석 |
|---|---|---|
| `problem_entity_choice_tasks.jsonl` | 복수 대안을 문항 문맥으로 선택할 task | 후속 decision 입력 |
| `verified_problem_review_summary.csv` | 현재 입력된 problem decision 검증 요약 | decision이 없으면 미완료 행 존재 가능 |
| `verified_problem_entity_assignments.csv` | 자동으로 명확한 deterministic 배정 | problem task 대상은 아직 미확정 가능 |
| `problem_review_validation_errors.csv` | decision 누락·잘못된 선택 등 | task 생성 직후 누락 오류는 예상 가능 |

decision 없이 이 명령을 실행하면 unresolved problem task는 validation error에 남을 수 있다.
이는 task 생성 시점에는 정상이다.

### 4.9 problem-level decision 생성·검증 gate

사용할 prompt와 schema는 다음에 있다.

- `config/prompts/problem_resolution_review.md`
- `config/schemas/problem_resolution_decision.schema.json`

전용 executor로 API 호출 예정 건수를 먼저 확인한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/execute_problem_review.py `
  etl/preprocessing/neo4j/output/internal/model_review/problem_entity_choice_tasks.jsonl `
  etl/preprocessing/neo4j/output/internal/model_review `
  --dry-run
```

확인 후 실제 판정을 실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/execute_problem_review.py `
  etl/preprocessing/neo4j/output/internal/model_review/problem_entity_choice_tasks.jsonl `
  etl/preprocessing/neo4j/output/internal/model_review
```

decision 파일이 준비되면 gate를 실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/problem_review.py `
  etl/preprocessing/neo4j/output/internal/entity_resolution `
  etl/preprocessing/neo4j/output/internal/model_review `
  --decisions `
  etl/preprocessing/neo4j/output/internal/model_review/problem_entity_model_decisions.jsonl
```

다음 파일은 `output/internal/model_review`에 생성된다.

| 생성 파일 | 의미 | finalizer 전 확인 |
|---|---|---|
| `verified_problem_review_summary.csv` | problem decision별 검증 결과 | 전체 task가 판정됐는지 |
| `verified_problem_entity_assignments.csv` | deterministic 결과와 검증된 모델 결과를 합친 문항별 배정 | 미배정·중복 배정 여부 |
| `problem_review_validation_errors.csv` | 누락·잘못된 ID·허용되지 않은 선택 등 | 처리하지 않은 오류가 없는지 |

### 4.10 최종 identity CSV 생성·산출물 확인

term·problem validation error를 처리한 뒤 실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/finalize_entity_resolution.py `
  etl/preprocessing/neo4j/output/internal/entity_resolution `
  etl/preprocessing/neo4j/output/internal/model_review `
  etl/preprocessing/neo4j/output/final_identity `
  --registry `
  etl/preprocessing/neo4j/output/final_identity/canonical_entity_registry.csv
```

최초 실행에는 registry 파일이 없어도 빈 registry에서 시작한다. 재실행에서는 기존 registry를
읽어 이미 발급된 canonical ID를 가능한 경우 재사용한다.

최종 10개 파일은 모두 `output/final_identity`에 생성된다.

| 파일 | 의미 |
|---|---|
| `canonical_entity_registry.csv` | 영구 canonical ID와 표시명·유형·identity member의 기준표 |
| `neo4j_exam_term_nodes.csv` | 원천 매칭 여부와 무관하게 보존하는 기출 추출 용어 |
| `neo4j_canonical_entity_nodes.csv` | Neo4j CanonicalEntity 노드 입력 |
| `neo4j_source_record_nodes.csv` | AKS·시소러스·ITKC SourceRecord 노드 입력 |
| `neo4j_entity_name_nodes.csv` | 검색·표시용 EntityName 노드 입력 |
| `neo4j_source_to_entity_relationships.csv` | SourceRecord에서 CanonicalEntity로 가는 identity 관계 |
| `neo4j_name_to_entity_relationships.csv` | EntityName에서 CanonicalEntity로 가는 이름 참조 관계 |
| `neo4j_exam_term_to_entity_relationships.csv` | 검증된 ExamTerm에서 CanonicalEntity로 가는 참조 관계 |
| `exam_problem_entity_assignments_final.csv` | 문항별 최종 canonical entity 배정 |
| `single_source_entities_requiring_approval.csv` | 다원천 자동 확정 조건을 충족하지 못한 단일 원천 승인 대기열 |

`single_source_entities_requiring_approval.csv`가 비어 있지 않아도 해당 기출 용어는
`ExamTerm`으로 보존된다. 공식 원천 연결을 확정하려는 행만 추가 승인 대상이다.

다음 조건을 모두 만족해야 final identity 생성 완료로 판단한다.

- term executor failure가 없거나 모두 처리됐다.
- 안전한 identity pair 연결 성분이 검증됐고 나머지 후보는 보류됐다.
- problem task에 필요한 decision이 모두 준비됐다.
- problem validation error가 처리됐다.
- final identity 10개 CSV가 모두 생성됐다.
- single-source 승인 대기열을 별도로 검토했다.
- 최종 문항 배정 수와 정책상 제외·보류 수를 대조했다.

### 4.11 실제 Neo4j 적재

loader는 먼저 CSV 참조 무결성과 `ACCEPTED` 상태만 검사한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/load_final_identity.py `
  etl/preprocessing/neo4j/output/final_identity `
  --dry-run
```

검증 결과가 `READY`일 때 실제 적재한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/entity_resolution/load_final_identity.py `
  etl/preprocessing/neo4j/output/final_identity
```

이 loader는 DB 전체를 초기화하지 않는다. `ExamTerm`, `CanonicalEntity`,
`SourceRecord`, `EntityName`, `HAS_ENTITY_TYPE`, `RESOLVES_TO`, `REFERS_TO`만
ID 기준으로 upsert한다.
`exam_problem_entity_assignments_final.csv`와
`single_source_entities_requiring_approval.csv`는 감사·검토 파일이므로 DB에 넣지 않는다.

### 4.12 통합 실행 파일

별도 명령을 순서대로 입력하지 않으려면 다음 통합 runner를 사용한다. 인자 없이 실행하면
파일을 생성하거나 API·DB를 호출하지 않고 전체 계획만 출력한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_full_neo4j_pipeline.py
```

최종 CSV까지 실제 실행:

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_full_neo4j_pipeline.py `
  --execute
```

최종 CSV 생성 후 Neo4j upsert까지 실행:

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_full_neo4j_pipeline.py `
  --execute `
  --load-neo4j
```

통합 runner의 순서는 다음과 같다.

```text
골드셋 안전성 gate
  -> 전체 용어 추출·후보 생성
  -> term LLM·pair/case gate
  -> problem LLM·gate
  -> final identity CSV
  -> 명시적으로 요청한 경우에만 Neo4j upsert
```

제한 실행(`--limit`, `--term-limit`, `--problem-limit`) 결과에는
`--load-neo4j`를 사용할 수 없다. 골드셋 gate는 자동 승인 pair precision과 검증 후
오병합 수를 확인하며, 우회가 꼭 필요할 때만 `--skip-goldset-gate`를 명시한다.

### 4.13 사실 검색 그래프

`run_full_neo4j_pipeline.py`는 final identity까지의 runner다. 공식 관계,
canonical 사실, 검색 Anchor와 RAG 후보는 다음 runner에서 기존 최종 출력을 입력으로
사용한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_fact_retrieval_pipeline.py
```

기본 실행은 CSV·JSONL을 만들고 Neo4j 적재는 dry-run으로만 검사한다. LLM도 호출하지
않는다. 외부 사실 검증 결과를 적용할 때는 schema에 맞는 JSONL을 전달한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_fact_retrieval_pipeline.py `
  --external-verification-results verified_facts.jsonl
```

실제 DB 적재는 출력과 사실 검증 상태를 확인한 뒤에만 별도로 실행한다.

```powershell
.\.venv\Scripts\python.exe `
  etl/preprocessing/neo4j/run_fact_retrieval_pipeline.py `
  --load-neo4j
```

외부 검증 입력 schema:
`config/schemas/external_fact_verification_result.schema.json`

## 5. 문항 텍스트 정책과 중복 처리

LLM 입력 텍스트는 다음 순서로 구성한다.

```text
material
question
choice 1
choice 2
...
```

- `material`과 `question`이 모두 있으면 두 필드로 stem을 재구성한다.
- 둘 중 하나라도 없으면 `input_text`를 stem으로 사용한다.
- 선지는 항상 원래 순서대로 모두 포함한다.
- 문항의 식별 기준은 `problem_id`다.
- `problem_id`가 비었거나 중복이면 데이터 오류로 중단한다.
- 텍스트가 같아도 문항 행은 삭제하지 않는다.
- 완전히 같은 `extraction_text`만 LLM을 한 번 호출하고 결과를 원래 모든 `problem_id`에 복제한다.
- 선지 내용·문장부호·순서가 다르면 다른 `extraction_text`이며 별도 문항으로 유지한다.

`exam_problem_contexts.csv`의 감사 상태는 다음과 같다.

| 상태 | 의미 |
|---|---|
| `EXACT` | 정리한 `input_text`와 `material + question`이 일치 |
| `WHITESPACE_EQUIVALENT` | 공백·줄바꿈 차이만 존재 |
| `CONTENT_CONFLICT` | 실제 내용이 다름 |
| `INPUT_COMPONENT_MISSING` | material 또는 question 누락으로 `input_text` fallback 사용 |

정상 문항은 파일 크기를 줄이기 위해 원본 stem 상세를 비워 둘 수 있다. 충돌 문항 또는 실제
중복 그룹에는 감사에 필요한 원본·재구성 값이 남는다.

## 6. 재실행·중단 복구

- 같은 설정으로 다시 실행하면 호환되는 용어 추출 checkpoint를 재사용한다.
- term review도 task ID·모델·prompt·정책이 맞는 성공 checkpoint를 재사용한다.
- 실패한 batch 또는 task는 설정된 횟수만큼 재시도한다.
- 4~6단계 cache는 7단계 성공 전에는 복구 자료이고, 성공 후에는 자동 삭제된다.
- 정책 또는 text policy를 바꾼 뒤 과거 checkpoint가 재사용되지 않는 것은 정상이다.
- 중간 파일을 임의 수정하면 hash·ID·후속 gate 정합성이 깨질 수 있으므로 직접 고치지 않는다.
- 완전 초기화가 필요하면 사람 검수본·goldset·기존 canonical registry를 먼저 분리 보관한 뒤,
  삭제 대상을 정확히 확정하고 진행한다.

## 7. UTF-8과 한글 깨짐

JSON과 JSONL은 UTF-8, CSV는 Excel 호환을 위해 주로 UTF-8 BOM으로 저장한다. Windows
PowerShell에서 JSON을 읽을 때는 반드시 `-Encoding UTF8`을 지정한다.

```powershell
$jsonPath = "etl/preprocessing/neo4j/output/test_run/internal/term_extraction/exam_terms_by_problem.json"
Get-Content -Raw -Encoding UTF8 $jsonPath | ConvertFrom-Json
```

`Get-Content` 기본 인코딩으로 화면만 깨진 것인지, 파일 자체가 깨진 것인지 구분해야 한다.
`-Encoding UTF8`로도 `?` 또는 깨진 한글이 나오면 파일 생성 원천부터 다시 확인한다.

## 8. 자주 혼동하는 사항

- `run_preprocessing_test.py`만 실행하면 **20문항 전처리와 term task 생성까지** 된다.
- 테스트 runner는 term review LLM, problem review, finalizer를 자동 실행하지 않는다.
- `coverage_pass=True`는 시소러스·AKS 후보 커버리지가 기준을 넘었다는 뜻이지 ER 정확도 승인이 아니다.
- `cases_requiring_review.csv`가 생기는 것은 실패가 아니라 보류 대상을 보존한 것이다.
- `internal/candidate_retrieval`이 비어 있는 것은 7단계 성공 후 정상이다.
- `final_identity` 폴더가 비어 있는 것은 아직 finalizer 전이면 정상이다.
- term model decision은 제안이고 term gate를 통과해야 한다.
- problem task 생성과 problem decision 생성은 다른 단계다.
- 현재 problem decision API executor와 실제 Neo4j loader는 별도 구현이 필요하다.
- 테스트와 운영은 시소러스만 `output/internal/shared`에서 공유하고, 나머지 결과는 분리한다.

## 9. 관련 문서

- `output/README.md`: 결과 폴더의 간단한 용도 안내
- `config/README.md`: 분할 정책 파일 구성
- `goldset/README.md`: 골든셋 생성·검수 흐름
- `goldset/human_review_csv/README.md`: 사람이 작성하는 검수 CSV 규칙
- `docs/neo4j/01_fact_graph_current_data_eda.md`: 현재 데이터와 사실 그래프 구축 가능성 EDA
- `etl/preprocessing/neo4j/goldset/README.md`: 골든셋 평가 상세

# Question Generation

검증된 closed pack을 5선지 generation pack으로 변환한 뒤 지문과 선지를 생성하는 현행 파이프라인입니다.

## Versioned Production Data

2026-07-23에 생성·평가를 마친 고유 380문항과 실제 출제 Pack은 `ai/question_generation/data/production_20260723/`에 보관합니다.

```text
packs/
  standard_50.json
  chronology_10.json
  image_passage_10/
  image_choice_27/
  image_choice_90/
  image_choice_117_manifest.json
questions/
  standard_305/
  chronology_and_image_75/
```

- `standard_305`: 일반 선택형 295문항과 이미지 자료형 10문항
- `chronology_and_image_75`: 연대기형 50문항과 이미지 선지형 25문항
- 이미지 파일은 복제하지 않고 원본 문제와 Pack에 기록된 `https://contents.history.go.kr/` URL을 사용합니다.
- `image_choice_90`은 기존 3팩과 겹치지 않는 10팩·고유 이미지 90개이며, `image_choice_117_manifest.json`으로 기존 27개 회전 입력과 함께 사용합니다.
- 실행 로그, 평가 checkpoint, API 응답 원문은 재적재에 필요하지 않아 포함하지 않습니다.

Fact Graph 기반 10팩과 최종 50문항은 `data/production_20260727`에 있습니다.
DB 적재 명령은 해당 폴더의 `README.md`를 따릅니다.

서비스 DB에 넣기 전에는 반드시 `--dry-run`으로 중복과 입력 계약을 확인합니다.

```powershell
$root = "ai\question_generation\data\production_20260723\questions"

.\.venv\Scripts\python.exe -m ai.question_generation.postprocess_questions import-db `
  --input "$root\standard_305\questions.json" `
  --explanations "$root\standard_305\choice_explanations.jsonl" `
  --classifications "$root\standard_305\service_classifications.jsonl" `
  --dry-run

.\.venv\Scripts\python.exe -m ai.question_generation.postprocess_questions import-db `
  --input "$root\chronology_and_image_75\questions.json" `
  --explanations "$root\chronology_and_image_75\choice_explanations.jsonl" `
  --classifications "$root\chronology_and_image_75\service_classifications.jsonl" `
  --dry-run
```

검증 결과가 각각 305개와 75개이면 같은 명령에서 `--dry-run`만 제거합니다. `source_key`가 이미 존재하는 문항은 내용·선지를 중복 삽입하지 않고 서비스 분류만 갱신합니다.

## Current Modules

- `core/`: 입력 계약, 난이도, 관계축, 문자열 처리
- `retrieval/closed_pack_bank.py`: 운영과 분리된 검수자 지정 closed pack 적재 도구
- `retrieval/image_pack_input.py`: 이미지 closed pack 전용 5선지 입력 변환
- `generation/`: GPT 지문·발문, V41 SLLM 입력·전송, 최종 문항 조립
- `evaluation/`: 생성과 분리된 v1.8 최종 평가
- `workflows/question_pipeline.py`: 5선지 입력 한 건을 실행하는 운영 오케스트레이터
- `tests/`: 현행 closed-pack 파이프라인 계약 테스트

## Single Question

현행 실행기는 검증된 5선지 generation pack JSON을 입력으로 받습니다.

```powershell
.\.venv\Scripts\python.exe -m ai.question_generation `
  --pack-input <generation-pack.json> `
  --output "$env:USERPROFILE\Desktop\문제생성 파이프라인 산출물\runs\question.json" `
  --dry-run
```

실제 생성은 `--dry-run`만 제거합니다. 생성과 평가는 분리되어 있으므로 최종 평가는 별도로 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m ai.question_generation.evaluation.v18 `
  --input <question.json> `
  --output-prefix <evaluation-output-prefix>
```

## Mixed 50-Question Exam

검수·평가가 끝난 문항 풀에서 일반 선택형, 연대기형, 이미지 선지형을 섞어 50문항을 편성합니다. API를 호출하지 않으며 `standard_50.json`의 난이도·시대 분포를 그대로 채웁니다.

```powershell
.\.venv\Scripts\python.exe -m ai.question_generation.workflows.mixed_mock_exam `
  --standard 35 `
  --chronology 10 `
  --image 5 `
  --output-dir "ai\question_generation\outputs\mixed_50"
```

유형별 수의 합은 quota Pack의 50개와 같아야 합니다. 같은 seed와 입력은 같은 시험지를 만들며, 유형 수와 난이도·시대 quota를 동시에 채울 수 없으면 일부를 임의 대체하지 않고 실패합니다.

체크포인트 구조:

```text
input
components.material
components.correct
components.distractors.1..4
question
```

## Closed Pack

일반 텍스트 closed pack은 다음 구조입니다.

```text
family_id
topic_type
question_frames
answer_eligible_owner_ids
members[9]
```

9개 중 정답 owner 1개와 오답 owner 4개를 선택해 기존 5선지 입력으로 변환합니다. collection 파일은 `family_id`를 지정하고, 정답 owner와 frame은 필요할 때 바꿉니다.

정답 owner를 회전하는 현행 pack의 모든 `question_frames`는 `answer_owner_scope: "material_target"`이어야 합니다. 전후 시기처럼 지문 대상과 다른 owner를 정답으로 요구하는 frame은 이 pack 구조에서 거부합니다.

```powershell
.\.venv\Scripts\python.exe -m ai.question_generation `
  --pack-input <closed-pack-collection.json> `
  --family-id <family-id> `
  --answer-owner-id <owner-id> `
  --frame-index 0 `
  --output <question.json> `
  --dry-run
```

각 pack은 V41 분류의 `topic_type`을, 각 frame은 검수된 `question_task_instruction`과 `distractor_type`을 명시해야 하며 런타임 fallback은 없습니다. `--answer-owner-id`를 생략하면 `answer_eligible_owner_ids` 안에서 seed 기반으로 회전합니다. 오답 4개는 같은 pack의 나머지 owner에서 결정론적으로 선택합니다.

이미지 source pack은 `answer_owner_id`, `distractor_owner_ids` 4개, `frame_id`를 명시해야 합니다. 이미지 adapter는 owner나 frame을 문자열·배열 순서로 추정하지 않습니다.

collection의 모든 pack에서 한 문항씩 생성하려면 다음 batch CLI를 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m ai.question_generation.workflows.closed_pack_batch `
  --pack-input <closed-pack-collection.json> `
  --output-dir <output-directory>
```

팩별 owner를 순환해 여러 변형을 만들 때는 `--variants-per-pack 9`를 사용합니다. 여러 실행 사이의 조합 재사용도 막으려면 같은 `--usage-manifest` 파일을 지정합니다.

50팩에서 300문항을 만들 때는 `--variants-per-pack 6`을 사용합니다. 지문 형식 비율은 `material_type_prompt_rules.json`의 `_distribution`을 모든 batch에 적용하며 Python 코드에 개수를 고정하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m ai.question_generation.workflows.closed_pack_batch `
  --pack-input <closed-pack-collection.json> `
  --image-pack-manifest <image-generation-pack-manifest.json> `
  --image-count 10 `
  --output-dir <output-directory> `
  --variants-per-pack 6 --evaluate
```

`--image-count 10`은 총수를 늘리지 않고 같은 난이도·시대 셀의 텍스트 문항 10개를 이미지 문항으로 치환합니다. 따라서 전체는 300문항으로 유지됩니다.

공식 기출의 난이도별 시대 분포로 50문항 모의고사를 편성할 때는 다음과 같이 실행합니다. 한 회분에는 같은 `family_id`가 두 번 들어가지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m ai.question_generation.workflows.closed_pack_batch `
  --pack-input <closed-pack-collection.json> `
  --official-data <official-question-data.json> `
  --output-dir <output-directory> `
  --mock-exam --easy 10 --medium 30 --hard 10 `
  --evaluate --evaluation-repair-cycles 3
```

`--evaluate`는 전체 문항을 v1.8로 평가하고, 탈락 문항에서 지목된 컴포넌트만 재생성한 뒤 수정 문항만 재평가합니다. 정답·오답 컴포넌트는 SLLM 재생성 2회 뒤에도 실패하면 직전 출력과 평가 피드백을 넣어 GPT가 같은 컴포넌트만 수리합니다. `needs_verification`은 자동 수정하지 않습니다.

최종 평가와 부분 재생성이 끝나면 동료의 선지 품질 모델 v15를 한 번 실행해 `evaluation/choice_quality_review.csv`와 `evaluation/choice_quality_review.json`을 남깁니다. `검수필요`가 하나라도 있는 문항에는 `review_tags: ["ML주의"]`를 붙인 `evaluation/choice_quality_tagged_questions.json`도 생성합니다. 이 파일을 후속 해설·DB 적재 입력으로 사용합니다. 모델 결과는 문항 탈락이나 추가 재생성을 일으키지 않으며, 텍스트 선지가 없는 `choice_mode=image` 문항은 제외됩니다. 현재 서비스 DB에는 태그 컬럼이 없어 `import-db --dry-run`에서 `ml_warning_count`만 확인할 수 있습니다.

미해결 문항은 폐기하지 않고 `repair_queue.jsonl`에 남습니다. 같은 명령에 `--resume`을 붙이면 완료 checkpoint와 내용이 같은 평가 결과는 건너뛰고 실패 지점부터 계속합니다. 문항 내용이 바뀐 경우에는 동일 ID여도 해시가 달라져 반드시 다시 평가합니다.
각 평가 수리 직전의 target·feedback·request·response·backend는 문항 checkpoint의 `repair_history`에 누적됩니다.

근거 자체가 잘못된 문항은 검수한 basis와 실제 민백 chunk ID를 JSON으로 만든 뒤 해당 체크포인트만 다시 엽니다.

```powershell
.\.venv\Scripts\python.exe -m ai.question_generation.workflows.source_repair `
  --run-dir <batch-output-dir> `
  --overrides <reviewed-overrides.json>
```

이 명령은 chunk 존재 여부와 owner 일치를 확인하고 지정한 `material`, `correct`, `distractor:N` 및 필수 하위 의존성만 무효화합니다. 다른 민백 항목의 직접 보조 근거를 사람이 검수해 쓸 때만 override에 `supporting_article_ids`를 명시하며 `fact_owner`는 유지합니다. 근거를 추정하거나 기존 basis로 fallback하지 않으며, 이후 기존 배치 명령을 `--resume`으로 다시 실행해 생성·평가를 이어갑니다.

## Legacy

구형 경로와 일회성 보정 스크립트는 현행 import 경로 밖에 보관합니다.

```text
archive/question_generation_legacy_20260722/graph_path/
archive/question_generation_legacy_20260722/legacy/
archive/question_generation_legacy_20260722/_repair_closed_packs_20260721.py
archive/question_generation_legacy_20260722/_rebind_closed_pack_clues_tmp.py
```

보존 자료이며 현행 실행·테스트에서는 import하지 않습니다.

## Known Issue

- V41 SLLM이 일부 고유명사·명사를 반복적으로 잘못 치환하는 사례가 있습니다(`이고` -> `고도`, `낫` -> `나트`). 지문 길이, V41 `topic_type`/`distractor_type`, 원자적 fact basis를 각각 교정해도 재현됐습니다. 일회성 문자열 치환은 적용하지 않으며, 생성 후 의미 평가에서 차단하고 SLLM 출력 안정화 작업에서 별도로 다룹니다.

## Check

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s ai\question_generation\tests -p "test_*.py"
```

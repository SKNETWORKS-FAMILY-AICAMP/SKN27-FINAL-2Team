# Question Generation 코드 전수검사

- 검사일: 2026-07-22
- 범위: `ai/question_generation/` 전체
- 변경 여부: 검사 중 코드·DB·산출물 변경 없음
- 최종 판정: 구조 방향은 맞지만 대량 유료 생성은 아직 위험함

## 핵심 결함

### 1. 지문에 사용할 수 있는 근거가 없는 정답 후보

`core/contracts.py`는 `material_fact_semantically_distinct=true`이면 정답 근거와 지문 근거의 chunk 중복을 허용한다. 그러나 실제 지문 Gate는 정답 근거 chunk 사용을 무조건 금지한다.

최종 50 pack의 450 member를 모두 정답으로 회전해 검사한 결과:

```text
어댑터가 허용한 회전: 450/450
사용 가능한 지문 근거가 0개인 회전: 43
현재 50문항 초회 계획에 포함되는 불가능 문항: 5
```

Dry-run은 실제 지문 Gate를 호출하지 않기 때문에 이 문제를 발견하지 못한다.

관련 코드:

- `core/contracts.py:47-58`
- `core/contracts.py:112-134`
- `retrieval/closed_pack_input.py:141-151`
- `workflows/question_pipeline.py:164-176`

### 2. SLLM 중간 Gate가 의미 오류를 검사하지 않음

`correct_output_error()`와 `distractor_output_error()`는 출력이 비어 있는지만 확인한다. 따라서 다음 오류가 중간 Gate를 통과한다.

- 주체와 객체 뒤집힘
- 행위 방향 반전
- 사실 일부 누락
- 근거에 없는 인과관계 추가
- 고유명사 오독

실제 신규 E2E 3문항에서도 사람 재검수 결과는 1 PASS, 2 FAIL이었다.

관련 코드:

- `workflows/question_pipeline.py:281-295`
- `generation/sllm_inputs.py:50-63`

### 3. 정답·오답 재생성 시 평가 피드백이 폐기됨

평가기는 오류 부위와 수정 조언을 전달하지만 `invalidate()`는 피드백을 지문과 발문에만 보존한다. `correct`, `distractor:*`는 평가 사유 없이 초기화되므로 동일한 SLLM 입력을 다시 호출한다.

```text
선지 오류 발견
→ 해당 선지 초기화
→ 평가 피드백 폐기
→ 같은 근거와 같은 instruction 재호출
→ 같은 오류 반복 가능
```

현재 테스트도 오답 컴포넌트의 피드백이 빈 문자열인 동작을 정답으로 고정하고 있다.

관련 코드:

- `workflows/closed_pack_batch.py:324-337`
- `workflows/question_pipeline.py:433-461`
- `tests/test_pipeline.py:637-643`

### 4. 발문 선택기가 출제 관계축 계약을 보지 않음

GPT/SLLM 발문 선택기는 지문, 정답 선지, 두 발문 후보만 본다. 다음 계약은 선택 요청에 포함되지 않는다.

- `relation_axis_id`
- `stem_pattern`
- `question_task_instruction`

따라서 문장은 자연스럽지만 관계축이 틀린 발문을 선택할 수 있다.

관련 코드: `workflows/question_pipeline.py:311-377`

### 5. 평가 출력 정규화가 잘못된 응답을 PASS시킬 수 있음

다음처럼 잘못된 judge 응답을 넣어도 PASS 10점으로 정규화되는 것을 재현했다.

```text
선지 라벨: A, B, C, D, E
satisfies_stem_condition: 전부 maybe
historically_valid: 전부 maybe
결과: PASS / accept / 10점
```

예상 라벨, enum 값, 역사 사실성과 G5 결과의 일관성을 엄격하게 검증하지 않기 때문이다.

관련 코드: `evaluation/v18.py:309-363`

### 6. 모의고사 초회 계획은 항상 첫 owner를 정답으로 선택

`plan_variants(..., count=1)`은 seed와 관계없이 항상 `members[0]`을 정답으로 고른다. seed는 오답 조합만 변경한다. 따라서 첫 모의고사에서는 9개 member의 정답 회전이 실질적으로 작동하지 않는다.

관련 코드:

- `retrieval/closed_pack_input.py:37-83`
- `workflows/closed_pack_batch.py:379-391`

### 7. 중앙 입력 검증에서 evidence owner 불일치를 허용

직접 5-item 입력에서 item의 `article_id`와 evidence의 `article_id`가 달라도 `validate_pack()`이 통과한다. Closed-pack 어댑터에는 별도 검사가 있지만 직접 JSON 및 이미지 입력은 중앙 검증을 우회할 수 있다.

관련 코드:

- `core/contracts.py:16-59`
- `retrieval/image_pack_input.py:25-78`

### 8. 평가 배치의 중단 복구와 총시간 제한 부족

평가 JSONL은 실행할 때마다 `w` 모드로 다시 열리며, 배치의 평가 subprocess에는 전체 timeout이 없다. 후반 문항에서 실패하면 앞선 평가를 다시 호출할 수 있다.

관련 코드:

- `evaluation/v18.py:400-450`
- `workflows/closed_pack_batch.py:305-321`

## 과설계·중복

- `[done] graph_path/ + legacy/`: `archive/question_generation_legacy_20260722/`로 이동.
- `[done] _repair_closed_packs_20260721.py + _rebind_closed_pack_clues_tmp.py`: 같은 archive로 이동.
- `[done] generation/assemble.py`: 사용하지 않는 구형 파일 조립 CLI 제거.
- `[done] core/difficulty.py + generation/material_rules.py`: Graph 호환 전용 함수와 별칭 제거.
- `[done] compact/normalize_era_markers`: `core/text.py`로 통합.
- `[done] CallBudget threading.Lock`: 직렬 경로의 불필요한 lock 제거.
- `[done] retrieval/closed_pack_bank.py`: 미사용 `frames` 인자와 빈 `shortages` 제거.

Python 코드 11,568줄 중 최소 6,768줄, 약 58.5%가 현행 실행과 무관한 레거시 또는 일회성 코드다.

## 검증 결과

```text
가상환경 단위 테스트: 40/40 PASS
compileall: PASS
git diff --check: PASS
최종 pack 정답 회전 검사: 450개
계약상 생성 불가능하지만 통과한 회전: 43개
API 호출: 없음
DB 변경: 없음
```

시스템 Python에서는 `retrieval/closed_pack_bank.py`가 import 시점에 DB 드라이버까지 로드하여 테스트 수집이 실패했다. 프로젝트 가상환경에서는 40개 테스트가 모두 통과했다.

## 권장 수정 순서

1. 사용 가능한 지문 근거가 없는 43개 회전을 중앙 계약에서 차단
2. 정답·오답 생성 직후 basis 의미 보존 Gate 추가
3. 평가 피드백을 해당 SLLM 재생성 instruction에 전달
4. 평가기 출력 strict validation 및 문항별 resume 추가
5. 모의고사 정답 owner 시작 위치를 seed로 회전
6. 위 수정 후 신규 소량 E2E 사람 검수
7. 사람 검수 통과 전까지 50문항 유료 생성 보류

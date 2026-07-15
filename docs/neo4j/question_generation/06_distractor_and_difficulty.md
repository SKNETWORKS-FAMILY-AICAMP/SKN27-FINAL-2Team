# 06. 같은 경로의 오답 후보 검색과 난이도 제어

## 1. “같은 노드를 타는 다른 대상”의 정확한 정의

오답 후보는 단순히 정답과 같은 Category에 있는 엔터티가 아니다. 다음 네 조건을 모두 만족해야 한다.

1. 정답과 동일한 `PathPattern`을 만족한다.
2. 정답과 같은 슬롯 타입에 다른 Entity가 결합된다.
3. 후보가 다른 anchor에 대해서는 참이라는 승인 Fact와 근거를 가진다.
4. 현재 정답 문맥에서는 FALSE라는 결정론적 mismatch proof가 있다.

```text
정답 PathInstance
  김정희 --CREATED--> 세한도

후보 PathInstance
  정선   --CREATED--> 인왕제색도

공통 PathPattern
  Person --CREATED--> Work
```

같은 시대·같은 회화 Category라는 조건은 난이도와 순위를 조절한다. `CREATED` Fact를 대신하지는 않는다.

위 정의는 현재 문맥에서 FALSE인 `FALSE_ALTERNATIVE`를 찾는 경로다. selection rule별 전체 조립은 다음처럼 다르다.

| selection rule | 선택 option | 나머지 option | profile이 보장할 분포 |
|---|---|---|---|
| `SELECT_TRUE` | reference TRUE binding | 다른 anchor의 참인 Fact와 현재 문맥 mismatch proof가 있는 FALSE alternatives | TRUE 1, FALSE `choice_count-1` |
| `SELECT_FALSE` | mismatch proof가 있는 FALSE target 1개 | correct anchor에 대해 승인 Fact와 근거를 가진 TRUE companions | TRUE `choice_count-1`, FALSE 1 |

TRUE companion은 같은 correct anchor에 결합된 승인 PathInstance 또는 Blueprint가 허용한 보조 패턴에서 수집한다. 각 option shape이 같아야 하고, 공동 관계·중복·시간 범위를 다시 검증한다. SELECT_TRUE용 FALSE alternatives 여러 개를 SELECT_FALSE에 그대로 넣지 않는다.

## 2. 후보 검색의 두 단계

### 2.1 구조 후보

Neo4j에서 동일 PathPattern의 다른 PathInstance를 찾는다.

```text
correct path instance
  -> same PathPattern
  -> other approved PathInstances
  -> answer slot의 다른 Entity
```

### 2.2 오답 확정 후보

구조 후보마다 다음을 확인한다.

- 후보 answer와 정답 answer가 다른 canonical Entity인가
- 별칭, 병합 Entity, 전신·후신 중복이 아닌가
- 후보 PathInstance가 승인 Fact를 사용하는가
- 후보가 다른 anchor에 대해 참인 근거를 가지는가
- 현재 anchor에 대해서는 관계 불일치가 증명 가능한가
- 요구 시대·주제·표현 형식에 적합한가
- 후보 RAG 근거를 검색할 수 있는가

구조 후보를 곧바로 선지에 쓰지 않는다.

## 3. 열린 세계와 오답 증명

Neo4j에 다음 관계가 없다는 사실만으로는 역사적으로 거짓이라고 할 수 없다.

```cypher
NOT EXISTS {
  MATCH (김정희)-[:CREATED]->(인왕제색도)
}
```

이는 미적재 또는 미검토일 수 있다. 오답 확정에는 Predicate별 검증 방식이 필요하다.

| `validation_mode` | 오답 불일치 증명 |
|---|---|
| `FUNCTIONAL` | 정확한 Predicate·고정 역할 범위를 덮는 `CompletenessAssertion(mode=FUNCTIONAL)`의 유일 값과 후보가 다름 |
| `TEMPORAL_FUNCTIONAL` | 시간·장소까지 같은 범위를 덮는 FUNCTIONAL assertion의 유일 값과 후보가 다름 |
| `CLOSED_SET_REQUIRED` | `CompletenessAssertion(mode=CLOSED_SET)`이 보장하는 완전 목록에 현재 결합이 없음 |
| `DISJOINT_TIME` | 후보 Fact의 시간 범위가 문제 문맥과 확실히 불일치 |
| `DISJOINT_PLACE` | 후보 Fact의 장소 범위가 문제 문맥과 확실히 불일치 |
| `EXPLICIT_REFUTATION` | 현재 결합을 반박하는 승인 근거가 있음 |
| `ALTERNATE_OWNER_REVIEWED` | 다른 주체의 참인 Fact와 함께 해당 역할의 FUNCTIONAL 또는 CLOSED_SET assertion이 있음 |

공동 제작, 공동 참여, 여러 장소에서 발생한 사건처럼 다값이 가능한 관계는 다른 주체의 참인 Fact만으로 현재 주체와의 관계를 부정할 수 없다. `Fact.argument_completeness`는 Fact 한 건의 필수 역할 완성도일 뿐 전체 값의 폐쇄성을 뜻하지 않는다. 정확한 역할·시간·장소 범위를 덮는 승인 `CompletenessAssertion`, 확실한 시간·장소 불일치, 명시적 반박 중 하나가 없으면 Verdict는 `UNKNOWN`이다.

`EXPLICIT_REFUTATION`은 후보와 정확히 같은 Predicate·역할·시간·장소를 가진 `ACCEPTED NEGATIVE Fact`와 이를 `SUPPORTS`하는 승인 EvidenceRef를 요구한다. `REFUTES` 관계 하나나 생성 모델의 부정 문장은 proof로 사용하지 않는다.

### 3.1 폐쇄성 assertion의 범위

```text
CompletenessAssertion
  assertion_id
  mode = FUNCTIONAL | CLOSED_SET
  COVERS_PREDICATE -> Predicate
  COVERS_ROLE -> 교체하려는 역할
  BINDS_SCOPE {role_id} -> 고정된 다른 역할의 Entity/Literal
  VALID_DURING / IN_PLACE -> 선택적 문맥 범위
  EvidenceRef -SUPPORTS_COMPLETENESS-> assertion
```

validator는 후보의 correct context를 정규화해 `scope_hash`를 만들고 assertion의 범위와 정확히 일치하는지 확인한다. 더 넓거나 다른 시기의 assertion을 재사용하지 않는다.

## 4. 같은 패턴 후보 조회 Cypher

다음 쿼리는 `answer_mode=ENTITY`인 후보를 찾는 1차 구조 검색이다. `$swap_step_id`와 고정 슬롯은 활성 CandidatePolicy의 `SWAPS_AT` 관계에서 읽은 정답 결정 계약이며, 후보 집합은 정확한 snapshot·정책 조합의 `EligibilityProfile`에서만 가져온다. 최종 오답 확정은 쿼리 이후 Predicate별 validator가 수행한다.

```cypher
MATCH (eligibility:EligibilityProfile {eligibility_profile_id: $eligibility_profile_id})
      -[:FOR_CORRECT_PATH]->(correct:PathInstance)
MATCH (correct)-[:OF_PATTERN]->(pattern:PathPattern)
MATCH (eligibility)-[:FOR_BLUEPRINT]->(blueprint:QuestionBlueprint)
MATCH (eligibility)-[eligibleCandidate:HAS_VALIDATED_CANDIDATE]->(candidate:PathInstance)
MATCH (candidate)-[:OF_PATTERN]->(pattern)

MATCH (pattern)-[:HAS_SLOT]->(answerSlot:PatternSlot)
WHERE answerSlot.slot_key = pattern.answer_slot_key

MATCH (pattern)-[:HAS_STEP]->(answerStep:PatternStep {step_id: $swap_step_id})
MATCH (answerStep)-[answerRole:BINDS_ROLE]->(answerSlot)

MATCH (pattern)-[:HAS_SLOT]->(fixedSlot:PatternSlot {slot_key: $anchor_slot_key})
MATCH (answerStep)-[fixedRole:BINDS_ROLE]->(fixedSlot)

MATCH (correct)-[correctAnswerBinding:BINDS]->(correctAnswer:Entity)
WHERE correctAnswerBinding.slot_key = answerSlot.slot_key

MATCH (correct)-[correctAnchorBinding:BINDS]->(correctAnchor:Entity)
WHERE correctAnchorBinding.slot_key = $anchor_slot_key

MATCH (candidate)-[candidateAnswerBinding:BINDS]->(candidateAnswer:Entity)
WHERE candidateAnswerBinding.slot_key = answerSlot.slot_key

MATCH (candidate)-[candidateAnchorBinding:BINDS]->(candidateAnchor:Entity)
WHERE candidateAnchorBinding.slot_key = $anchor_slot_key

MATCH (candidate)-[factUse:USES_FACT]->(candidateFact:Fact)
WHERE factUse.step_id = answerStep.step_id
MATCH (evidence:EvidenceRef)-[:SUPPORTS]->(candidateFact)
WHERE eligibility.status = 'ELIGIBLE'
  AND eligibility.graph_snapshot_id = $graph_snapshot_id
  AND eligibility.choice_count = $choice_count
  AND eligibility.selection_rule = $selection_rule
  AND eligibility.polarity = $polarity
  AND eligibility.answer_mode = $answer_mode
  AND eligibility.modifier_fingerprint = $modifier_fingerprint
  AND eligibility.target_true_count = $target_true_count
  AND eligibility.target_false_count = $target_false_count
  AND eligibility.candidate_policy_version = $candidate_policy_version
  AND eligibility.difficulty_policy_version = $difficulty_policy_version
  AND eligibility.feature_policy_version = $feature_policy_version
  AND eligibility.validation_rule_version = $validation_rule_version
  AND eligibility.validator_version = $validator_version
  AND eligibleCandidate.truth_in_correct_context = 'FALSE'
  AND candidate <> correct
  AND candidate.structural_status = 'COMPILED'
  AND candidate.review_status = 'APPROVED'
  AND candidateAnswer.entity_id <> correctAnswer.entity_id
  AND candidateAnchor.entity_id <> correctAnchor.entity_id
  AND candidateFact.status = 'ACCEPTED'
  AND candidateFact.polarity = answerStep.expected_polarity
  AND evidence.review_status = 'ACCEPTED'

MATCH (candidate)-[:HAS_PATH_FEATURE]->(pathFeature:PathFeatureProfile)
WHERE pathFeature.graph_snapshot_id = $graph_snapshot_id
  AND pathFeature.feature_policy_version = $feature_policy_version
  AND pathFeature.compiler_version = $compiler_version
  AND pathFeature.status = 'ACTIVE'

RETURN DISTINCT
  pattern.pattern_id AS pattern_id,
  blueprint.blueprint_id AS question_blueprint_id,
  answerStep.step_id AS swap_step_id,
  answerSlot.slot_key AS swap_slot,
  answerRole.role_id AS answer_role_id,
  fixedRole.role_id AS fixed_role_id,
  candidate.path_instance_id AS candidate_path_instance_id,
  candidateAnswer.entity_id AS candidate_answer_id,
  candidateAnswer.canonical_name AS candidate_answer,
  candidateAnchor.entity_id AS true_anchor_id,
  candidateAnchor.canonical_name AS true_anchor,
  candidateFact.fact_id AS true_fact_id,
  collect(DISTINCT evidence.chunk_id) AS evidence_chunk_ids,
  properties(pathFeature) AS candidate_path_features,
  eligibleCandidate.validation_result_id AS validation_result_id,
  eligibleCandidate.truth_in_correct_context AS truth_in_correct_context,
  eligibleCandidate.option_role AS option_role,
  eligibleCandidate.candidate_fit_score AS candidate_fit_score

ORDER BY candidate_path_instance_id

LIMIT $candidate_pool_limit;
```

모든 입력값과 제한값은 정책 또는 호출 파라미터에서 받는다. 시대명, 관계 타입 목록, 난이도 경계를 쿼리 문자열에 직접 넣지 않는다. Neo4j는 구조 후보와 정적 특징만 반환하며, 정답-후보 쌍의 `CandidateFit`과 전체 문항 난이도는 서비스가 계산한다.

쿼리는 `answer_role_id`와 `fixed_role_id`를 모두 반환한다. ROLE mismatch의 실제 `failed_role_id`는 선택된 CompletenessAssertion의 `COVERS_ROLE` 또는 ValidationRule을 확인한 뒤 validator가 정한다. 예를 들어 작품 기준 creator가 단일임을 증명할 때와 인물 기준 작품 완전 목록을 증명할 때 실패 역할의 방향이 다르다. n-ary 패턴은 CandidatePolicy가 지정한 고정 슬롯 목록과 role 목록 전체를 같은 방식으로 반환한다.

이 쿼리는 FALSE option 전용이다. `SELECT_FALSE`의 TRUE companion 조회는 동일 EligibilityProfile에서 `truth_in_correct_context='TRUE'`인 관계만 읽고, CandidatePolicy가 허용한 패턴과 correct anchor 바인딩을 검증한다. 두 결과를 합친 뒤 `target_truth_distribution`과 유일한 `SELECTED_TARGET`을 확인한다.

## 5. 후보 카드의 최종 검증

1차 조회 결과마다 validator가 다음 `ValidationResult`를 만든다.

```json
{
  "candidate_path_instance_id": "path-instance:uuid",
  "correct_context": {
    "anchor_entity_id": "entity:person:kim-jeonghui",
    "answer_slot": "answer_work"
  },
  "candidate_true_fact_id": "fact:jeong-seon-created-inwang",
  "mismatch_proof": {
    "mode": "FUNCTIONAL",
    "mismatch_kind": "ROLE",
    "failed_role_id": "creator",
    "failed_constraint_id": null,
    "context_scope": {},
    "completeness_assertion_id": "closure:inwangjesaekdo:creator:v1",
    "scope_hash": "sha256:...",
    "proof_fact_ids": ["fact:..."],
    "proof_chunk_ids": ["chunk:..."]
  },
  "truth_in_correct_context": "FALSE",
  "validation_status": "APPROVED",
  "validator_version": "candidate-validator-v1"
}
```

`UNKNOWN`은 FALSE로 바꾸지 않고 후보에서 제외한다.

`mismatch_kind`는 `ROLE`, `TIME`, `PLACE`, `REFUTATION` 중 하나다. `failed_role_id`는 ROLE일 때만 필수이고, 시간·장소·직접 반박은 `failed_constraint_id`와 `context_scope`로 범위를 기록한다.

## 6. Raw EDA에서 확인한 후보 풀

아래 수치는 같은 타입·시대·분류의 구조 후보가 정답 포함 5개 이상 존재하는 비율이다. 승인 Fact와 불일치 검증까지 통과한 최종 생성 커버리지는 별도로 측정해야 한다.

| 구조 후보 조건 | 풀 5개 이상인 대상 비율 |
|---|---:|
| AKS `primaryType + era` 동일 | 98.67% |
| AKS `field + primaryType + era` 동일 | 93.75% |
| 시소러스 동일 최하위 `term_lk` | 99.73% |
| 시소러스 동일 `term_lk + term_times` | 95.19% |
| ITKC 동일 사건에 연결된 인물 | 93.25% |
| ITKC 동일 인물에 연결된 사건 | 40.19% |
| ITKC 동일 인물·동일 관계어의 대상 | 10.48% |
| ITKC 동일 시대·사건분류의 사건 | 86.40% |

시소러스와 AKS의 분류 풀은 후보의 모양과 난이도 조절에 유리하다. 그러나 의미 관계가 없는 분류 이웃만으로 오답을 확정하면 안 된다. 최종 후보는 반드시 동일 PathPattern과 승인 Fact를 가져야 한다.

ITKC의 `동일 인물·동일 관계어`만으로 4개 오답을 확보할 수 있는 비율은 낮다. 이 패턴은 보조 유형으로 사용하고, 후보가 부족하면 관계 조건을 임의 완화하지 말고 다른 QuestionType을 선택한다.

## 7. 난이도별 후보 거리

| 특징 | 쉬움 | 보통 | 어려움 |
|---|---|---|---|
| 시대 거리 | 다른 큰 시대 | 같은 대시대·다른 세부 시기 | 같은 세부 시대·가까운 연도 |
| Category 거리 | 다른 하위 분류 | 같은 상위 분류 | 같은 leaf Category |
| Entity 타입 | 같은 상위 타입 | 같은 하위 타입 | 같은 하위 타입·같은 역할 |
| 실제 anchor 유사도 | 낮음 | 중간 | 높음 |
| 이름·표현 유사도 | 낮음 | 중간 | 높음, 단 동일 Entity 제외 |
| 시간 연산 | 없음 | 한 번 | 복수 구간 비교 |
| 단서 방식 | 직접 | 일부 간접 | 복수 간접 단서 |
| 오답 간 미세 차이 | 큼 | 중간 | 한 역할·시점만 다름 |

난이도는 오답 후보만으로 결정되지 않는다. 지문 단서와 발문 연산을 함께 반영한다.

## 8. 난이도 특징의 세 단계

난이도 값은 계산 시점에 따라 분리한다.

| 객체 | 계산 시점 | 특징 예 | 저장 위치 |
|---|---|---|---|
| `PathFeatureProfile` | PathInstance 컴파일 시 | `path_length`, Fact 수, 시간 정밀도, source grade, `answer_obscurity` | Neo4j 파생 캐시 |
| `CandidateFit` | 정답-후보 쌍 평가 시 | taxonomy·시대·역할·이름 거리, 실제 anchor 유사도 | 생성 작업 스냅샷 |
| `QuestionDifficultySnapshot` | 지문·후보 세트 확정 뒤 | `required_clue_count`, `anchor_visibility_score`, `clue_indirectness`, `operand_count`, `temporal_reasoning_cost`, `distractor_similarity_mean`, `visual_complexity`, `negation_cost` | 운영·생성 저장소 |

후보 PathInstance 하나의 점수만으로 requested band를 판정하지 않는다. 정적 경로 특징과 모든 `CandidateFit`으로 후보 세트를 먼저 구성하고, 지문 생성 뒤 실현된 단서 수와 표현 비용을 포함해 최종 점수를 다시 검증한다. 각 특징의 가중치와 EASY/MEDIUM/HARD 경계는 `DifficultyPolicy`가 버전별로 관리하며 코드 상수나 Cypher 숫자로 고정하지 않는다.

### 정답 유일성 기반 난이도 특징

단서를 하나씩 적용했을 때 남는 후보 수를 측정할 수 있다.

```text
초기 후보: 같은 타입의 모든 Entity
  -> 시대 단서 적용
  -> 관계 단서 적용
  -> 작품·장소·활동 단서 적용
  -> 최종 후보 1개
```

정답을 유일하게 만드는 데 필요한 최소 단서 수 `min_clues_to_unique`가 크고, 남은 후보들이 구조적으로 가까울수록 난도가 높다. 최종 후보 수는 난이도와 무관하게 항상 1이어야 한다.

## 9. 가중 무작위 선택

난수 선택은 다음 순서로 수행한다.

1. correct PathInstance에 연결 가능한 QuestionBlueprint·QuestionType만 남긴다.
2. `choice_count`, CandidatePolicy와 DifficultyPolicy 버전이 일치하는 `EligibilityProfile`을 조회한다.
3. profile이 없거나 STALE이면 해당 조합을 제외하고 오프라인 재컴파일 대상으로 기록한다.
4. 필수 오답 수와 requested DifficultyBand를 충족하는 조합만 남긴다.
5. 세트 내 시대·주제·유형·modifier 중복 패널티를 계산한다.
6. 정책 weight를 정규화하고 기록된 random seed로 한 조합을 선택한다.

동일 요청을 재현할 수 있도록 `random_seed`, 후보 ID 목록, 정책 버전, 최종 선택 ID를 GenerationRun에 기록한다.

## 10. 후보별 RAG 검색

RAG에는 “왜 이게 틀렸는가”를 먼저 묻지 않는다. 후보가 다른 문맥에서 참인 Fact를 검색한다.

```json
{
  "purpose": "FALSE_OPTION_TRUE_CONTEXT",
  "option_role": "FALSE_ALTERNATIVE",
  "true_fact_id": "fact:jeong-seon-created-inwang",
  "predicate_id": "CREATED",
  "argument_bindings": [
    {"role_id": "creator", "entity_id": "entity:person:jeong-seon"},
    {"role_id": "work", "entity_id": "entity:work:inwangjesaekdo"}
  ],
  "required_source_grade": "AUTHORITATIVE_REFERENCE"
}
```

검색된 근거로 다음 두 문장을 구분할 수 있어야 한다.

```text
참인 설명: 인왕제색도는 정선의 작품이다.
현재 문맥의 불일치: 김정희의 작품을 묻는 문항에는 해당하지 않는다.
```

## 11. 후보 선택 시 추가 제약

- 같은 canonical Entity의 별칭을 서로 다른 선지로 사용하지 않는다.
- 동일한 실제 anchor에서 나온 후보만 반복 선택하지 않는다.
- 정답보다 유난히 긴 이름이나 문장만 모이지 않도록 표현 길이를 후처리한다.
- 후보의 RAG 근거가 정답 지문에 섞이지 않게 evidence block을 분리한다.
- 공동 저작·공동 참여의 가능성이 있는 후보는 범위가 정확히 일치하는 승인 CompletenessAssertion 없이는 제외한다.
- 시간형에서는 겹치는 시간 범위를 UNKNOWN으로 처리한다.
- 부정형에서는 역사적 FALSE와 시험 정답을 별도 필드로 계산한다.
- 후보가 부족하면 난이도를 낮추거나 유형을 다시 선택하고, sLLM이 새 후보를 만들게 하지 않는다.

## 12. 응답 데이터 이후의 난이도 보정

초기 predicted score는 학습자 풀이 데이터가 쌓이면 실측치로 보정한다.

```text
measured_difficulty
discrimination
guessing
response_count
standard_error
calibration_model_version
```

배점과 실측 난이도는 분리한다. 기존 시험의 1·2·3점은 cold-start 학습 특징으로 사용할 수 있지만 고정 난이도 라벨로 사용하지 않는다.

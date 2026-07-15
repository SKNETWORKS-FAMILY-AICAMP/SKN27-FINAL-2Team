# 03. Neo4j·RAG·문제 재료의 책임 분리

## 1. 설계의 출발점

문제 생성은 다음 세 종류의 데이터를 섞지 않아야 한다.

1. 역사적으로 검증된 지식
2. 검색 가능한 긴 근거 본문
3. 한 번의 생성 작업을 위해 조립된 문제 재료

이들을 모두 Neo4j에 넣으면 본문 중복, 임베딩 관리, 생성 이력, 사실 수정의 책임이 뒤섞인다. 반대로 Neo4j에 이름과 `RELATED_TO`만 넣으면 같은 의미 경로의 오답 후보를 안전하게 찾을 수 없다.

## 2. Neo4j 책임

| 데이터 | Neo4j 저장 방식 |
|---|---|
| canonical 인물·사건·기관·작품·장소 | 주 저장 |
| 별칭·한자명·출처별 ID | 주 저장 |
| 분류·시대·시간 구간 | 주 저장 |
| 승인된 역사 Fact와 역할 | 주 저장 |
| 원문 본문·청크 | 청크 ID만 저장 |
| 임베딩 | 저장하지 않음 |
| 이미지 URI·권리·묘사 대상 | `MediaAsset` 메타데이터만 저장 |
| 이미지 바이너리 | 저장하지 않음 |
| 생성용 PathPattern·호환성 정책 | 주 저장 |
| 문제 키워드·발문의도·정답 바인딩 | 저장하지 않음 |
| API가 생성한 지문 | 저장하지 않음 |
| sLLM 최종 문항·선지·해설 | 저장하지 않음 |
| 학습자 응답·정답률·풀이 시간 | 집계 난이도만 선택적으로 반영 |

### Neo4j에 긴 본문을 넣지 않는 이유

- 같은 본문이 여러 Fact와 후보에서 중복된다.
- 청크 재분할과 임베딩 모델 변경이 그래프 재적재로 이어진다.
- 후보 경로 탐색에 수천 자의 본문은 필요하지 않다.
- RAG의 전문 검색·벡터 검색 인덱스가 본문 검색에 더 적합하다.

Neo4j에는 다음처럼 근거 참조만 둔다.

```text
(EvidenceRef {
  chunk_id,
  document_id,
  chunk_content_hash,
  corpus_version,
  chunker_version,
  source_grade,
  review_status,
  excerpt_hash
})-[:SUPPORTS]->(Fact)
```

현재 RAG 구조에서는 `EvidenceRef.chunk_id`가 PostgreSQL `rag.document_chunks.chunk_id`를 가리키도록 맞춘다. 청크 저장 구조는 [PostgreSQL RAG 스키마 설계](../../postgresql/postgresql_rag_schema_design.md)를 기준으로 하되, 재청킹에 대비해 `document_id`, `content_hash`, `corpus_version`, `chunker_version`도 함께 검증한다.

## 3. 문제 재료는 두 단계로 만든다

초기 입력에 키워드와 발문의도만 있다면 아직 정답 근거를 검색할 수 없다. RAG가 정답까지 추측하게 되면 “정답 생성 금지” 원칙을 깨게 된다. 따라서 재료를 `MaterialSeed`와 `QuestionMaterial`로 분리한다.

### 3.1 MaterialSeed

```json
{
  "material_seed_id": "seed-uuid",
  "keyword": "김정희",
  "keyword_entity_id": "entity:person:kim-jeonghui",
  "stem_intent_mode": "EXPLICIT",
  "stem_intent_id": "SELECT_ASSOCIATED",
  "scope_filters": {
    "era_ids": ["era:late-joseon"],
    "category_ids": ["category:culture"],
    "concept_ids": [],
    "display_labels": ["조선 후기", "문화"]
  },
  "optional_selected_answer_expression": null,
  "set_context": null,
  "random_seed": 184027
}
```

`keyword_entity_id`는 문자열 검색으로 찾은 후보 중 엔터티 해소가 끝난 ID다. 동명이인이면 MaterialSeed 생성 단계에서 중단한다. `stem_intent_mode=EXPLICIT`이면 `stem_intent_id`가 필수이고, `AUTO`이면 호환 intent 중 하나를 정책 추첨한다. `optional_selected_answer_expression`이 있으면 selection rule의 truth와 승인 Fact 또는 mismatch proof를 만족하는지 검증한다. `set_context`에는 세트 ID, 이미 사용한 anchor·유형·modifier·정답 위치 분포와 세트 정책 버전을 선택적으로 전달한다.

### 3.2 정답 바인딩 확정

MaterialSeed로 다음을 조회한다.

```text
keyword entity
  -> 사용할 수 있는 QuestionBlueprint
  -> 주·보조 PathPattern
  -> 승인된 PathInstance
  -> answer 슬롯의 Bindable 또는 복합 OptionBinding
```

예를 들어 다음 PathInstance가 선택된다.

```text
pattern = PERSON_CREATED_WORK
anchor  = 김정희
answer  = 세한도
fact    = 김정희 CREATED 세한도
```

이 단계는 그래프의 승인 Fact에서 정답을 선택하는 과정이며 LLM 생성이 아니다.

### 3.3 QuestionMaterial

```json
{
  "material_id": "material-uuid",
  "material_seed_id": "seed-uuid",
  "graph_snapshot_id": "graph-snapshot-id",
  "keyword_ids": ["entity:person:kim-jeonghui"],
  "stem_intent_mode": "EXPLICIT",
  "stem_intent_id": "SELECT_ASSOCIATED",
  "question_type_id": "ACTOR_ACTIVITY",
  "composition_mode_id": "SINGLE_PATH",
  "question_blueprint_id": "blueprint:actor-created-work:v1",
  "eligibility_profile_id": "eligibility:uuid",
  "selection_rule": "SELECT_TRUE",
  "target_truth_distribution": {"TRUE": 1, "FALSE": 4},
  "difficulty_band_id": "MEDIUM",
  "modifiers": {
    "source_mode": "PRIMARY_TEXT",
    "anchor_visibility": "IMPLICIT",
    "temporal_mode": "NONE",
    "answer_mode": "ENTITY",
    "polarity": "POSITIVE",
    "anchor_count": 1
  },
  "primary_path_pattern_id": "PERSON_CREATED_WORK_V1",
  "primary_path_instance_id": "path-instance:uuid",
  "supporting_path_instance_ids": [],
  "reference_binding": {
    "binding_id": "option-binding:reference",
    "answer_mode": "ENTITY",
    "entity_ids": ["entity:work:sehando"],
    "fact_ids": ["fact:uuid"]
  },
  "selected_answer_binding_id": "option-binding:reference",
  "reference_evidence": [],
  "option_candidates": [],
  "difficulty_selection_snapshot": {
    "path_feature_profile_id": "path-feature:uuid",
    "candidate_fit_ids": [],
    "preflight_predicted_score": 0.52,
    "feature_values": {},
    "policy_version": "difficulty-v1"
  },
  "set_context_snapshot": null,
  "policy_versions": {
    "entity_resolution": "er-2026-07",
    "candidate_policy": "candidate-v1",
    "difficulty_policy": "difficulty-v1"
  }
}
```

QuestionMaterial은 운영 DB 또는 생성 작업 저장소에 둔다. Neo4j에는 동일 내용을 매번 영구 노드로 만들지 않는다. 재현에 필요한 그래프 ID와 정책 버전만 기록한다.

`reference_binding`과 모든 option candidate는 다음 `OptionBinding` 합집합 구조다. 모든 하위 항목은 모델이 다시 식별하지 않도록 불변 ID로 고정한다.

| `answer_mode` | 필수 바인딩 | 검증 단위 |
|---|---|---|
| `ENTITY` | `entity_ids`, 지지 `fact_ids` | 하나의 canonical Entity와 해당 역할 Fact |
| `STATEMENT` | `claim_bindings[{claim_id, fact_ids, expected_truth}]` | 하위 주장별 Verdict와 근거 |
| `IMAGE` | `media_ids`, `depicted_entity_ids` | MediaAsset 권리·묘사 대상·답 노출 상태 |
| `SEQUENCE` | `ordered_operand_ids`, 각 operand의 `time_span_id` | 모든 인접·필요 사건 쌍의 확정 가능한 선후 |
| `MATCH_SET` | `pairs[{left_binding_id, right_binding_id, fact_ids}]` | pair마다 독립적인 TRUE/FALSE/UNKNOWN |

`QuestionBlueprint`가 주 답 경로와 보조 단서 경로의 조립 방법을 정한다. `reference_binding`은 anchor·지문을 구성하는 승인 TRUE 경로이고, `selected_answer_binding_id`는 selection rule을 적용해 시험에서 선택할 option이다. `SELECT_TRUE`에서는 두 ID가 같을 수 있지만, `SELECT_FALSE`에서는 mismatch proof를 가진 유일한 FALSE option이 선택되므로 다르다. MVP는 `ENTITY`부터 활성화하더라도 계약은 나머지 형식을 Entity 문자열로 억지 축소하지 않는다.

## 4. 유형과 난이도 선택 시점

유형과 난이도는 무작위지만 완전 독립 난수가 아니다. 지문 형식과 검색 근거가 유형에 따라 달라지므로 지문 생성 전에 선택해야 한다.

권장 순서는 다음과 같다.

```text
MaterialSeed
  -> 정답 QuestionBlueprint와 주 PathInstance 확정
  -> 생성 가능한 QuestionType·CompositionMode 후보 계산
  -> 생성 가능한 DifficultyBand 후보 계산
  -> choice count와 후보 proof를 충족하는 EligibilityProfile만 유지
  -> 가중 무작위 선택
  -> 유형에 맞는 정답 근거 RAG 검색
  -> 지문 생성
```

예를 들어 이미지가 없는데 `IMAGE` modifier를 선택하거나, 정확한 날짜가 없는데 `CHRONOLOGY_ORDER`를 선택하는 일을 eligibility 계산에서 차단한다.

## 5. reference·선택 답 근거 RAG 계약

RAG 검색 입력은 자유 문장 하나보다 구조화된 쿼리가 안전하다.

```json
{
  "purpose": "REFERENCE_EVIDENCE",
  "fact_id": "fact:uuid",
  "predicate_id": "CREATED",
  "argument_bindings": [
    {
      "role_id": "creator",
      "entity_id": "entity:person:kim-jeonghui",
      "canonical_name": "김정희",
      "aliases": ["추사", "완당"]
    },
    {
      "role_id": "work",
      "entity_id": "entity:work:sehando",
      "canonical_name": "세한도"
    }
  ],
  "time_scope": {
    "time_span_id": "timespan:1844",
    "earliest_start": 1844,
    "latest_start": 1844,
    "earliest_end": 1844,
    "latest_end": 1844,
    "precision": "YEAR"
  },
  "required_roles": ["creator", "work"],
  "allowed_source_grades": ["AUTHORITATIVE_REFERENCE", "PRIMARY_SOURCE"]
}
```

RAG 응답은 다음 조건을 만족해야 한다.

```json
{
  "query_id": "rag-query-uuid",
  "evidence_cards": [
    {
      "chunk_id": "chunk:aks:E...:12",
      "document_id": "aks:E...",
      "chunk_content_hash": "sha256:...",
      "corpus_version": "aks-corpus-v1",
      "chunker_version": "chunker-v1",
      "source_grade": "AUTHORITATIVE_REFERENCE",
      "supports_fact_id": "fact:uuid",
      "support_roles": ["creator", "work"],
      "retrieval_score": 0.91,
      "review_status": "ACCEPTED"
    }
  ]
}
```

검색 우선순위는 `Fact에 연결된 EvidenceRef.chunk_id 정확 조회 → 같은 document_id 내부 검색 → 구조화된 하이브리드 검색`이다. exact 조회에서도 `chunk_content_hash`, `corpus_version`, `chunker_version`을 승인 당시 EvidenceRef와 대조한다. 이미 승인된 근거가 있는데 매번 전체 코퍼스에서 새 근거를 선택하면 생성 결과가 불안정해진다. 재검색으로 더 좋은 청크를 발견해도 런타임이 EvidenceRef를 직접 갱신하지 않고 검수 큐로 보낸다.

검색 점수만 높다고 사실을 승인하지 않는다. `supports_fact_id`가 맞는지, 필요한 모든 역할이 근거 문장에 실제로 들어 있는지 확인한다. 조약처럼 n-ary인 Fact는 `basis`, `grantor`, `beneficiary`, `right`, `scope`를 생략하지 않고 역할별 evidence span을 요구한다.

`SELECT_TRUE`에서는 reference TRUE Fact가 선택 답의 근거이기도 하다. `SELECT_FALSE`에서는 reference와 TRUE companion 각각의 승인 근거에 더해, 선택된 FALSE option의 다른 문맥 TRUE Fact와 현재 문맥 mismatch proof까지 답 근거로 묶는다.

## 6. 지문 생성 API의 책임

지문 생성 API는 다음 입력만 표현한다.

- 문제 키워드와 발문의도
- 선택된 QuestionType과 DifficultyBand
- anchor를 식별할 수 있는 승인 근거
- 정답 Fact의 근거
- 허용된 표현 형식

API에 허용되는 출력은 `stimulus_blocks`, `used_chunk_ids`다. 정답, 선지, 해설, 새로운 역사 사실은 출력 대상이 아니다. MVP는 `TEXT` 블록만 활성화하지만 계약은 `MEDIA_REF`, `TABLE`, `MAP`, `TIMELINE`을 지원한다.

```json
{
  "stimulus_blocks": [
    {
      "block_id": "stimulus-block:1",
      "block_type": "TEXT",
      "text": "...",
      "clue_spans": [
        {"start": 15, "end": 27, "source_chunk_id": "chunk:..."}
      ]
    }
  ],
  "used_chunk_ids": ["chunk:..."],
  "answer_leak_detected": false
}
```

지문에는 다음 내용을 넣지 않는다.

- 정답 표준명 또는 정답만의 고유 별칭
- 근거에 없는 연도·인물·사건
- 선택지 후보를 평가하는 표현
- “정답은” 같은 메타 문장

## 7. option 후보 카드 계약

아래는 `SELECT_TRUE`에서 비선택 FALSE option으로 사용할 후보 카드다. Neo4j 조회 결과는 이름 목록이 아니라 후보가 다른 문맥에서 참이라는 Fact와 현재 문맥 mismatch proof까지 포함해야 한다.

```json
{
  "option_binding": {
    "binding_id": "option-binding:distractor:1",
    "answer_mode": "ENTITY",
    "entity_ids": ["entity:work:inwangjesaekdo"],
    "fact_ids": ["fact:jeong-seon-created-inwang"]
  },
  "candidate_display_name": "인왕제색도",
  "path_pattern_id": "PERSON_CREATED_WORK_V1",
  "candidate_path_instance_id": "path-instance:other-uuid",
  "option_role": "FALSE_ALTERNATIVE",
  "truth_in_question_context": "FALSE",
  "true_anchor_entity_id": "entity:person:jeong-seon",
  "true_fact_id": "fact:jeong-seon-created-inwang",
  "swap_slot": "answer_work",
  "mismatch_proof": {
    "validation_mode": "FUNCTIONAL",
    "mismatch_kind": "ROLE",
    "failed_role_id": "creator",
    "failed_constraint_id": null,
    "context_scope": {},
    "completeness_assertion_id": "closure:inwangjesaekdo:creator:v1",
    "proof_fact_ids": ["fact:jeong-seon-created-inwang"],
    "proof_chunk_ids": ["chunk:distractor:1"],
    "validator_version": "candidate-validator-v1"
  },
  "candidate_fit_features": {
    "same_era": true,
    "same_entity_type": true,
    "taxonomy_distance": 1,
    "temporal_distance": 0
  },
  "evidence_query": {
    "purpose": "FALSE_OPTION_TRUE_CONTEXT",
    "option_role": "FALSE_ALTERNATIVE",
    "fact_id": "fact:jeong-seon-created-inwang"
  }
}
```

후보 RAG는 `인왕제색도`가 정선의 작품이라는 참인 근거를 찾는다. 근거가 없거나 현재 anchor에도 성립할 가능성을 배제하지 못하면 후보를 폐기한다. `SELECT_FALSE`는 이 FALSE 후보를 정확히 하나만 선택 대상으로 두고, 나머지는 correct anchor에 대해 참인 companion OptionBinding과 승인 근거로 채운다.

RAG purpose는 역할별 gate를 고정한다.

| purpose | option 역할 | 필수 검증 |
|---|---|---|
| `REFERENCE_EVIDENCE` | reference | reference Fact의 모든 역할 |
| `FALSE_OPTION_TRUE_CONTEXT` | FALSE_ALTERNATIVE/selected FALSE | 후보가 다른 true context에서 성립하는 Fact |
| `TRUE_COMPANION_EVIDENCE` | TRUE_COMPANION | correct anchor 문맥에서 성립하는 Fact |
| `MISMATCH_PROOF_EVIDENCE` | FALSE option proof | CompletenessAssertion, 시간·장소 제약 또는 ACCEPTED NEGATIVE Fact의 exact EvidenceRef |

이 카드는 `ENTITY` 예시다. `STATEMENT`는 하위 claim 하나를 다른 승인 Fact 바인딩으로 교체하고 모든 claim의 Verdict를 저장한다. `SEQUENCE`는 동일한 검증 대상 operand를 순열로 만들되 TimeSpan 비교로 정답 순서가 하나임을 보장한다. `MATCH_SET`은 승인된 pair 집합에서 교체 슬롯 하나를 바꾸고 모든 pair를 다시 판정한다. `IMAGE`는 권리와 `DEPICTS` 검토를 통과한 media ID를 교체한다. 어느 형식에서도 sLLM이 새 operand나 pair를 만들지 않는다.

## 8. 최종 sLLM 입력 번들

sLLM에는 검색이나 사실 선택 권한을 주지 않는다. 입력은 이미 확정된 다음 블록이다.

```text
GENERATION_POLICY
QUESTION_TYPE_AND_INTENT
STIMULUS
REFERENCE_FACT_CARD
OPTION_BINDINGS_AND_VERDICTS
SOURCE_PROVENANCE
OUTPUT_SCHEMA
```

sLLM의 역할은 다음으로 제한한다.

- 발문을 선택된 StemIntent에 맞게 작성
- 정답과 오답 후보를 문법적으로 같은 형태로 표현
- 선지 길이와 문체를 균형 있게 조정
- 후보별 참인 근거를 사용해 해설 초안을 작성

최종 정답 위치는 sLLM이 결정하지 않는다. 서버가 선택지 순서를 섞고 불변 `binding_id`와 selection rule을 기준으로 정답 위치를 계산한다.

## 9. 출처의 등급

| 등급 | 사용 예 | Fact 승인 |
|---|---|---|
| `PRIMARY_SOURCE` | 사료 원문 | 해석 검토와 함께 가능 |
| `AUTHORITATIVE_REFERENCE` | 대백과사전·공식 역사 자료 | 기본 승인 후보 |
| `STRUCTURED_RELATION_SOURCE` | 고전종합DB 관계망 | 관계 역할이 충분할 때 가능 |
| `THESAURUS_METADATA` | 시대·분류·용어 정의 | 분류·시간 보조 |
| `SECONDARY_COMMENTARY` | 일반 해설 | 보조 근거 |
| `AI_GENERATED_COMMENTARY` | AI 생성 해설 | 단독 승인 금지 |

근거 강도는 난이도가 아니다. 근거가 약한 후보를 어려운 문제로 분류하지 않고 생성 대상에서 제외한다.

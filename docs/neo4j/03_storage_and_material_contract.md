# 03. 저장소 책임과 문제 생성 계약

> 계약 버전: `QG-MATERIAL-V1-DRAFT`
> 기준일: 2026-07-16

## 1. 저장소별 단일 책임

| 영역 | 저장하는 것 | 저장하지 않는 것 |
|---|---|---|
| Neo4j | canonical target, donor 분류, 검증 Fact, RAG 근거 참조, QuestionUse revision | 본문·임베딩·생성 지문·사용자 답 |
| RAG PostgreSQL | 문서, 청크, 임베딩, source/corpus version | donor 자격·정답키 |
| 정책 레지스트리 | Facet 컴파일 규칙, 발문의도, 문제 유형, 난이도, mismatch rule, 교육과정 분류 binding | 역사 Fact 원문 |
| 운영 PostgreSQL | 생성 작업·문항·option provenance, 정답 token, proof, 응답·채점 | 역사 지식 그래프 |
| 생성 모델 | 저장소가 아니라 고정 재료의 표현 계층 | 사실·정답·후보 ID 결정 |

Neo4j의 `EvidenceSpan`은 본문을 복사하는 노드가 아니다. `document_id`, `chunk_id`,
offset, hash, corpus version을 보존해 RAG 행을 정확히 가리킨다.

`Candidate`는 저장 노드나 영속 역할이 아니다. 후보 조회 중 다른
`CanonicalEntity:QuestionTarget`에 붙이는 런타임 변수명이다. 후보가 option 재료로
선택된 뒤에는 “자신에게 참인 Fact를 제공한 대상”이라는 뜻의 `donor`로 부른다.
Neo4j나 운영 DB에 별도 `Candidate` 노드를 만들지 않는다.

## 2. ID와 revision 불변식

논리 ID와 revision ID의 의미를 분리한다.

| 종류 | 의미 | 예 |
|---|---|---|
| 논리 ID | 버전이 바뀌어도 같은 개념임을 나타내는 식별자 | `question_use_id`, `facet_id`, `fact_id` |
| revision ID | 특정 계약·검토·snapshot에서 불변인 구현 식별자 | `question_use_revision_id`, `facet_revision_id`, `fact_revision_id` |
| snapshot ID | 함께 조회해도 되는 revision 집합 | `graph_snapshot_id` |

서비스 경계를 넘는 모든 Graph DTO는 `graph_snapshot_id`와 자신이 참조하는 모든
`*_revision_id`를 함께 가진다. 논리 ID만 전달해 현재 active revision을 다시 찾는 행위는
금지한다. 최소 고정 대상은 다음과 같다.

```text
graph_snapshot_id
topic_type_revision_id
question_use_revision_id
facet_revision_id
parent_semantic_class_revision_id
fact_revision_id
predicate_revision_id
evidence_span_revision_id
```

정책 레지스트리의 revision도 같은 원칙을 따른다.

```text
policy_bundle_revision_id
question_classification_binding_revision_id
mismatch_rule_id + mismatch_rule_version
```

한 생성 작업 안에서 revision 하나라도 다른 `graph_snapshot_id` 또는 policy bundle에
속하면 조합하지 않는다. revision이 바뀌면 기존 DTO를 보정하지 않고 새 생성 시도로
시작한다.

### 2.1 source Fact binding의 단일 직렬화

`source_fact_binding_hash`는 Neo4j 노드 속성이 아니다. Graph repository adapter가 pinned
`Fact` revision의 endpoint·typed value·qualifier를 아래 **유일한** payload로 조립한 뒤
계산해 경계 DTO에 넣는다. 이후 RAG, proof, option provenance는 이 값을 그대로 전달하며
다시 계산할 때도 같은 규칙을 사용한다.

```json
{
  "binding_schema_version": "source-fact-binding-v1",
  "subject_entity_id": "AKS_ENTITY:<SUBJECT_EID>",
  "predicate_revision_id": "PR:<uuid>",
  "object": {
    "kind": "ENTITY",
    "entity_id": "AKS_ENTITY:<OBJECT_EID>",
    "value": null,
    "value_type": null,
    "unit": null
  },
  "historical_qualifiers": {
    "historical_era_ids": [],
    "historical_polity_ids": [],
    "start_year": null,
    "end_year": null
  }
}
```

literal object이면 `object.kind=LITERAL`, `entity_id=null`로 두고 정규화된 `value`,
`value_type`, `unit`을 채운다. 모든 key와 null을 생략하지 않고, qualifier ID 배열은
canonical ID 오름차순으로 중복 제거한다. 표시명은 payload에 넣지 않는다. UTF-8,
lexicographic key order, 숫자·문자열 정규화가 고정된 `canonical-json-v1` bytes에
SHA-256을 적용한다. 따라서 중첩 `subject.entity_id`나 단축 `object_entity_id` 같은 다른
표현으로 같은 hash를 만들 수 없다.

## 3. 온라인 계약의 단계

```text
MaterialSeed
  -> CorrectMaterialBinding
  -> QuestionClassificationBinding
  -> DonorCoverage + FeasibleCombination
  -> CorrectEvidenceBundle
  -> StimulusBundle
  -> DonorMaterial[] + DonorEvidenceBundle
  -> InternalQuestionAssemblyBundle
  -> RedactedSllmAssemblyView
  -> GeneratedQuestion + GeneratedOptionProvenance[]
```

각 단계는 앞 단계에서 확정된 ID, revision ID, binding hash를 변경하지 않는다. 후보 부족으로
`QuestionUse`, Fact, 문제 유형 또는 난이도를 바꾸면 `attempt_index`를 증가시키고 영향을
받는 이후 단계를 새로 실행한다.

## 4. MaterialSeed

초기 재료에는 정답을 넣지 않는다. 키워드와 발문의도를 먼저 받는다.

```json
{
  "material_seed_id": "SEED:<uuid>",
  "keyword": "정조",
  "stem_intent_id": "select_correct_statement",
  "requested_curriculum_topic_ids": [],
  "requested_curriculum_detail_topic_ids": [],
  "requested_curriculum_era_ids": [],
  "requested_curriculum_detail_era_ids": [],
  "requested_taxonomy_version": "curriculum-v1",
  "curriculum_filter_scope": "PRIMARY_ONLY",
  "random_seed": 184027
}
```

서비스는 키워드를 이름 문자열로 계속 사용하지 않는다. 엔터티 해소 후
`question_target_entity_id`를 고정한다. 동명이인 후보가 둘 이상이면 자동 생성하지
않는다.

curriculum 요청 필드는 비어 있으면 제한 없음이고, 값이 있으면
`requested_taxonomy_version`의 hard filter다. `curriculum_filter_scope`는
`PRIMARY_ONLY` 또는 승인된 별도 값으로 명시하며 primary와 secondary를 암묵적으로 섞지
않는다. 추천용 hint는 이 필드에 넣지 않고 별도 `preference_*` 계약으로 확장한다.

v1은 `stem_intent_id=select_correct_statement`만 active로 허용한다. 부정형 발문은 별도의
TRUE companion·FALSE selected option 계약이 생기기 전까지 요청 검증에서 거절한다.

## 5. 정답 재료 바인딩

Neo4j에서 active·verified `QuestionUse` revision을 선택하면 정답 재료가 확정된다.

```json
{
  "graph_snapshot_id": "GRAPH:2026-07-16:v1",
  "question_target_entity_id": "AKS_ENTITY:E0050867",
  "question_target_name": "정조",
  "topic_type_id": "person",
  "topic_type_revision_id": "TTR:person:v1",
  "question_use_id": "QU:person:E0050867:activity:001",
  "question_use_revision_id": "QUR:<uuid>",
  "facet_id": "person.activity_achievement",
  "facet_revision_id": "QFR:<uuid>",
  "target_role": "subject",
  "answer_role": "whole_fact",
  "answer_shape": "FACT_STATEMENT",
  "answer_domain_id": "DOMAIN:fact_statement",
  "facet_signature_id": "SIG:<uuid>",
  "answer_route": "GENERIC_DONOR",
  "parent_semantic_class_id": "SC:person:joseon_monarch",
  "parent_semantic_class_revision_id": "SCR:<uuid>",
  "semantic_class_taxonomy_version": "semantic-taxonomy-v1",
  "fact_id": "FACT:jeongjo:founded:gyujanggak",
  "fact_revision_id": "FR:<uuid>",
  "fact_canonical_hash": "sha256:<canonical-fact-hash>",
  "predicate_id": "FOUNDED",
  "predicate_revision_id": "PR:<uuid>",
  "predicate_family": "ESTABLISHMENT",
  "source_fact_target_entity_id": "AKS_ENTITY:E0050867",
  "source_fact_binding": {
    "binding_schema_version": "source-fact-binding-v1",
    "subject_entity_id": "AKS_ENTITY:E0050867",
    "predicate_revision_id": "PR:<uuid>",
    "object": {
      "kind": "ENTITY",
      "entity_id": "AKS_ENTITY:<규장각 EID>",
      "value": null,
      "value_type": null,
      "unit": null
    },
    "historical_qualifiers": {
      "historical_era_ids": ["AKS_ENTITY:<조선 후기 EID>"],
      "historical_polity_ids": ["AKS_ENTITY:<조선 EID>"],
      "start_year": null,
      "end_year": null
    }
  },
  "source_fact_binding_hash": "sha256:<canonical-json-hash>",
  "answer_binding": {
    "kind": "WHOLE_FACT",
    "fact_revision_id": "FR:<uuid>"
  },
  "authoritative_evidence_spans": [
    {
      "evidence_span_id": "EV:aks:E0050867:span:12",
      "evidence_span_revision_id": "EVR:<uuid>",
      "content_hash": "sha256:<span-hash>",
      "document_id": "aks:E0050867",
      "chunk_id": "chunk:<ID>",
      "corpus_version": "<VERSION>"
    }
  ]
}
```

`source_fact_target_entity_id`는 해당 Fact가 원래 참인 target이다. 정답 재료에서는
`question_target_entity_id`와 같아야 한다. 이 값은 LLM이 만든 정답이 아니라 검증된
Graph projection 중 하나를 서비스가 선택한 결과다.

위 예시는 generic donor route다. `answer_route=RELATIONAL_GROUP_MEMBERSHIP`이면
`parent_semantic_class_id`와 revision ID를 보내지 않으며 dummy parent를 만들지 않는다.

`historical_qualifiers.historical_era_ids`와 `historical_polity_ids`는 역사 사실의
시간·정권 근접도를 계산하기 위한 canonical 역사 대상 ID다. `curriculum-v1`의 “조선”,
“인물” 같은 취약점 분석 ID와 같은 ID 공간이 아니며 서로 대신 사용할 수 없다.

## 6. QuestionClassificationBinding

취약점 분석 분류는 Neo4j donor 분류에서 추론하지 않는다. 정책 레지스트리는
`question_use_revision_id`마다 검토된 `QuestionClassificationBinding` revision을
반환한다.

```json
{
  "question_classification_binding_revision_id": "QCBR:<uuid>",
  "graph_snapshot_id": "GRAPH:2026-07-16:v1",
  "question_use_revision_id": "QUR:<uuid>",
  "taxonomy_version": "curriculum-v1",
  "binding_hash": "sha256:<canonical-binding-hash>",
  "review_status": "verified",
  "primary_topic": {
    "topic_id": "CTOP:person",
    "detail_topic_ids": ["CTOP_DETAIL:royal_activity"]
  },
  "secondary_topics": [
    {
      "topic_id": "CTOP:politics",
      "detail_topic_ids": ["CTOP_DETAIL:royal_power"]
    }
  ],
  "primary_era": {
    "era_id": "CERA:joseon",
    "detail_era_ids": ["CERA_DETAIL:late_joseon"]
  },
  "secondary_eras": []
}
```

`primary`와 `secondary`는 문항을 어느 축에 대표 집계할지 나타내는 분류 역할이다.
`detail_*`은 상위·하위 계층 수준이다. 둘은 같은 개념이 아니다.

```text
primary_topic.topic_id        = 대표 상위 주제 1개
primary_topic.detail_topic_ids = 그 대표 주제 아래의 세부 주제
secondary_topics              = 보조 상위 주제와 각 하위 세부 주제
primary_era / secondary_eras  = 시대 축의 동일 구조
```

모든 detail ID는 같은 `taxonomy_version` 안에서 자신이 함께 저장된 상위 ID의 실제
하위 항목이어야 한다. primary topic과 primary era는 각각 정확히 하나다. 요청의
curriculum hard filter는 이 binding을 대상으로 적용하고, 통과한 binding revision ID를
생성 문항에 고정한다.

## 7. donor 조회 결과 계약

Neo4j는 이름 목록이 아니라 donor target과 donor의 검증 Fact revision을 같이 반환한다.
조회 안에서는 변수명을 `candidate`로 사용할 수 있지만 이 변수는 저장 노드 유형이 아니다.

같은 자격 쿼리를 두 시점에 사용한다. 지문 생성 전 preflight에서는 난이도별 coverage와
deterministic reserve 순서만 확인하고, 지문 생성 후 최종 조회에서는 아래 전체 DTO와
donor별 RAG 근거를 확정한다.

```json
{
  "graph_snapshot_id": "GRAPH:2026-07-16:v1",
  "question_target_entity_id": "AKS_ENTITY:E0050867",
  "question_use_revision_id": "QUR:<target-uuid>",
  "facet_id": "person.activity_achievement",
  "facet_revision_id": "QFR:<uuid>",
  "topic_type_revision_id": "TTR:person:<REV>",
  "parent_semantic_class_id": "SC:person:joseon_monarch",
  "parent_semantic_class_revision_id": "SCR:<uuid>",
  "semantic_class_taxonomy_version": "semantic-taxonomy-v1",
  "donors": [
    {
      "donor_entity_id": "AKS_ENTITY:<EID>",
      "donor_name": "영조",
      "donor_question_use_id": "QU:person:<EID>:activity:001",
      "donor_question_use_revision_id": "QUR:<donor-uuid>",
      "donor_fact_id": "FACT:<ID>",
      "donor_fact_revision_id": "FR:<donor-uuid>",
      "donor_fact_canonical_hash": "sha256:<canonical-fact-hash>",
      "donor_predicate_id": "<PREDICATE_ID>",
      "donor_predicate_revision_id": "PR:<donor-uuid>",
      "target_role": "subject",
      "answer_role": "whole_fact",
      "answer_shape": "FACT_STATEMENT",
      "answer_domain_id": "DOMAIN:fact_statement",
      "donor_facet_signature_id": "SIG:<uuid>",
      "source_fact_target_entity_id": "AKS_ENTITY:<EID>",
      "fact_target_endpoint_id": "AKS_ENTITY:<EID>",
      "source_fact_binding": {
        "binding_schema_version": "source-fact-binding-v1",
        "subject_entity_id": "AKS_ENTITY:<EID>",
        "predicate_revision_id": "PR:<donor-uuid>",
        "object": {
          "kind": "ENTITY",
          "entity_id": "AKS_ENTITY:<OBJECT_EID>",
          "value": null,
          "value_type": null,
          "unit": null
        },
        "historical_qualifiers": {
          "historical_era_ids": ["AKS_ENTITY:<조선 후기 EID>"],
          "historical_polity_ids": ["AKS_ENTITY:<조선 EID>"],
          "start_year": null,
          "end_year": null
        }
      },
      "source_fact_binding_hash": "sha256:<canonical-json-hash>",
      "predicate_proof_contract": {
        "functional_scope": "SUBJECT_WITH_QUALIFIERS",
        "inverse_functional_scope": "NONE",
        "exclusive_group_ids": [],
        "closed_world_scope_ids": [],
        "proof_contract_version": "predicate-proof-v1"
      },
      "mismatch_rule_ids": ["MR:<ID>:v1"],
      "surface_template_ids": ["ST:<ID>:v1"],
      "donor_authoritative_evidence_spans": [
        {
          "evidence_span_id": "EV:<ID>",
          "evidence_span_revision_id": "EVR:<uuid>",
          "content_hash": "sha256:<span-hash>",
          "document_id": "aks:<EID>",
          "chunk_id": "chunk:<ID>",
          "corpus_version": "<VERSION>"
        }
      ],
      "shared_parent_semantic_class_id": "SC:person:joseon_monarch",
      "shared_parent_semantic_class_revision_id": "SCR:<uuid>",
      "shared_subgroup_revision_ids": ["SCR:<subgroup-uuid>"],
      "target_endpoint_verified": true,
      "difficulty_features": {
        "shared_subgroup_count": 1,
        "historical_era_overlap": true,
        "historical_polity_overlap": true,
        "time_distance": null,
        "same_predicate_family": true
      },
      "relative_rank": 1
    }
  ]
}
```

`fact_owner_id` 같은 별도 소유자 문자열은 두지 않는다. donor Fact의 owner 검증은
`donor_question_use_revision_id`의 `target_role`이 가리키는 실제 Fact endpoint가
`donor_entity_id`인지 구조적으로 검사한다.

```text
target_role=subject -> DonorFact -[:SUBJECT]-> Donor
target_role=object  -> DonorFact -[:OBJECT]-> Donor
```

endpoint가 다르거나 revision이 target의 `graph_snapshot_id`와 다르면 즉시 폐기한다.

## 8. RAG 조회 계약

RAG는 Graph가 확정한 범위 밖에서 더 그럴듯한 대상이나 Fact를 찾지 않는다.

```json
{
  "purpose": "DONOR_TRUE_EVIDENCE",
  "graph_snapshot_id": "GRAPH:2026-07-16:v1",
  "question_target_entity_id": "AKS_ENTITY:E0050867",
  "source_fact_target_entity_id": "AKS_ENTITY:<DONOR_EID>",
  "question_use_revision_id": "QUR:<target-uuid>",
  "donor_question_use_revision_id": "QUR:<donor-uuid>",
  "source_fact_id": "FACT:<ID>",
  "source_fact_revision_id": "FR:<uuid>",
  "source_fact_canonical_hash": "sha256:<canonical-fact-hash>",
  "predicate_revision_id": "PR:<uuid>",
  "source_fact_binding_hash": "sha256:<canonical-json-hash>",
  "allowed_authoritative_evidence_spans": [
    {
      "evidence_span_id": "EV:<ID>",
      "evidence_span_revision_id": "EVR:<uuid>",
      "content_hash": "sha256:<span-hash>",
      "document_id": "aks:<EID>",
      "chunk_id": "chunk:<ID>"
    }
  ],
  "allowed_document_ids": ["aks:<EID>"],
  "corpus_version": "<VERSION>"
}
```

정답 근거 조회에서는 `purpose=REFERENCE_TRUE_EVIDENCE`를 사용하고
`source_fact_target_entity_id=question_target_entity_id`이며
`donor_question_use_revision_id`는 null이다.

검색 우선순위는 다음과 같다.

1. `evidence_span_revision_id`가 가리키는 `chunk_id` exact lookup
2. 같은 `document_id` 안에서 표현 문맥을 보강하는 context-only 검색
3. 동일 source Fact target과 Fact binding hash를 강제한 context-only 제한 검색

RAG가 새로운 target, Fact, 정답 또는 revision을 선택하면 계약 위반이다. 근거를 찾지
못하면 해당 재료를 폐기한다.

2~3단계에서 찾은 새 span은 지문 표현을 돕는 `context_span`일 뿐 Fact나 mismatch
proof의 승인 근거가 아니다. 런타임 응답은 `authoritative_evidence_span_revision_ids`와
`context_chunk_ids`를 분리한다. 새 span을 proof로 쓰려면 오프라인 검수로 새
`EvidenceSpan` revision을 발급하고 Graph를 다시 배포해야 한다.

## 9. 지문 생성 API 계약

지문 API 입력은 정답 Fact revision과 그 authoritative 근거, 발문의도, 선택된
유형·난이도다. 출력은 지문 블록뿐이다.

```json
{
  "stimulus_blocks": [
    {
      "block_token": "STIM:<uuid>",
      "block_type": "TEXT",
      "text": "...",
      "used_authoritative_evidence_span_revision_ids": ["EVR:<uuid>"]
    }
  ]
}
```

출력에 정답 token, truth 값, donor 선택, 새 역사 Fact가 포함되면 실패로 처리한다.
`used_authoritative_evidence_span_revision_ids`는 Orchestrator 내부 provenance이며
sLLM용 redacted view에서는 제거한다.

## 10. 내부 전체 조립 번들

sLLM 호출 전에 option 재료와 정답을 불변 token으로 고정한다. `option_token`은
`OPT:<uuid>` 형식의 opaque token이다. `OPT:reference`, `OPT:candidate:1`처럼 역할이나
정답 여부를 노출하는 값을 사용하지 않는다.

후보가 자기 문맥에서 참이라는 사실만으로 question target 문맥에서 거짓이 되지는 않는다.
정책 레지스트리의 mismatch rule로 `FALSE`를 증명한 donor만 아래 번들에 포함하고,
증명하지 못한 donor는 `UNKNOWN`으로 폐기한다.

v1 production cardinality는 **reference option 1개 + donor option 4개 = 총 5개**다.
아래 JSON과 11장의 redacted view는 필드 구조를 읽기 쉽게 보여 주려고 donor option을
1개만 적은 축약 예시이며, option 개수 검증 fixture가 아니다. 실제 요청·저장에서는
`options`와 `display_order`가 정확히 5개여야 한다.

```json
{
  "question_job_id": "QJOB:<uuid>",
  "material_seed_id": "SEED:<uuid>",
  "graph_snapshot_id": "GRAPH:2026-07-16:v1",
  "policy_bundle_revision_id": "POLICY:<uuid>",
  "question_target_entity_id": "AKS_ENTITY:E0050867",
  "question_use_revision_id": "QUR:<target-uuid>",
  "facet_revision_id": "QFR:<uuid>",
  "question_classification_binding_revision_id": "QCBR:<uuid>",
  "question_classification_binding_hash": "sha256:<canonical-binding-hash>",
  "stem_intent_id": "select_correct_statement",
  "question_type_id": "source_based_mcq",
  "difficulty_band_id": "hard",
  "random_seed": 184027,
  "attempt_index": 2,
  "stimulus_blocks": [
    {
      "block_token": "STIM:<uuid>",
      "block_type": "TEXT",
      "text": "...",
      "used_authoritative_evidence_span_revision_ids": ["EVR:<uuid>"]
    }
  ],
  "options": [
    {
      "option_token": "OPT:550e8400-e29b-41d4-a716-446655440000",
      "truth_in_question_context": true,
      "question_target_entity_id": "AKS_ENTITY:E0050867",
      "source_fact_target_entity_id": "AKS_ENTITY:E0050867",
      "question_use_revision_id": "QUR:<target-uuid>",
      "donor_question_use_revision_id": null,
      "source_fact_id": "FACT:jeongjo:founded:gyujanggak",
      "source_fact_revision_id": "FR:<reference-uuid>",
      "source_fact_canonical_hash": "sha256:<reference-fact-hash>",
      "source_fact_binding": {
        "binding_schema_version": "source-fact-binding-v1",
        "subject_entity_id": "AKS_ENTITY:E0050867",
        "predicate_revision_id": "PR:<reference-uuid>",
        "object": {
          "kind": "ENTITY",
          "entity_id": "AKS_ENTITY:<규장각 EID>",
          "value": null,
          "value_type": null,
          "unit": null
        },
        "historical_qualifiers": {
          "historical_era_ids": ["AKS_ENTITY:<조선 후기 EID>"],
          "historical_polity_ids": ["AKS_ENTITY:<조선 EID>"],
          "start_year": null,
          "end_year": null
        }
      },
      "source_fact_binding_hash": "sha256:<reference-binding-hash>",
      "rendered_claim_binding": {
        "subject_entity_id": "AKS_ENTITY:E0050867",
        "predicate_revision_id": "PR:<reference-uuid>",
        "object_entity_id": "AKS_ENTITY:<규장각 EID>"
      },
      "rendered_claim_hash": "sha256:<reference-claim-hash>",
      "surface_template_id": "fact_statement_v1",
      "render_slots": {
        "subject_display": "정조",
        "predicate_phrase": "<승인 표현>",
        "object_display": "규장각"
      },
      "evidence_roles": {
        "reference_authoritative_evidence_span_revision_ids": ["EVR:<uuid>"],
        "donor_authoritative_evidence_span_revision_ids": [],
        "counter_authoritative_evidence_span_revision_ids": []
      },
      "mismatch_proof_id": null
    },
    {
      "option_token": "OPT:7f4f4b64-82d3-4aac-9d8f-4ea2714db200",
      "truth_in_question_context": false,
      "question_target_entity_id": "AKS_ENTITY:E0050867",
      "source_fact_target_entity_id": "AKS_ENTITY:<DONOR_EID>",
      "question_use_revision_id": "QUR:<target-uuid>",
      "donor_question_use_revision_id": "QUR:<donor-uuid>",
      "source_fact_id": "FACT:<DONOR_FACT_ID>",
      "source_fact_revision_id": "FR:<donor-uuid>",
      "source_fact_canonical_hash": "sha256:<donor-fact-hash>",
      "source_fact_binding": {
        "binding_schema_version": "source-fact-binding-v1",
        "subject_entity_id": "AKS_ENTITY:<DONOR_EID>",
        "predicate_revision_id": "PR:<donor-uuid>",
        "object": {
          "kind": "ENTITY",
          "entity_id": "AKS_ENTITY:<OBJECT_EID>",
          "value": null,
          "value_type": null,
          "unit": null
        },
        "historical_qualifiers": {
          "historical_era_ids": ["AKS_ENTITY:<조선 후기 EID>"],
          "historical_polity_ids": ["AKS_ENTITY:<조선 EID>"],
          "start_year": null,
          "end_year": null
        }
      },
      "source_fact_binding_hash": "sha256:<donor-binding-hash>",
      "rendered_claim_binding": {
        "subject_entity_id": "AKS_ENTITY:E0050867",
        "predicate_revision_id": "PR:<donor-uuid>",
        "object_entity_id": "AKS_ENTITY:<OBJECT_EID>"
      },
      "rendered_claim_hash": "sha256:<donor-claim-hash>",
      "surface_template_id": "fact_statement_v1",
      "render_slots": {
        "subject_display": "정조",
        "predicate_phrase": "<승인 표현>",
        "object_display": "<검증된 표시값>"
      },
      "evidence_roles": {
        "reference_authoritative_evidence_span_revision_ids": ["EVR:<target-uuid>"],
        "donor_authoritative_evidence_span_revision_ids": ["EVR:<donor-uuid>"],
        "counter_authoritative_evidence_span_revision_ids": ["EVR:<counter-uuid>"]
      },
      "mismatch_proof_id": "PROOF:<uuid>"
    }
  ],
  "correct_option_token": "OPT:550e8400-e29b-41d4-a716-446655440000"
}
```

`source_fact_binding`은 source Fact가 원래 donor에게 참인 구조다.
`rendered_claim_binding`은 그 Fact 재료를 question target 문맥에 투영해 실제 option이
주장하는 구조다. 둘은 별도 canonical JSON으로 hash하고 donor option의 mismatch proof는
반드시 `rendered_claim_hash`를 대상으로 한다.

`source_fact_binding`은 2.1의 고정 schema만 사용한다. `rendered_claim_binding`은 별도
`rendered-claim-binding-v1` schema로 canonicalize하며, 둘을 같은 payload나 hash로
취급하지 않는다.

## 11. sLLM redacted view

앞 절의 전체 번들은 Orchestrator와 validator만 보유한다. sLLM에는 allowlist 방식으로
만든 아래 redacted view만 전달한다.

```json
{
  "assembly_request_token": "ASM:<uuid>",
  "stimulus_blocks": [
    {
      "block_token": "STIM:<uuid>",
      "block_type": "TEXT",
      "text": "..."
    }
  ],
  "stem_contract": {
    "stem_intent_id": "select_correct_statement",
    "question_type_id": "source_based_mcq",
    "difficulty_band_id": "hard"
  },
  "options": [
    {
      "option_token": "OPT:550e8400-e29b-41d4-a716-446655440000",
      "surface_template_id": "fact_statement_v1",
      "render_slots": {
        "subject_display": "정조",
        "predicate_phrase": "<승인 표현>",
        "object_display": "규장각"
      }
    },
    {
      "option_token": "OPT:7f4f4b64-82d3-4aac-9d8f-4ea2714db200",
      "surface_template_id": "fact_statement_v1",
      "render_slots": {
        "subject_display": "정조",
        "predicate_phrase": "<승인 표현>",
        "object_display": "<검증된 표시값>"
      }
    }
  ],
  "output_schema": {
    "required_option_token": true,
    "allow_option_addition": false,
    "allow_option_deletion": false
  }
}
```

sLLM 입력에는 다음을 절대 포함하지 않는다.

- `truth_in_question_context`, `correct_option_token`
- `question_target_entity_id`, `source_fact_target_entity_id`
- `source_fact_id`, Fact·Predicate·QuestionUse revision ID
- `source_fact_binding`, `source_fact_binding_hash`
- `rendered_claim_binding`, `rendered_claim_hash`
- mismatch rule·proof ID·verdict
- authoritative/context evidence ID와 evidence 역할
- `REFERENCE`, `DONOR`, `CORRECT` 같은 option 역할명

sLLM은 option token을 보존해 문법과 표현만 만든다. option을 추가·삭제하거나 token을
변경하면 실패다. 서버 validator가 출력 token을 내부 전체 번들과 다시 결합해 사실·claim
hash·정답 유일성을 검사한 뒤 표시 순서를 섞는다.

## 12. mismatch proof 계약

`mismatch_proof_id`는 운영 PostgreSQL의 불변 proof payload를 가리킨다. proof는 donor
Fact가 참이라는 근거와, 그 Fact를 question target에 투영한
`rendered_claim_binding`이 거짓이라는 근거를 분리한다.

```json
{
  "proof_id": "PROOF:<uuid>",
  "graph_snapshot_id": "GRAPH:2026-07-16:v1",
  "proof_kind": "FUNCTIONAL_KEY_CONFLICT",
  "mismatch_rule_id": "MR:<FACET>:<PREDICATE>:v1",
  "mismatch_rule_version": "1.0.0",
  "question_target_entity_id": "AKS_ENTITY:E0050867",
  "question_use_revision_id": "QUR:<target-uuid>",
  "target_fact_id": "FACT:jeongjo:founded:gyujanggak",
  "target_fact_revision_id": "FR:<target-uuid>",
  "donor_entity_id": "AKS_ENTITY:<DONOR_EID>",
  "donor_question_use_revision_id": "QUR:<donor-uuid>",
  "source_fact_id": "FACT:<DONOR_FACT_ID>",
  "source_fact_revision_id": "FR:<donor-uuid>",
  "source_fact_canonical_hash": "sha256:<donor-fact-hash>",
  "source_fact_binding_hash": "sha256:<donor-binding-hash>",
  "predicate_revision_id": "PR:<donor-uuid>",
  "predicate_proof_contract": {
    "functional_scope": "SUBJECT_WITH_QUALIFIERS",
    "inverse_functional_scope": "NONE",
    "exclusive_group_ids": [],
    "closed_world_scope_ids": [],
    "proof_contract_version": "predicate-proof-v1"
  },
  "rendered_claim_binding": {
    "subject_entity_id": "AKS_ENTITY:E0050867",
    "predicate_revision_id": "PR:<donor-uuid>",
    "object_entity_id": "AKS_ENTITY:<OBJECT_EID>"
  },
  "rendered_claim_hash": "sha256:<donor-claim-hash>",
  "functional_key": {
    "predicate_revision_id": "PR:<donor-uuid>",
    "key_role": "object",
    "key_binding_hash": "sha256:<functional-key-hash>"
  },
  "exclusive_group": null,
  "closure_scope": {
    "scope_id": "CLOSURE:<scope-id>",
    "scope_version": "1.0.0"
  },
  "evidence": [
    {
      "role": "TARGET_TRUE",
      "evidence_span_id": "EV:<target-id>",
      "evidence_span_revision_id": "EVR:<target-uuid>",
      "content_hash": "sha256:<target-span-hash>"
    },
    {
      "role": "DONOR_TRUE",
      "evidence_span_id": "EV:<donor-id>",
      "evidence_span_revision_id": "EVR:<donor-uuid>",
      "content_hash": "sha256:<donor-span-hash>"
    },
    {
      "role": "COUNTER_FALSE",
      "evidence_span_id": "EV:<counter-id>",
      "evidence_span_revision_id": "EVR:<counter-uuid>",
      "content_hash": "sha256:<counter-span-hash>"
    }
  ],
  "verdict": "FALSE",
  "validator_version": "mismatch-validator-v1",
  "proof_hash_algorithm": "sha256-canonical-json-v1",
  "proof_hash": "sha256:<proof-hash>"
}
```

`proof_kind`별 필수 필드는 정책 레지스트리가 버전으로 관리한다.
사용한 `mismatch_rule_id`는 donor DTO의 `mismatch_rule_ids`와 target Facet signature가
공통으로 허용한 값이어야 한다. rendered option의 `surface_template_id`도 donor DTO의
`surface_template_ids` 안에 있어야 한다.

| proof_kind | 필수 근거 |
|---|---|
| `FUNCTIONAL_KEY_CONFLICT` | 승인 functional key와 충돌하는 authoritative Fact·evidence |
| `EXCLUSIVE_GROUP_CONFLICT` | `exclusive_group` ID/version과 상호배타 membership 근거 |
| `CLOSED_SCOPE_ABSENCE` | 완전성이 승인된 `closure_scope` ID/version과 폐쇄 목록 |
| `EXPLICIT_COUNTER_EVIDENCE` | rendered claim을 직접 반증하는 counter EvidenceSpan revision |

단순히 Graph에 관계가 없거나 검색 결과가 0건이라는 이유로 `FALSE`를 만들지 않는다.
`CLOSED_SCOPE_ABSENCE`도 version 있는 폐쇄 목록의 완전성이 승인된 경우에만 사용할 수
있다. 필수 필드·근거가 하나라도 없으면 verdict는 `UNKNOWN`이다.

`FALSE`이고 모든 authoritative evidence와 rule version이 유효한 proof만 option에 사용할
수 있다. `proof_hash`는 `proof_hash` 필드 자체를 제외한 전체 payload를
`sha256-canonical-json-v1`로 정규화해 계산한다. source binding이나 rendered claim hash가
달라지면 기존 proof를 재사용하지 않는다.

## 13. 운영 DB 저장 인터페이스

### 13.1 GeneratedQuestion

생성 성공과 skip을 같은 생성 작업 계보로 추적한다.

```json
{
  "question_job_id": "QJOB:<uuid>",
  "question_version_id": "QV:<uuid>",
  "generation_status": "GENERATED",
  "skip_reason_code": null,
  "material_seed_id": "SEED:<uuid>",
  "graph_snapshot_id": "GRAPH:2026-07-16:v1",
  "policy_bundle_revision_id": "POLICY:<uuid>",
  "question_classification_binding_revision_id": "QCBR:<uuid>",
  "question_classification_binding_hash": "sha256:<canonical-binding-hash>",
  "taxonomy_version": "curriculum-v1",
  "rag_corpus_version": "<CORPUS_VERSION>",
  "passage_model_revision_id": "MODEL:passage:<version>",
  "passage_prompt_revision_id": "PROMPT:passage:<version>",
  "sllm_model_revision_id": "MODEL:sllm:<version>",
  "sllm_prompt_revision_id": "PROMPT:sllm:<version>",
  "validator_revision_id": "VALIDATOR:<version>",
  "question_target_entity_id": "AKS_ENTITY:E0050867",
  "question_use_revision_id": "QUR:<target-uuid>",
  "facet_revision_id": "QFR:<uuid>",
  "target_fact_revision_id": "FR:<target-uuid>",
  "stem_intent_id": "select_correct_statement",
  "question_type_id": "source_based_mcq",
  "difficulty_band_id": "hard",
  "random_seed": 184027,
  "attempt_index": 2,
  "retry_trace": [
    {
      "attempt_index": 0,
      "status": "RETRY",
      "reason_code": "DONOR_PROOF_INSUFFICIENT"
    },
    {
      "attempt_index": 1,
      "status": "RETRY",
      "reason_code": "DONOR_COUNT_INSUFFICIENT"
    },
    {
      "attempt_index": 2,
      "status": "GENERATED",
      "reason_code": null
    }
  ],
  "option_tokens": [
    "OPT:550e8400-e29b-41d4-a716-446655440000",
    "OPT:7f4f4b64-82d3-4aac-9d8f-4ea2714db200"
  ],
  "correct_option_token": "OPT:550e8400-e29b-41d4-a716-446655440000",
  "display_order": [
    "OPT:7f4f4b64-82d3-4aac-9d8f-4ea2714db200",
    "OPT:550e8400-e29b-41d4-a716-446655440000"
  ],
  "primary_curriculum_topic_id": "CTOP:person",
  "primary_curriculum_detail_topic_ids": ["CTOP_DETAIL:royal_activity"],
  "secondary_curriculum_topic_ids": ["CTOP:politics"],
  "secondary_curriculum_detail_topic_ids": ["CTOP_DETAIL:royal_power"],
  "primary_curriculum_era_id": "CERA:joseon",
  "primary_curriculum_detail_era_ids": ["CERA_DETAIL:late_joseon"],
  "secondary_curriculum_era_ids": [],
  "secondary_curriculum_detail_era_ids": [],
  "rendered_question_hash": "sha256:<question-hash>"
}
```

위 `option_tokens`와 `display_order`도 10장의 축약 표기다. production 성공 행은 두 배열이
모두 서로 같은 5개 token 집합을 정확히 한 번씩 포함해야 한다.

모든 조합이 실패하면 `generation_status=SKIPPED`, `question_version_id=null`,
`correct_option_token=null`로 저장하고 최종 `skip_reason_code`와 전체 `retry_trace`를
남긴다. skip을 쉬운 난이도나 broad parent fallback으로 위장하지 않는다.

### 13.2 GeneratedOptionProvenance

각 option은 다음 provenance를 별도 행 또는 동등한 정규화 구조로 저장한다.

```json
{
  "question_version_id": "QV:<uuid>",
  "option_token": "OPT:7f4f4b64-82d3-4aac-9d8f-4ea2714db200",
  "internal_option_role": "DONOR",
  "truth_in_question_context": false,
  "graph_snapshot_id": "GRAPH:2026-07-16:v1",
  "question_target_entity_id": "AKS_ENTITY:E0050867",
  "source_fact_target_entity_id": "AKS_ENTITY:<DONOR_EID>",
  "question_use_revision_id": "QUR:<target-uuid>",
  "donor_question_use_revision_id": "QUR:<donor-uuid>",
  "source_fact_id": "FACT:<DONOR_FACT_ID>",
  "source_fact_revision_id": "FR:<donor-uuid>",
  "source_fact_canonical_hash": "sha256:<donor-fact-hash>",
  "predicate_revision_id": "PR:<donor-uuid>",
  "source_fact_binding_hash": "sha256:<donor-binding-hash>",
  "rendered_claim_hash": "sha256:<donor-claim-hash>",
  "reference_authoritative_evidence_span_revision_ids": ["EVR:<target-uuid>"],
  "donor_authoritative_evidence_span_revision_ids": ["EVR:<donor-uuid>"],
  "counter_authoritative_evidence_span_revision_ids": ["EVR:<counter-uuid>"],
  "mismatch_rule_id": "MR:<FACET>:<PREDICATE>:v1",
  "mismatch_rule_version": "1.0.0",
  "mismatch_proof_id": "PROOF:<uuid>",
  "mismatch_proof_hash": "sha256:<proof-hash>"
}
```

reference option은 `internal_option_role=REFERENCE`이고
`source_fact_target_entity_id=question_target_entity_id`다.
`donor_question_use_revision_id`와 mismatch 필드는 null이다.

`internal_option_role`, truth, source target, proof, evidence 역할은 서버 내부 provenance다.
클라이언트 문제 시작 응답이나 sLLM redacted view에 노출하지 않는다. 채점의 단일
진실원은 저장된 `correct_option_token`과 사용자 `selected_option_token`의 일치 여부다.

## 14. 현재 애플리케이션과의 관계

현재 `app/question` API는 PostgreSQL의 기존 `questions`와 `question_options`를 조회해
문제지를 구성한다. 이 문서의 MaterialSeed·Graph 조회·RAG·생성 모델 계약은 아직
구현되어 있지 않다. 새 파이프라인은 기존 풀이·채점 API 앞에서 검증된
`GeneratedQuestion`과 `GeneratedOptionProvenance`를 만드는 별도 서비스로 추가하는 것이
경계상 맞다.

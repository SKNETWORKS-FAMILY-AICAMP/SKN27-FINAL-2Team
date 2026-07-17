# 03. 저장소 책임과 오답 후보 조회 계약

> 상태: `TARGET-CONTRACT`
> 목적: Neo4j가 어떤 정보를 저장하고 검색 서비스에 무엇을 반환하는지 정의한다.

## 1. 저장소별 단일 책임

| 저장소/계층 | 저장·처리 대상 | 저장하지 않는 것 |
|---|---|---|
| raw/staging | 원문 행, 원천 ID, 파일 hash, 파싱 오류, 후보·충돌 | production 검색 결과 |
| Neo4j | canonical 대상, 승인 이름, 분류, 역할 맥락, 검증 관계, evidence 참조 | 긴 본문, 임베딩, 선지 문장 |
| pgvector/RAG | 문서·chunk·embedding·근거 검색 | canonical 관계의 최종 진실 |
| 생성/운영 DB | 문제 재료, 생성 지문·발문·선지, 평가 결과 | 원천 전체 복제 |

Neo4j의 `EvidenceSpan`에는 원문 전체를 넣지 않고 RAG 저장소에서 근거를 재조회할 수 있는
식별자와 짧은 검증 span만 둔다.

## 2. ID 계약

| ID | 의미 |
|---|---|
| `source_record_id` | 원천 namespace와 원천 ID, release를 포함한 레코드 ID |
| `canonical_id` | 원천이 달라도 같은 역사 실체를 가리키는 안정 ID |
| `entity_name_id` | 대표명·별칭·한자·자·호 등 표기 occurrence ID |
| `anchor_id` | entity type, topic, era, role, polity, region, 세부 분류 노드의 안정 ID |
| `evidence_id` | 문서·chunk·offset 또는 content hash에 묶인 근거 ID |
| `relation_id` | subject·predicate·object·qualifier·근거 버전으로 만든 관계 ID |

ID를 이름 문자열이나 입력 행 순번만으로 만들지 않는다. 원천별 ID namespace도 섞지 않는다.

## 3. 요청 계약

```json
{
  "correct_canonical_id": "canonical:person:...",
  "correct_entity_type_id": "person",
  "question_intent_id": "material에 포함된 발문의도 ID",
  "topic_ids": ["topic:politics"],
  "era_ids": ["era:joseon_late"],
  "era_match_mode": "SELF_OR_DESCENDANT",
  "allowed_path_pattern_ids": [
    "SIBLING_DETAIL_CLASS",
    "SHARED_ROLE_ASSIGNMENT"
  ],
  "excluded_canonical_ids": [],
  "graph_release_id": "선택 사항"
}
```

필수값은 `correct_canonical_id`와 비어 있지 않은 `allowed_path_pattern_ids`다. 나머지는
검색 팀이 후보 범위를 제한하기 위한 값이다.
`excluded_canonical_ids`를 생략하면 검색 서비스가 빈 배열로 정규화해 query에 전달한다.
`graph_release_id`를 생략하면 검색 서비스가 현재 활성 production release ID로 정규화한다.
따라서 실제 query에는 두 parameter가 항상 전달된다.
`correct_entity_type_id`가 들어오면 Graph의 값과 일치해야 하며 불일치하면 빈 결과로
조용히 처리하지 않고 계약 오류를 반환한다.

`topic_ids`와 `era_ids`는 후보를 새로 분류하기 위한 값이 아니다. 이미 검증된 Graph
연결을 요청 맥락에 맞게 필터링하기 위한 값이다. 여러 `era_ids`는 OR로 처리한다.
`era_match_mode` 기본값은 `SELF_OR_DESCENDANT`이며 서버가 요청 Era 자신과 승인된 모든
하위 Era로 확장한다. `SELF_ONLY`는 요청 Era 자체만 허용한다. 세부 Era를 요청했는데 대상이
상위 Era에만 연결된 경우에는 세부 시대를 추측하지 않고 제외한다.

`allowed_path_pattern_ids`는 배포된 path pattern 카탈로그의 ID만 허용한다. 호출자는 숫자
hop이나 taxonomy 최대 깊이를 전달하지 않는다. 각 패턴이 축·관계 방향·계층 탐색 규칙을
고정하며, 난이도는 외부 계층이 pattern과 specificity·taxonomy distance 조건으로 변환한다.

이전 초안의 `semantic_detail`은 요청 호환 alias로만 받고 내부에서는 `detail_class`로
정규화한다. 신규 요청과 결과는 `detail_class`를 사용한다.

### 3.1 문제 생성 계약 crosswalk

문제 생성 측의 `QuestionTarget`은 `correct_canonical_id`, `SemanticClass`는
`DetailClass`, 부모·하위 분류는 `SUBCATEGORY_OF` 계층으로 대응한다. 원자 `Fact`는
VERIFIED typed relationship 또는 `RelationAssertion`, 근거는 `EvidenceSpan`으로
대응한다. 이 대응을 위해 별도 `QuestionTarget`, `SemanticClass`, `Fact` 노드를
중복 생성하지 않는다.

문제 생성 측의 `TopicType`과 `QuestionFacet`은 고정 Graph label이 아니다. 호출자가 이를
`correct_entity_type_id`, `question_intent_id`, `allowed_path_pattern_ids`와 필터 조건으로
변환한다. 취약점 분석용 Topic 10개와 Era 10개는 그대로 유지한다.

## 4. 결과 계약

```json
{
  "graph_release_id": "graph:2026-07-17:...",
  "correct_canonical_id": "canonical:person:...",
  "candidates": [
    {
      "candidate_canonical_id": "canonical:person:...",
      "display_name": "세종",
      "aliases": ["세종대왕"],
      "entity_type_id": "person",
      "shared_anchors": [
        {
          "axis": "role_context",
          "path_pattern_id": "SHARED_ROLE_ASSIGNMENT",
          "role_id": "role:king",
          "role_name": "왕",
          "polity_id": "polity:joseon",
          "polity_name": "조선",
          "correct_assignment_id": "assignment:correct:...",
          "candidate_assignment_id": "assignment:candidate:...",
          "taxonomy_distance": null,
          "correct_evidence_ids": ["evidence:correct:..."],
          "candidate_evidence_ids": ["evidence:candidate:..."],
          "specificity_level": 2
        },
        {
          "axis": "polity",
          "anchor_id": "polity:joseon",
          "anchor_name": "조선",
          "path_pattern_id": "SHARED_ANCHOR_DIRECT",
          "taxonomy_distance": null,
          "correct_relation_id": "relation:correct:...",
          "candidate_relation_id": "relation:candidate:...",
          "correct_evidence_ids": ["evidence:correct:..."],
          "candidate_evidence_ids": ["evidence:candidate:..."]
        }
      ],
      "rag_search_context": {
        "canonical_name": "세종",
        "aliases": ["세종대왕"],
        "polity_names": ["조선"],
        "era_names": ["조선 전기"],
        "context_terms": ["왕", "재위"]
      }
    }
  ]
}
```

Neo4j는 후보별 최종 선지 근거를 보장하지 않는다. `correct_evidence_ids`와
`candidate_evidence_ids`는 각각 정답·후보 Graph 경로의 근거이며, 선지 생성에 사용할
본문은 RAG가 별도로 찾아야 한다.

`shared_anchors`는 후보 비교 기준점의 계약 필드다. 물리적인 `:Anchor` 분류 노드뿐 아니라
`role_context`, `typed_relation`처럼 승인된 복합 경로도 포함할 수 있다. 물리적 분류 노드의
axis는 `entity_type | topic | era | polity | person_role | region | detail_class`를 사용하고,
복합 경로 결과에는 `role_context | typed_relation`을 사용한다. 필드명을 별도의
`SharedContext`로 변경하지 않는다.

모든 항목에는 `path_pattern_id`가 있다. DetailClass가 포함된 패턴의 `taxonomy_distance`는
`correct_to_common`, `candidate_to_common`, `total`을 반환하고 비계층 패턴은 `null`이다.
물리 관계 수는 계약에 포함하지 않는다. 진단이 필요하면 선택적 `_debug.physical_hop_count`로
제공할 수 있지만 후보 자격·점수·난이도에는 사용하지 않는다.

`role_context`는 단일 Anchor가 아니라 역할·국가·선택적 시대가 결합된 복합 경로다.
따라서 `anchor_id` 대신 `role_id`, `polity_id`, 양쪽 `assignment_id`와 evidence ID를
반환한다. `SHARED_ROLE_ASSIGNMENT_ERA`에는 같은 형식에 `era_id`, `era_name`을 추가한다.
직접 Anchor와 typed relation 패턴은 `correct_relation_id`, `candidate_relation_id`를
반환하고 모든 패턴은 양쪽 evidence ID를 명시적으로 분리한다.

## 5. 허용 path pattern과 anchor 계약

검색 서비스는 임의의 모든 edge를 탐색하지 않는다. 요청의 `allowed_path_pattern_ids`를
배포 버전의 카탈로그로 해석하고, 각 pattern에 고정된 축과 관계 allowlist를 사용한다.

| 축 | 관계 예 | 사용 조건 |
|---|---|---|
| entity type | `HAS_ENTITY_TYPE` | 후보와 정답의 type 일치 검사 |
| topic | `HAS_TOPIC` | VERIFIED, broad anchor 단독 사용 제한 |
| era | `IN_ERA` | VERIFIED 또는 승인된 기간 규칙 |
| polity | `ASSOCIATED_WITH_POLITY` | VERIFIED |
| role context (`role_context`) | `HAS_ROLE_ASSIGNMENT` 경로 | role·polity·era 맥락 중 필요한 값 일치 |
| region | `OCCURRED_IN`, `LOCATED_IN` | predicate 의미와 endpoint type 일치 |
| detail class (`detail_class`) | `CLASSIFIED_AS` | 승인된 세부 카탈로그, VERIFIED |
| typed fact (`typed_relation`) | `BUILT`, `FOUNDED`, `PARTICIPATED_IN` 등 | 근거 span과 양 endpoint VERIFIED |

`AKS relatedArticles`, ITKC의 역할 미확정 `사건인물`, `UNRESOLVED` entity,
`PENDING`/`REJECTED`
관계는 allowlist에 들어갈 수 없다.

## 6. 후보 제외 계약

반환 전에 최소한 다음을 제외한다.

1. 정답과 동일한 `canonical_id`
2. 요청의 `excluded_canonical_ids`에 포함된 대상
3. 정답의 승인 별칭이 별도 canonical 대상으로 잘못 만들어진 경우
4. entity type 불일치 후보
5. `AMBIGUOUS`, `UNRESOLVED`, `REJECTED` entity link가 포함된 후보
6. `PENDING`, `REJECTED`, `CONFLICT` 관계만으로 연결된 후보
7. broad anchor 하나만 공유하는 후보
8. 서로 모순되는 시대·정치체 qualifier를 가진 관계

3번은 검색 단계의 임시 방어이기도 하다. 근본적으로는 entity resolution QA에서 0건이어야
한다.

## 7. 버전과 재현성

Graph 배포본은 최소한 다음 버전을 기록한다.

```text
source_manifest_hash
normalization_version
entity_resolution_version
entity_type_catalog_version
taxonomy_version
period_rule_version
relation_extraction_version
verification_policy_version
anchor_allowlist_version
path_pattern_catalog_version
```

검색 서비스가 `graph_release_id`를 전달하면 해당 배포본과 다른 ID/관계를 섞지 않는다.
전체 immutable snapshot 구조를 먼저 도입할 필요는 없지만, 어떤 원천과 규칙으로 후보가
나왔는지는 재현 가능해야 한다.

## 8. 명시적 비계약

다음 값은 Neo4j 결과 계약에 고정하지 않는다.

- 최종 후보 점수와 순위
- 난이도
- PageRank/PPR 여부
- 최종 오답 4개 선택
- 정답·오답 선지 문장
- 문제 유형과 프롬프트

검색 팀이 점수를 추가하더라도 Graph가 제공한 `shared_anchors`와 검증 상태를 제거하지
않는다.

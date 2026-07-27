# 06. 공통 노드 기반 오답 후보 조회

> 레거시 문서: 이전 오답 후보 조회 계약이다. 현재 사실 그래프 기준으로 사용하지 않는다.
>
> 상태: `TARGET-SEARCH-CONTRACT`
> 범위: 후보 자격과 반환 근거. 난이도 계산과 최종 순위는 검색/문제 생성 팀의 책임이다.

## 1. “같은 노드를 타는 다른 대상”의 정의

기본 후보는 정답과 같은 검증 anchor를 공유하는 다른 canonical 대상이다.

```text
(correct)-[VERIFIED 관계]->(shared anchor)<-[동일 의미 VERIFIED 관계]-(candidate)
```

다음 조건을 모두 만족해야 한다.

1. `candidate.canonical_id <> correct.canonical_id`
2. 정답과 후보의 `EntityType`이 같다.
3. 정답과 후보의 entity resolution이 `ACCEPTED`다.
4. 양쪽 관계가 모두 `VERIFIED`다.
5. 관계 방향과 의미가 같다.
6. anchor가 해당 요청의 allowlist에 있다.
7. broad anchor 하나만 공유한 경우는 후보 자격으로 충분하지 않다.

여기서 anchor는 후보를 비교하는 계약상의 기준점이다. 물리적인 `:Anchor` 분류 노드는
그 부분집합이며, `RoleAssignment` 복합 맥락이나 동일 typed relationship 대상도
`shared_anchors` 결과에 포함될 수 있다.

## 2. 사용할 수 있는 공통 anchor

| 질문 맥락 | 공통 경로 예 | 비고 |
|---|---|---|
| 같은 세부 종류 | `Entity -> CLASSIFIED_AS -> DetailClass` | 가장 일반적인 후보 경로 |
| 같은 국가의 왕 | `Person -> RoleAssignment -> 왕 + Polity` | 역할과 국가를 함께 비교 |
| 같은 시대 인물 | `Entity -> IN_ERA -> 세부 Era` | top-level era 단독은 너무 넓을 수 있음 |
| 같은 국가의 제도 | `Institution -> ASSOCIATED_WITH_POLITY -> Polity` | Topic/세부 분류와 결합 권장 |
| 같은 지역 사건 | `Event -> OCCURRED_IN -> Region` | 발생지와 소재지 혼용 금지 |
| 같은 사건 참여자 | `Person -> PARTICIPATED_IN -> Event` | `사건인물` 미확정 관계 제외 |
| 같은 문화재 관계 | `Person -> BUILT/PROMOTED_* -> Heritage` | Predicate 의미가 같아야 함 |
| 같은 topic | `Entity -> HAS_TOPIC -> Topic` | 단독 후보 자격은 제한 |

`왕`, `조선`, `정치`를 각각 따로 공유하는 것과 `조선에서 왕이었던 역할 assignment`를
공유하는 것은 다르다. 발문의도가 역할 맥락을 요구하면 후자를 사용한다.

## 3. broad anchor 방어

다음 노드는 degree가 크기 때문에 단독 공통점으로 사용하면 무관한 유명 대상이 대량으로
섞일 수 있다.

```text
EntityType=Person/Event
Topic=인물/사건/정치/문화
Era=조선/고려
Polity=조선/고려
```

이러한 anchor는 필터 또는 보조 공통점으로만 쓰고, 다음 중 하나 이상과 결합한다.

- 세부 역할과 역할 국가
- 세부 시대
- 세부 사건·제도·문화 유형
- 구체 지역
- 동일 typed historical relation
- degree가 낮은 승인 세부 분류

anchor별 `search_eligible`, `specificity_level`, `max_degree_policy`를 카탈로그에 둔다.
실제 가중치와 cutoff는 검색 팀이 결정한다.

## 4. 이름 붙인 path pattern 조회

요청 계약은 물리 hop 수가 아니라 `allowed_path_pattern_ids` 하나로 탐색 의미를 선택한다.
각 패턴은 허용 축·관계 방향·검증 조건·taxonomy 탐색 범위를 카탈로그에 고정한다. 호출자는
별도의 최대 hop이나 최대 taxonomy 깊이를 전달하지 않는다.

### 4.1 초기 path pattern seed

| pattern ID | 의미 |
|---|---|
| `SHARED_ANCHOR_DIRECT` | 같은 분류 Anchor를 직접 공유 |
| `ANCESTOR_DESCENDANT_DETAIL_CLASS` | 한쪽의 DetailClass가 다른 쪽 class의 상위·하위 |
| `SIBLING_DETAIL_CLASS` | 서로 다른 DetailClass가 승인된 공통 조상을 공유 |
| `SHARED_ROLE_ASSIGNMENT` | role+polity 맥락 공유 |
| `SHARED_ROLE_ASSIGNMENT_ERA` | role+polity+세부 era 맥락 공유 |
| `SHARED_TYPED_RELATION` | 같은 방향·Predicate로 동일 canonical 대상을 공유 |

이 목록은 계약 seed다. EDA 후 실제 활성화할 패턴과 Predicate allowlist를
`path_pattern_catalog_version`으로 승인한다. `SHARED_ANCHOR_DIRECT`도 broad Anchor 단독
방어와 specificity 조건을 우회하지 않는다.

### 4.2 taxonomy distance

DetailClass 계층 패턴은 정답과 후보의 검증된 실제 분류에서 가장 가까운 공통 조상까지의
거리를 계산한다.

```json
{
  "correct_to_common": 1,
  "candidate_to_common": 1,
  "total": 2
}
```

같은 class는 `0/0/0`, 상위·하위는 `1/0/1`, 형제 class는 `1/1/2`처럼 표현한다.
상위 분류 지름길 edge가 있더라도 거리 계산에는 사용하지 않는다. 비계층 패턴의
`taxonomy_distance`는 `null`이다. 검증 분류가 여러 개면 `total`이 가장 작고 공통 class의
specificity가 가장 높은 경로를 대표 거리로 선택하며 나머지는 보조 match로 보존한다.

### 4.3 `SHARED_ANCHOR_DIRECT` Cypher 예시

아래 Cypher는 구조 예시다. production에서는 관계 allowlist와 release ID를 parameter로
관리한다.

```cypher
MATCH (correct:CanonicalEntity {canonical_id: $correct_canonical_id})
MATCH (correct)-[correctRel]->(anchor:Anchor)<-[candidateRel]-(candidate:CanonicalEntity)
WHERE candidate.canonical_id <> correct.canonical_id
  AND NOT candidate.canonical_id IN $excluded_canonical_ids
  AND correct.resolution_status = 'ACCEPTED'
  AND candidate.resolution_status = 'ACCEPTED'
  AND correct.entity_type_id = candidate.entity_type_id
  AND correctRel.status = 'VERIFIED'
  AND candidateRel.status = 'VERIFIED'
  AND type(correctRel) = type(candidateRel)
  AND type(correctRel) IN $pattern_relation_types
  AND anchor.axis IN $pattern_anchor_axes
  AND anchor.search_eligible = true
  AND anchor.review_status = 'VERIFIED'
  AND correctRel.graph_release_id = $graph_release_id
  AND candidateRel.graph_release_id = $graph_release_id
WITH candidate,
     collect(DISTINCT {
       path_pattern_id: 'SHARED_ANCHOR_DIRECT',
       axis: anchor.axis,
       anchor_id: anchor.anchor_id,
       anchor_name: anchor.name,
       relation_type: type(correctRel),
       specificity_level: anchor.specificity_level,
       correct_relation_id: correctRel.relation_id,
       candidate_relation_id: candidateRel.relation_id,
       taxonomy_distance: CASE
         WHEN anchor.axis = 'detail_class' THEN {
           correct_to_common: 0,
           candidate_to_common: 0,
           total: 0
         }
         ELSE null
       END,
       correct_evidence_ids: correctRel.evidence_ids,
       candidate_evidence_ids: candidateRel.evidence_ids
     }) AS sharedAnchors
WHERE any(a IN sharedAnchors WHERE a.specificity_level >= $minimum_specificity)
  AND size(sharedAnchors) >= $minimum_shared_anchor_count
RETURN candidate.canonical_id AS candidate_canonical_id,
       candidate.display_name AS candidate_name,
       candidate.entity_type_id AS entity_type_id,
       sharedAnchors AS shared_anchors
```

`$pattern_relation_types`와 `$pattern_anchor_axes`는 호출자가 임의로 전달하는 값이 아니라
서버가 승인된 path pattern 카탈로그에서 해석한 값이다. 관계 type을 동적으로 허용하기
어렵거나 query 계획이 불안정하면 pattern별 고정 query를 분리한다.

### 4.4 결과 계약

모든 후보는 `shared_anchors` 항목별 `path_pattern_id`를 반환한다. 계층 패턴은
`taxonomy_distance`, 모든 패턴은 통과한 Anchor·관계와 양쪽의
`correct_evidence_ids`·`candidate_evidence_ids`를 반환한다. 물리 관계 수는 정상 결과
계약에서 제외하며 필요할 때만 `_debug.physical_hop_count`로 제공한다.

## 5. 역할·국가 복합 조회

왕과 국가처럼 관계 맥락이 필요한 경우 `RoleAssignment`를 사용한다.

```cypher
MATCH (correct:CanonicalEntity {canonical_id: $correct_canonical_id})
      -[:HAS_ROLE_ASSIGNMENT]->(correctAssignment:RoleAssignment {status: 'VERIFIED'})
MATCH (correctAssignment)-[:ROLE]->(role:PersonRole {
  search_eligible: true,
  review_status: 'VERIFIED'
})
MATCH (correctAssignment)-[:IN_POLITY]->(polity:Polity {
  search_eligible: true,
  review_status: 'VERIFIED'
})

MATCH (candidate:CanonicalEntity)
      -[:HAS_ROLE_ASSIGNMENT]->(candidateAssignment:RoleAssignment {status: 'VERIFIED'})
MATCH (candidateAssignment)-[:ROLE]->(role)
MATCH (candidateAssignment)-[:IN_POLITY]->(polity)

WHERE candidate.canonical_id <> correct.canonical_id
  AND NOT candidate.canonical_id IN $excluded_canonical_ids
  AND correct.resolution_status = 'ACCEPTED'
  AND candidate.entity_type_id = correct.entity_type_id
  AND candidate.resolution_status = 'ACCEPTED'
  AND correctAssignment.graph_release_id = $graph_release_id
  AND candidateAssignment.graph_release_id = $graph_release_id
RETURN candidate.canonical_id AS candidate_canonical_id,
       candidate.display_name AS candidate_name,
       candidate.entity_type_id AS entity_type_id,
       collect(DISTINCT {
         axis: 'role_context',
         path_pattern_id: 'SHARED_ROLE_ASSIGNMENT',
         role_id: role.anchor_id,
         role_name: role.name,
         polity_id: polity.anchor_id,
         polity_name: polity.name,
         correct_assignment_id: correctAssignment.assignment_id,
         candidate_assignment_id: candidateAssignment.assignment_id,
         taxonomy_distance: null,
         correct_evidence_ids: correctAssignment.evidence_ids,
         candidate_evidence_ids: candidateAssignment.evidence_ids,
         specificity_level: role.specificity_level
       }) AS shared_anchors
```

발문의도가 `같은 시대의 왕`까지 요구하면 두 assignment가 같은 세부 `Era`에도 연결됐는지
추가로 확인한다. 원천에 국가 또는 세부 시대 근거가 없으면 그 조건에 억지로 포함하지 않는다.

## 6. typed relation 조회

같은 역사 대상과 같은 의미의 관계를 가진 다른 주체를 찾을 수 있다.

```cypher
MATCH (correct:CanonicalEntity {canonical_id: $correct_canonical_id})
      -[correctRel:PARTICIPATED_IN]->(event:CanonicalEntity:Event)
MATCH (candidate:CanonicalEntity)
      -[candidateRel:PARTICIPATED_IN]->(event)
WHERE candidate.canonical_id <> correct.canonical_id
  AND NOT candidate.canonical_id IN $excluded_canonical_ids
  AND correct.resolution_status = 'ACCEPTED'
  AND candidate.resolution_status = 'ACCEPTED'
  AND event.resolution_status = 'ACCEPTED'
  AND correctRel.status = 'VERIFIED'
  AND candidateRel.status = 'VERIFIED'
  AND candidate.entity_type_id = correct.entity_type_id
  AND correctRel.graph_release_id = $graph_release_id
  AND candidateRel.graph_release_id = $graph_release_id
RETURN candidate.canonical_id AS candidate_canonical_id,
       candidate.display_name AS candidate_name,
       candidate.entity_type_id AS entity_type_id,
       collect(DISTINCT {
         axis: 'typed_relation',
         path_pattern_id: 'SHARED_TYPED_RELATION',
         predicate_id: type(correctRel),
         target_canonical_id: event.canonical_id,
         target_name: event.display_name,
         correct_relation_id: correctRel.relation_id,
         candidate_relation_id: candidateRel.relation_id,
         taxonomy_distance: null,
         correct_evidence_ids: correctRel.evidence_ids,
         candidate_evidence_ids: candidateRel.evidence_ids
       }) AS shared_anchors
```

이 예시는 활성 path pattern 카탈로그가 `PARTICIPATED_IN`을 허용한 경우에만 실행한다.
결과의 `path_pattern_id`는 `SHARED_TYPED_RELATION`, `taxonomy_distance`는 `null`이다.

`PARTICIPATED_IN`과 `COMMANDED`를 같은 관계로 취급하지 않는다. 문화재 관계도
`BUILT`, `ORDERED_CONSTRUCTION`, `PROMOTED_CONSTRUCTION`, `REBUILT`를 구분한다.

## 7. 후보 중복·오류 제외 순서

1. 동일 canonical ID 제외
2. 요청의 `excluded_canonical_ids`에 포함된 대상 제외
3. 정답의 승인 별칭이 별도 entity로 남은 중복 제외
4. entity type 불일치 제외
5. resolution/relationship status 미승인 제외
6. 관계 type·방향 불일치 제외
7. 요청 topic·era와 명백히 충돌하는 후보 제외
8. broad anchor만 공유한 후보 제외
9. 같은 실체의 source record가 여러 개인 경우 canonical ID로 deduplicate

정답과 후보가 같은 사건군·인물군에 속한다는 이유만으로 항상 제외하지는 않는다.
발문의도에 따라 유용한 공통점일 수 있으므로 group 관계의 의미를 명시해야 한다.

## 8. 후보 반환과 RAG 연결

후보마다 다음 값을 보존한다.

```text
candidate canonical ID
대표명과 승인 별칭
entity type
shared anchor ID·축·관계 type
정답·후보 관계의 분리된 evidence ID
polity·era·role·region 검색 맥락
source record ID
```

RAG query는 후보 이름만 사용하지 않는다. 동명이인 분리를 위해 승인 별칭과 맥락을
함께 넘긴다.

```text
후보 대표명 + 한자/별칭 + 시대 + 국가 + 역할/사건 + 발문의도
```

RAG가 후보를 뒷받침할 근거를 찾지 못하면 해당 후보는 최종 오답 생성에서 제외한다.
Graph의 공통 anchor 근거와 선지 내용을 지지하는 RAG 근거는 목적이 다르므로 둘 다
추적한다.

## 9. 랭킹 알고리즘의 경계

PageRank는 전역적으로 연결이 많은 유명 대상을 높이는 경향이 있어 기본 후보 자격을
판정하는 수단으로 적합하지 않다. 필요하면 검색 팀이 tie-breaker로 검토할 수 있다.

공통 노드 검색에는 inverse-degree anchor weight, weighted common neighbors,
Adamic-Adar 같은 방식이 더 직접적일 수 있다. 여러 hop의 연관 탐색이 필요해질 때는
Personalized PageRank를 실험할 수 있다.

이 문서는 어떤 알고리즘도 의무화하지 않는다. Graph 계약은 검증된 anchor와 후보 선정
이유를 제공하는 데서 끝난다.

## 10. 난이도

난이도는 문제 재료 또는 문제 생성 단계에서 결정한다. Neo4j가 난이도를 랜덤 선택하거나
확정하지 않는다. 검색 팀은 난이도를 승인된 `allowed_path_pattern_ids`,
`specificity_level`, `taxonomy_distance` 조건으로 변환한다. 물리 hop 수는 난이도에
사용하지 않는다. 난이도 정책은 Graph의 사실 관계나 분류를 변경하지 않는다.

## 11. 필수 테스트

- 같은 이름의 다른 시대 인물이 서로 후보 anchor를 오염시키지 않는다.
- 동일 인물의 AKS·ITKC·시소러스 레코드가 후보 세 개로 나오지 않는다.
- `조선 + 정치`만 공유한 대량 후보가 그대로 통과하지 않는다.
- `왕 + 조선` 역할 맥락은 통과하고 다른 국가의 왕은 요구 조건에 따라 제외된다.
- `사건인물` PENDING 관계는 참여자 검색에 쓰이지 않는다.
- `추진` 근거가 `BUILT` 관계 후보를 만들지 않는다.
- 후보 결과마다 `path_pattern_id`, `shared_anchors`, 양쪽 evidence ID가 존재한다.
- `excluded_canonical_ids`에 포함된 대상이 어떤 path pattern에서도 반환되지 않는다.
- DetailClass 계층 패턴의 `taxonomy_distance`가 상위 분류 지름길 edge 유무와 무관하다.
- 요청에 없는 path pattern과 카탈로그에 없는 임의 관계 경로가 결과에 없다.
- 최종 후보가 0건이어도 broad fallback으로 미검증 edge를 사용하지 않는다.

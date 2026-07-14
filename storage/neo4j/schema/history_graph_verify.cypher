MATCH (n)
UNWIND labels(n) AS node_label
RETURN node_label, count(n) AS node_count
ORDER BY node_label;

MATCH ()-[r]->()
RETURN type(r) AS relationship_type, count(r) AS relationship_count
ORDER BY relationship_type;

MATCH (n)
WHERE size(labels(n)) = 0
RETURN count(n) AS unlabeled_node_count;

MATCH ()-[r]->()
WHERE type(r) = ''
RETURN count(r) AS empty_relationship_type_count;

MATCH (n:SourceArticle)
WHERE NOT (n)-[:DESCRIBES]->(:CanonicalEntity)
RETURN count(n) AS source_article_without_canonical_entity_count;

MATCH (n:CanonicalEntity)
RETURN count(n) AS canonical_entity_count;

MATCH (:SourceArticle)-[r:DESCRIBES]->(:CanonicalEntity)
RETURN count(r) AS source_article_describes_entity_count;

MATCH (n:Polity)
WHERE NOT n:CanonicalEntity
RETURN count(n) AS polity_without_canonical_entity_label_count;

MATCH (n:Reign)
WHERE NOT (:CanonicalEntity)-[:HELD_REIGN]->(n)
RETURN count(n) AS reign_without_monarch_count;

MATCH (n:Reign)
WHERE NOT (n)-[:OF_POLITY]->(:Polity)
RETURN count(n) AS reign_without_polity_count;

MATCH (n:Reign)
WHERE NOT (:SourceArticle)-[:EVIDENCE_FOR]->(n)
RETURN count(n) AS reign_without_source_evidence_count;

MATCH (n:Reign)-[:OF_POLITY]->(p:Polity)
WITH n, count(DISTINCT p) AS polity_count
WHERE polity_count <> 1
RETURN count(n) AS reign_with_invalid_polity_count;

MATCH (n:Reign)
WHERE n.start_year IS NOT NULL
  AND n.end_year IS NOT NULL
  AND n.start_year > n.end_year
RETURN count(n) AS reign_with_invalid_year_range_count;

MATCH (n:Reign)-[:OF_POLITY]->(p:Polity)
WHERE n.review_status <> 'REVIEW_REQUIRED'
WITH p.polity_id AS polity_id,
     n.succession_order AS succession_order,
     collect(DISTINCT n.anchor_source_eid) AS source_eids
WHERE size(source_eids) > 1
RETURN count(*) AS unreviewed_duplicate_polity_order_count;

MATCH (n:Reign)
RETURN count(n) AS reign_count;

MATCH (n:Polity)
RETURN count(n) AS polity_count;

MATCH (n:RoyalAction)
WHERE NOT (:CanonicalEntity)-[:ASSOCIATED_WITH_ACTION]->(n)
RETURN count(n) AS royal_action_without_monarch_count;

MATCH (n:RoyalAction)
WHERE NOT (n)-[:TARGETS]->(:CanonicalEntity)
RETURN count(n) AS royal_action_without_target_count;

MATCH (n:RoyalAction)
WHERE NOT (n)-[:DURING_REIGN]->(:Reign)
RETURN count(n) AS royal_action_without_reign_count;

MATCH (n:RoyalAction)
WHERE NOT (:SourceArticle)-[:EVIDENCE_FOR]->(n)
RETURN count(n) AS royal_action_without_source_evidence_count;

MATCH (n:RoyalAction)-[:DURING_REIGN]->(r:Reign)
WITH n, count(DISTINCT r) AS reign_count
WHERE reign_count <> 1
RETURN count(n) AS royal_action_with_invalid_reign_count;

MATCH (n:RoyalAction)
WHERE n.start_year IS NOT NULL
  AND n.end_year IS NOT NULL
  AND n.start_year > n.end_year
RETURN count(n) AS royal_action_with_invalid_year_range_count;

MATCH (n:RoyalAction)
RETURN count(n) AS royal_action_count;

MATCH (n:CulturalHeritage)
WHERE NOT n:CanonicalEntity
RETURN count(n) AS cultural_heritage_without_canonical_entity_label_count;

MATCH (n:CulturalHeritage)
WHERE n.entity_type = '개념'
RETURN count(n) AS concept_labeled_as_cultural_heritage_count;

MATCH (n:CulturalHeritage)
WHERE NOT n.heritage_form IN ['PHYSICAL', 'DOCUMENT']
RETURN count(n) AS cultural_heritage_with_invalid_form_count;

MATCH (n:CulturalHeritage)
RETURN n.heritage_kind AS heritage_kind, count(n) AS heritage_count
ORDER BY heritage_count DESC, heritage_kind;

MATCH (n:CulturalHeritage)
RETURN count(n) AS cultural_heritage_count;

MATCH (n:SourceImage)
WHERE NOT n:SourceRecord OR n:CanonicalEntity OR n:CulturalHeritage
RETURN count(n) AS source_image_with_invalid_semantic_label_count;

MATCH (n:SourceImage)-[r:DEPICTS]->(:CanonicalEntity)
WHERE r.evidence_field <> 'title'
RETURN count(r) AS source_image_depicts_without_title_evidence_count;

MATCH (n:SourceImage)-[:DEPICTS]->(target:CanonicalEntity)
WITH n, count(DISTINCT target) AS target_count
WHERE target_count > 1
RETURN count(n) AS source_image_with_multiple_depicts_targets_count;

MATCH (n:SourceImage)
WHERE NOT n.local_file_available IN ['Y', 'N']
RETURN count(n) AS source_image_with_invalid_local_file_status_count;

MATCH (n:SourceImage)
WHERE n.local_file_available = 'N'
  AND trim(coalesce(n.original_url, '')) = ''
  AND trim(coalesce(n.thumbnail_url, '')) = ''
RETURN count(n) AS remote_only_source_image_without_remote_url_count;

MATCH (n:SourceImage)
RETURN count(n) AS source_image_count;

MATCH (:SourceImage)-[r:DEPICTS]->(:CanonicalEntity)
RETURN count(r) AS source_image_depicts_entity_count;

MATCH (n:InscriptionContent)
WHERE NOT n:Term OR n:CulturalHeritage OR n:SourceImage
RETURN count(n) AS inscription_content_with_invalid_semantic_label_count;

MATCH (n:InscriptionContent)
WHERE NOT (n)-[:INSCRIBED_ON]->(:CulturalHeritage)
RETURN count(n) AS inscription_content_without_physical_heritage_count;

MATCH (n:InscriptionContent)-[:INSCRIBED_ON]->(target:CulturalHeritage)
WITH n, count(DISTINCT target) AS target_count
WHERE target_count <> 1
RETURN count(n) AS inscription_content_with_invalid_target_count;

MATCH (n:SourceText)
WHERE NOT n:SourceRecord
   OR NOT (n)-[:PRESENTS_TEXT_OF]->(:InscriptionContent)
   OR n.translated_text IS NULL
   OR trim(n.translated_text) = ''
   OR n.original_text IS NULL
   OR trim(n.original_text) = ''
RETURN count(n) AS source_text_with_invalid_content_or_relation_count;

// ── 인물 관계 타입 분리 검증 (docs/neo4j/neo4j_관계_정규화_점검.md 발견 1) ──

// 유형별 인물 관계 건수. CSV의 normalized_relation_type 분포와 일치해야 한다.
MATCH (:Person)-[r]->(:Person)
RETURN type(r) AS person_relation_type, count(r) AS person_relation_count
ORDER BY person_relation_count DESC;

// catch-all(RELATED_TO)로 적재된 관계 수. 0이 아니면 CSV에 새 유형이 생긴 것이며
// import_relations.cypher에 해당 유형 블록을 추가해야 한다.
MATCH (:Person)-[r:RELATED_TO]->(:Person)
RETURN count(r) AS person_relation_catch_all_count,
       collect(DISTINCT r.normalized_relation_type)[..10] AS unhandled_relation_types;

// 엣지 타입과 normalized_relation_type 속성 불일치 검사. 0이어야 한다.
MATCH (:Person)-[r]->(:Person)
WHERE type(r) <> 'RELATED_TO'
  AND r.normalized_relation_type IS NOT NULL
  AND type(r) <> r.normalized_relation_type
RETURN count(r) AS person_relation_type_mismatch_count;

// person_relation_id 유일성 검사 (유형 분리 후에도 중복 적재가 없어야 한다). 0이어야 한다.
MATCH (:Person)-[r]->(:Person)
WHERE r.person_relation_id IS NOT NULL
WITH r.person_relation_id AS relation_id, count(r) AS loaded_count
WHERE loaded_count > 1
RETURN count(relation_id) AS duplicated_person_relation_id_count;

// 노드 속성-엣지 정합 표본 검사 (father_name 캐시가 남아 있는 동안만 의미).
// 불일치가 0이 아니면 속성 캐시가 엣지와 어긋난 것이다.
MATCH (c:Person)-[:HAS_FATHER]->(f:Person)
WHERE c.father_name IS NOT NULL
  AND trim(c.father_name) <> ''
  AND c.father_name <> f.name
RETURN count(c) AS father_name_property_mismatch_count;

// ── 기간 계층 검증 (SUBPERIOD_OF) ──

// parent_period_name이 있는 Period는 SUBPERIOD_OF 엣지가 있어야 한다. 0이어야 한다.
MATCH (p:Period)
WHERE p.parent_period_name IS NOT NULL
  AND trim(p.parent_period_name) <> ''
  AND NOT (p)-[:SUBPERIOD_OF]->(:Period)
RETURN count(p) AS period_with_parent_name_but_no_edge_count;

// SUBPERIOD_OF 엣지와 parent_period_name 속성 정합. 0이어야 한다.
MATCH (p:Period)-[:SUBPERIOD_OF]->(parent:Period)
WHERE p.parent_period_name <> parent.name
RETURN count(p) AS subperiod_parent_mismatch_count;

// 기간 계층 순환 검사. 0이어야 한다.
MATCH (p:Period)
WHERE (p)-[:SUBPERIOD_OF*1..5]->(p)
RETURN count(p) AS period_hierarchy_cycle_count;

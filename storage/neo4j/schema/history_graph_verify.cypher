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
WHERE coalesce(n.review_status, '') <> 'REVIEW_REQUIRED'
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
WHERE coalesce(r.evidence_field, '') <> 'title'
RETURN count(r) AS source_image_depicts_without_title_evidence_count;

MATCH (n:SourceImage)-[:DEPICTS]->(target:CanonicalEntity)
WITH n, count(DISTINCT target) AS target_count
WHERE target_count > 1
RETURN count(n) AS source_image_with_multiple_depicts_targets_count;

MATCH (n:SourceImage)
WHERE NOT coalesce(n.local_file_available, '') IN ['Y', 'N']
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

MATCH (:SourceImage)-[r:HAS_RELATED_CONTENT]->(:SourceUrl)
RETURN count(r) AS source_image_related_content_count;

MATCH (image:SourceImage)-[r:HAS_RELATED_CONTENT]->(url:SourceUrl)
WHERE trim(coalesce(r.content_title, '')) = ''
   OR coalesce(r.mapping_method, '') <> 'SOURCE_DECLARED_URL'
   OR coalesce(r.review_status, '') <> 'SOURCE_ANCHORED'
   OR trim(coalesce(url.url, '')) = ''
RETURN count(r) AS invalid_source_image_related_content_count;

MATCH (image:SourceImage)-[r:HAS_RELATED_CONTENT]->(url:SourceUrl)
WITH image.source_image_id AS image_id, url.source_url_id AS url_id, count(r) AS loaded_count
WHERE loaded_count > 1
RETURN count(*) AS duplicated_source_image_related_content_count;

MATCH (url:SourceUrl)
WHERE 'IMAGE_RELATED_CONTENT' IN split(coalesce(url.source_types, ''), '|')
  AND NOT (:SourceImage)-[:HAS_RELATED_CONTENT]->(url)
RETURN count(url) AS image_related_source_url_without_relation_count;

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

// catch-all(RELATED_TO)로 적재된 관계 수. 0이 아니면 seed에 미등록 유형이 생긴 것이다.
MATCH (:Person)-[r:RELATED_TO]->(:Person)
RETURN count(r) AS person_relation_catch_all_count,
       collect(DISTINCT r.normalized_relation_type)[..10] AS unhandled_relation_types;

// 엣지 타입과 normalized_relation_type 속성 불일치 검사. 0이어야 한다.
MATCH (:Person)-[r]->(:Person)
WHERE type(r) <> coalesce(r.normalized_relation_type, '')
RETURN count(r) AS person_relation_type_mismatch_count;

// person_relation_id 유일성 검사 (유형 분리 후에도 중복 적재가 없어야 한다). 0이어야 한다.
MATCH (:Person)-[r]->(:Person)
WHERE r.person_relation_id IS NOT NULL
WITH r.person_relation_id AS relation_id, count(r) AS loaded_count
WHERE loaded_count > 1
RETURN count(relation_id) AS duplicated_person_relation_id_count;

// 대칭 관계는 ID가 작은 쪽에서 큰 쪽으로 한 번만 저장한다. 0이어야 한다.
MATCH (start:Person)-[r]->(target:Person)
WHERE r.is_symmetric = 'Y' AND start.person_id >= target.person_id
RETURN count(r) AS invalid_symmetric_person_relation_direction_count;

MATCH (start:Person)-[r]->(target:Person)
WHERE r.is_symmetric = 'Y'
WITH type(r) AS relation_type,
     start.person_id + '|' + target.person_id AS endpoint_pair,
     count(r) AS loaded_count
WHERE loaded_count > 1
RETURN count(*) AS duplicated_symmetric_person_relation_count;

// ── 제거된 중복 관계 속성의 잔존 검사 (전부 0이어야 한다) ──
// 관계성 컬럼은 노드 속성에서 제거하고 엣지로만 표현한다 (점검 문서 발견 2·4).
// 0이 아니면 옛 CSV로 적재된 것이므로 재생성·재적재가 필요하다.

MATCH (n:Person) WHERE n.father_name IS NOT NULL
RETURN count(n) AS removed_father_name_residue_count;

MATCH (n:Person) WHERE n.degree IS NOT NULL
RETURN count(n) AS removed_person_degree_residue_count;

MATCH (p:Person)
CALL (p) {
    MATCH (p)-[r]-(other:Person)
    WHERE r.person_relation_id IS NOT NULL
    RETURN count(r) AS person_relation_degree
}
CALL (p) {
    MATCH (p)-[r:INVOLVED_IN]->(:Event)
    RETURN count(r) AS event_relation_degree
}
WITH p, person_relation_degree, event_relation_degree
WHERE coalesce(p.core_relation_degree, 0)
   <> person_relation_degree + event_relation_degree
RETURN count(p) AS person_core_relation_degree_mismatch_count;

MATCH (n:RoyalAction)
WHERE n.monarch_name IS NOT NULL OR n.target_name IS NOT NULL OR n.target_kind IS NOT NULL
RETURN count(n) AS removed_royal_action_name_residue_count;

MATCH (n:Term) WHERE n.topterm_id IS NOT NULL
RETURN count(n) AS removed_topterm_id_residue_count;

MATCH (n:Period) WHERE n.parent_period_name IS NOT NULL
RETURN count(n) AS removed_parent_period_name_residue_count;

MATCH (n:CanonicalCategory)
WHERE n.parent_category_id IS NOT NULL OR n.parent_category_path IS NOT NULL
   OR n.root_category_name IS NOT NULL
RETURN count(n) AS removed_category_parent_residue_count;

MATCH (n:Region)
WHERE n.parent_region_id IS NOT NULL OR n.parent_region_name IS NOT NULL
   OR n.canonical_category_id IS NOT NULL OR n.canonical_category_path IS NOT NULL
RETURN count(n) AS removed_region_reference_residue_count;

MATCH (n:Country)
WHERE n.canonical_category_id IS NOT NULL OR n.canonical_category_path IS NOT NULL
RETURN count(n) AS removed_country_reference_residue_count;

MATCH (n:EconomicDomain)
WHERE n.canonical_category_id IS NOT NULL OR n.canonical_category_path IS NOT NULL
RETURN count(n) AS removed_economic_domain_reference_residue_count;

MATCH (n:TaxonomyFacet)
WHERE n.canonical_category_id IS NOT NULL OR n.canonical_category_path IS NOT NULL
RETURN count(n) AS removed_taxonomy_facet_reference_residue_count;

MATCH (n:SourceImage) WHERE n.related_content IS NOT NULL
RETURN count(n) AS removed_source_image_related_content_residue_count;

// ── 기간 계층 검증 (SUBPERIOD_OF) ──

// 기간 계층 엣지 건수 (기대: 21 — 생성 시점 기준)
MATCH (:Period)-[r:SUBPERIOD_OF]->(:Period)
RETURN count(r) AS period_subperiod_of_count;

// 기간 계층 순환 검사. 0이어야 한다.
MATCH (p:Period)
WHERE (p)-[:SUBPERIOD_OF*1..5]->(p)
RETURN count(p) AS period_hierarchy_cycle_count;

MATCH (child:Period)-[r:SUBPERIOD_OF]->(parent:Period)
WHERE coalesce(r.period_name, '') <> coalesce(child.name, '')
   OR coalesce(r.parent_period_name, '') <> coalesce(parent.name, '')
RETURN count(r) AS period_hierarchy_name_mismatch_count;

// ── 이벤트-재위·관련사건 엣지 검증 ──

// 이벤트 재위 엣지 건수 (기대: started 444 / ended 445 — 생성 시점 기준)
MATCH (:Event)-[r:STARTED_DURING_REIGN]->(:Reign)
RETURN count(r) AS event_started_during_reign_count;
MATCH (:Event)-[r:ENDED_DURING_REIGN]->(:Reign)
RETURN count(r) AS event_ended_during_reign_count;

// 재위 엣지-속성 정합: 엣지의 재위 이름이 이벤트의 왕호로 시작해야 한다. 0이어야 한다.
MATCH (e:Event)-[r:STARTED_DURING_REIGN]->(g:Reign)
WHERE NOT coalesce(g.name, '') STARTS WITH (coalesce(e.start_reign_name, '') + '의 ')
RETURN count(e) AS event_start_reign_mismatch_count;

MATCH (e:Event)-[r:ENDED_DURING_REIGN]->(g:Reign)
WHERE NOT coalesce(g.name, '') STARTS WITH (coalesce(e.end_reign_name, '') + '의 ')
RETURN count(e) AS event_end_reign_mismatch_count;

// match_method와 무관하게 이벤트 연도가 매칭된 재위 기간을 벗어나면 안 된다.
MATCH (e:Event)-[r:STARTED_DURING_REIGN]->(g:Reign)
WHERE e.start_year IS NOT NULL
  AND (
      g.start_year IS NULL
      OR g.end_year IS NULL
      OR e.start_year < g.start_year
      OR e.start_year > g.end_year
  )
RETURN count(e) AS event_reign_year_out_of_range_count;

MATCH (e:Event)-[r:ENDED_DURING_REIGN]->(g:Reign)
WITH e, g, coalesce(e.end_year, e.start_year) AS event_year
WHERE event_year IS NOT NULL
  AND (
      g.start_year IS NULL
      OR g.end_year IS NULL
      OR event_year < g.start_year
      OR event_year > g.end_year
  )
RETURN count(e) AS event_end_reign_year_out_of_range_count;

// 관련 사건 문자열은 EventGroup으로 묶고, 유일 이름 일치 Term만 후보로 연결한다.
MATCH (:EventGroup)-[r:HAS_TERM_CANDIDATE]->(:Term)
RETURN count(r) AS event_group_has_term_candidate_count;

MATCH (group:EventGroup)-[r:HAS_TERM_CANDIDATE]->(term:Term)
WHERE coalesce(group.name, '') <> coalesce(term.name, '')
   OR coalesce(r.match_method, '') <> 'UNIQUE_TERM_NAME'
   OR coalesce(r.review_status, '') <> 'AUTO_CANDIDATE'
   OR coalesce(r.answer_eligible, '') <> 'N'
RETURN count(r) AS invalid_event_group_term_candidate_count;

MATCH (group:EventGroup)-[:HAS_TERM_CANDIDATE]->(term:Term)
WITH group, count(DISTINCT term) AS target_count
WHERE target_count > 1
RETURN count(group) AS event_group_with_multiple_term_candidates_count;

MATCH (:Event)-[r:HAS_RELATED_EVENT]->(:Term)
RETURN count(r) AS discontinued_event_has_related_event_count;

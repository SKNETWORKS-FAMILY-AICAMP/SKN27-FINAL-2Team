LOAD CSV WITH HEADERS FROM 'file:///relations/term_has_canonical_category.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:CanonicalCategory {category_id: row.end_category_id})
    MERGE (start)-[r:HAS_CATEGORY]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/source_article_describes_entity.csv' AS row
CALL (row) {
    MATCH (start:SourceRecord {source_record_id: row.start_source_record_id})
    MATCH (target:CanonicalEntity {canonical_id: row.end_canonical_id})
    MERGE (start)-[r:DESCRIBES]->(target)
    SET r += row
    SET r.confidence = toFloatOrNull(row.confidence)
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/monarch_held_reign.csv' AS row
CALL (row) {
    MATCH (start:CanonicalEntity {canonical_id: row.start_canonical_id})
    MATCH (target:Reign {reign_id: row.end_reign_id})
    MERGE (start)-[r:HELD_REIGN]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/reign_of_polity.csv' AS row
CALL (row) {
    MATCH (start:Reign {reign_id: row.start_reign_id})
    MATCH (target:Polity {polity_id: row.end_polity_id})
    MERGE (start)-[r:OF_POLITY]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/source_article_evidence_for_reign.csv' AS row
CALL (row) {
    MATCH (start:SourceRecord {source_record_id: row.start_source_record_id})
    MATCH (target:Reign {reign_id: row.end_reign_id})
    MERGE (start)-[r:EVIDENCE_FOR]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/monarch_associated_with_royal_action.csv' AS row
CALL (row) {
    MATCH (start:CanonicalEntity {canonical_id: row.start_canonical_id})
    MATCH (target:RoyalAction {action_id: row.end_action_id})
    MERGE (start)-[r:ASSOCIATED_WITH_ACTION]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/royal_action_targets_entity.csv' AS row
CALL (row) {
    MATCH (start:RoyalAction {action_id: row.start_action_id})
    MATCH (target:CanonicalEntity {canonical_id: row.end_canonical_id})
    MERGE (start)-[r:TARGETS]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/royal_action_during_reign.csv' AS row
CALL (row) {
    MATCH (start:RoyalAction {action_id: row.start_action_id})
    MATCH (target:Reign {reign_id: row.end_reign_id})
    MERGE (start)-[r:DURING_REIGN]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/source_article_evidence_for_royal_action.csv' AS row
CALL (row) {
    MATCH (start:SourceRecord {source_record_id: row.start_source_record_id})
    MATCH (target:RoyalAction {action_id: row.end_action_id})
    MERGE (start)-[r:EVIDENCE_FOR]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/source_image_depicts_entity.csv' AS row
CALL (row) {
    MATCH (start:SourceImage {source_image_id: row.source_image_id})
    MATCH (target:CanonicalEntity {canonical_id: row.canonical_id})
    MERGE (start)-[r:DEPICTS]->(target)
    SET r.mapping_method = row.mapping_method,
        r.evidence_field = row.evidence_field,
        r.evidence_text = row.evidence_text,
        r.review_status = row.review_status
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/source_image_has_related_content.csv' AS row
CALL (row) {
    MATCH (start:SourceImage {source_image_id: row.source_image_id})
    MATCH (target:SourceUrl {source_url_id: row.source_url_id})
    MERGE (start)-[r:HAS_RELATED_CONTENT]->(target)
    SET r.content_title = row.content_title,
        r.content_collection = row.content_collection,
        r.mapping_method = row.mapping_method,
        r.review_status = row.review_status
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/inscription_content_inscribed_on.csv' AS row
CALL (row) {
    MATCH (start:InscriptionContent {inscription_id: row.inscription_id})
    MATCH (target:CulturalHeritage {canonical_id: row.canonical_id})
    MERGE (start)-[r:INSCRIBED_ON]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/source_text_presents_inscription.csv' AS row
CALL (row) {
    MATCH (start:SourceText {source_text_id: row.source_text_id})
    MATCH (target:InscriptionContent {inscription_id: row.inscription_id})
    MERGE (start)-[r:PRESENTS_TEXT_OF]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_in_period.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:Period {period_id: row.end_period_id})
    MERGE (start)-[r:IN_PERIOD]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_about_country.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:Country {country_id: row.end_country_id})
    MERGE (start)-[r:ABOUT_COUNTRY]->(target)
    SET r.country_name = row.country_name,
        r.canonical_category_id = row.canonical_category_id,
        r.canonical_category_path = row.canonical_category_path,
        r.match_type = row.match_type
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_about_region.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:Region {region_id: row.end_region_id})
    MERGE (start)-[r:ABOUT_REGION]->(target)
    SET r.region_name = row.region_name,
        r.region_type = row.region_type,
        r.canonical_category_id = row.canonical_category_id,
        r.canonical_category_path = row.canonical_category_path,
        r.match_type = row.match_type
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_about_economic_domain.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:EconomicDomain {economic_domain_id: row.end_economic_domain_id})
    MERGE (start)-[r:ABOUT_ECONOMIC_DOMAIN]->(target)
    SET r.economic_domain_name = row.economic_domain_name,
        r.canonical_category_id = row.canonical_category_id,
        r.canonical_category_path = row.canonical_category_path,
        r.match_type = row.match_type
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_about_taxonomy_facet.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:TaxonomyFacet {taxonomy_facet_id: row.end_taxonomy_facet_id})
    MERGE (start)-[r:ABOUT_TAXONOMY_FACET]->(target)
    SET r.taxonomy_facet_name = row.taxonomy_facet_name,
        r.taxonomy_facet_path = row.taxonomy_facet_path,
        r.canonical_category_id = row.canonical_category_id,
        r.canonical_category_path = row.canonical_category_path,
        r.match_type = row.match_type
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_has_source_category.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:SourceEventCategory {event_category_id: row.end_event_category_id})
    MERGE (start)-[r:HAS_EVENT_CATEGORY]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_has_canonical_category.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:CanonicalCategory {category_id: row.end_category_id})
    MERGE (start)-[r:HAS_CATEGORY {event_category_id: row.event_category_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_has_facet.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:EventFacet {event_facet_id: row.end_event_facet_id})
    MERGE (start)-[r:HAS_EVENT_FACET {source_event_category_id: row.source_event_category_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_in_period.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:Period {period_id: row.end_period_id})
    MERGE (start)-[r:IN_PERIOD]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_part_of_event_group.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:EventGroup {event_group_id: row.end_event_group_id})
    MERGE (start)-[r:PART_OF_EVENT_GROUP]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_has_source_url.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:SourceUrl {source_url_id: row.end_source_url_id})
    MERGE (start)-[r:HAS_SOURCE_URL {source_column: row.source_column}]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_has_search_tag.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:SearchTag {search_tag_id: row.end_search_tag_id})
    MERGE (start)-[r:HAS_SEARCH_TAG {
        source_node_type: row.source_node_type,
        source_node_id: row.source_node_id,
        source_relation: row.source_relation
    }]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_has_search_tag.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:SearchTag {search_tag_id: row.end_search_tag_id})
    MERGE (start)-[r:HAS_SEARCH_TAG {
        source_node_type: row.source_node_type,
        source_node_id: row.source_node_id,
        source_relation: row.source_relation
    }]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_has_search_tag.csv' AS row
CALL (row) {
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:SearchTag {search_tag_id: row.end_search_tag_id})
    MERGE (start)-[r:HAS_SEARCH_TAG {
        source_node_type: row.source_node_type,
        source_node_id: row.source_node_id,
        source_relation: row.source_relation
    }]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_about_country.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:Country {country_id: row.end_country_id})
    MERGE (start)-[r:ABOUT_COUNTRY]->(target)
    SET r.country_name = row.country_name,
        r.canonical_category_id = row.canonical_category_id,
        r.canonical_category_path = row.canonical_category_path,
        r.match_type = row.match_type
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_about_taxonomy_facet.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:TaxonomyFacet {taxonomy_facet_id: row.end_taxonomy_facet_id})
    MERGE (start)-[r:ABOUT_TAXONOMY_FACET]->(target)
    SET r.taxonomy_facet_name = row.taxonomy_facet_name,
        r.taxonomy_facet_path = row.taxonomy_facet_path,
        r.canonical_category_id = row.canonical_category_id,
        r.canonical_category_path = row.canonical_category_path,
        r.match_type = row.match_type
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_involved_in_event.csv' AS row
CALL (row) {
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Event {event_id: row.end_event_id})
    MERGE (start)-[r:INVOLVED_IN {event_person_relation_id: row.event_person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

// 인물 간 관계: relation_type_seed.csv에서 승인된 neo4j_rel_type을 CSV에 기록하고
// Neo4j 5.26의 동적 관계 타입으로 한 번만 읽어 적재한다. seed에 없는 원천 유형은
// 사전 생성 단계에서 RELATED_TO로 명시되며 QA에서 실패 대상으로 검출한다.
LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:$(row.relation_type) {person_relation_id: row.person_relation_id}]->(target)
    SET r.raw_relation_type = row.raw_relation_type,
        r.normalized_relation_type = row.normalized_relation_type,
        r.relation_group = row.relation_group,
        r.direction_rule = row.direction_rule,
        r.is_symmetric = row.is_symmetric,
        r.inverse_relation_type = row.inverse_relation_type,
        r.evidence_url = row.evidence_url
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_refers_to_person.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:REFERS_TO]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_mentions_person.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:MENTIONS_PERSON {
        source_field: row.source_field,
        matched_name: row.matched_name
    }]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_refers_to_event.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:Event {event_id: row.end_event_id})
    MERGE (start)-[r:REFERS_TO]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_has_source_url.csv' AS row
CALL (row) {
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:SourceUrl {source_url_id: row.end_source_url_id})
    MERGE (start)-[r:HAS_SOURCE_URL {source_column: row.source_column}]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/canonical_category_subcategory_of.csv' AS row
CALL (row) {
    MATCH (start:CanonicalCategory {category_id: row.start_category_id})
    MATCH (target:CanonicalCategory {category_id: row.end_category_id})
    MERGE (start)-[r:SUBCATEGORY_OF]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/source_category_mapped_to_canonical_category.csv' AS row
CALL (row) {
    MATCH (start:SourceEventCategory {event_category_id: row.start_event_category_id})
    MATCH (target:CanonicalCategory {category_id: row.end_category_id})
    MERGE (start)-[r:MAPPED_TO_CATEGORY]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/canonical_category_about_country.csv' AS row
CALL (row) {
    MATCH (start:CanonicalCategory {category_id: row.start_category_id})
    MATCH (target:Country {country_id: row.end_country_id})
    MERGE (start)-[r:ABOUT_COUNTRY]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/canonical_category_about_region.csv' AS row
CALL (row) {
    MATCH (start:CanonicalCategory {category_id: row.start_category_id})
    MATCH (target:Region {region_id: row.end_region_id})
    MERGE (start)-[r:ABOUT_REGION]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/canonical_category_about_economic_domain.csv' AS row
CALL (row) {
    MATCH (start:CanonicalCategory {category_id: row.start_category_id})
    MATCH (target:EconomicDomain {economic_domain_id: row.end_economic_domain_id})
    MERGE (start)-[r:ABOUT_ECONOMIC_DOMAIN]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/canonical_category_about_taxonomy_facet.csv' AS row
CALL (row) {
    MATCH (start:CanonicalCategory {category_id: row.start_category_id})
    MATCH (target:TaxonomyFacet {taxonomy_facet_id: row.end_taxonomy_facet_id})
    MERGE (start)-[r:ABOUT_TAXONOMY_FACET]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/region_subregion_of.csv' AS row
CALL (row) {
    MATCH (start:Region {region_id: row.start_region_id})
    MATCH (target:Region {region_id: row.end_region_id})
    MERGE (start)-[r:SUBREGION_OF]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/canonical_category_has_theme.csv' AS row
CALL (row) {
    MATCH (start:CanonicalCategory {category_id: row.start_category_id})
    MATCH (target:Theme {theme_id: row.end_theme_id})
    MERGE (start)-[r:HAS_THEME]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_has_theme.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:Theme {theme_id: row.end_theme_id})
    MERGE (start)-[r:HAS_THEME]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_has_theme.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:Theme {theme_id: row.end_theme_id})
    MERGE (start)-[r:HAS_THEME]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_has_theme.csv' AS row
CALL (row) {
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Theme {theme_id: row.end_theme_id})
    MERGE (start)-[r:HAS_THEME]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_in_era.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:Era {era_id: row.end_era_id})
    MERGE (start)-[r:IN_ERA]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/period_part_of_era.csv' AS row
CALL (row) {
    MATCH (start:Period {period_id: row.start_period_id})
    MATCH (target:Era {era_id: row.end_era_id})
    MERGE (start)-[r:PART_OF_ERA]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

// 기간 계층 (하위 기간 → 상위 기간). parent_period_name 노드 속성으로만 있던
// 관계를 엣지화한 것 (docs/neo4j/neo4j_관계_정규화_점검.md 발견 3).
LOAD CSV WITH HEADERS FROM 'file:///relations/period_subperiod_of.csv' AS row
CALL (row) {
    MATCH (start:Period {period_id: row.start_period_id})
    MATCH (target:Period {period_id: row.end_period_id})
    MERGE (start)-[r:SUBPERIOD_OF]->(target)
    SET r.period_name = row.period_name,
        r.parent_period_name = row.parent_period_name
} IN TRANSACTIONS OF 1000 ROWS;

// 이벤트 발생 재위. start_reign_name/end_reign_name 노드 속성으로만 있던
// 관계를 왕호+연도 매칭으로 엣지화한 것 (점검 문서 발견 3).
LOAD CSV WITH HEADERS FROM 'file:///relations/event_started_during_reign.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:Reign {reign_id: row.end_reign_id})
    MERGE (start)-[r:STARTED_DURING_REIGN]->(target)
    SET r.event_name = row.event_name,
        r.reign_name = row.reign_name,
        r.match_method = row.match_method
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_ended_during_reign.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:Reign {reign_id: row.end_reign_id})
    MERGE (start)-[r:ENDED_DURING_REIGN]->(target)
    SET r.event_name = row.event_name,
        r.reign_name = row.reign_name,
        r.match_method = row.match_method
} IN TRANSACTIONS OF 1000 ROWS;

// Event의 related_event_name은 같은 이름을 공유하는 EventGroup으로 이미 구조화된다.
// 그룹명과 유일 Term 이름이 정확히 일치할 때만 후보 링크를 만들며 정답 근거로는 쓰지 않는다.
LOAD CSV WITH HEADERS FROM 'file:///relations/event_group_has_term_candidate.csv' AS row
CALL (row) {
    MATCH (start:EventGroup {event_group_id: row.start_event_group_id})
    MATCH (target:Term {term_id: row.end_term_id})
    MERGE (start)-[r:HAS_TERM_CANDIDATE]->(target)
    SET r.match_method = row.match_method,
        r.review_status = row.review_status,
        r.answer_eligible = row.answer_eligible
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_has_entity_type.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:EntityType {entity_type_id: row.end_entity_type_id})
    MERGE (start)-[r:HAS_ENTITY_TYPE]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_in_era.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:Era {era_id: row.end_era_id})
    MERGE (start)-[r:IN_ERA]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_in_era.csv' AS row
CALL (row) {
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Era {era_id: row.end_era_id})
    MERGE (start)-[r:IN_ERA]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

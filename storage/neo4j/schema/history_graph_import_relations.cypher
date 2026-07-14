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
    SET r += row
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
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_about_region.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:Region {region_id: row.end_region_id})
    MERGE (start)-[r:ABOUT_REGION]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_about_economic_domain.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:EconomicDomain {economic_domain_id: row.end_economic_domain_id})
    MERGE (start)-[r:ABOUT_ECONOMIC_DOMAIN]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/term_about_taxonomy_facet.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:TaxonomyFacet {taxonomy_facet_id: row.end_taxonomy_facet_id})
    MERGE (start)-[r:ABOUT_TAXONOMY_FACET]->(target)
    SET r += row
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
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/event_about_taxonomy_facet.csv' AS row
CALL (row) {
    MATCH (start:Event {event_id: row.start_event_id})
    MATCH (target:TaxonomyFacet {taxonomy_facet_id: row.end_taxonomy_facet_id})
    MERGE (start)-[r:ABOUT_TAXONOMY_FACET]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_involved_in_event.csv' AS row
CALL (row) {
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Event {event_id: row.end_event_id})
    MERGE (start)-[r:INVOLVED_IN {event_person_relation_id: row.event_person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 1000 ROWS;

// 인물 간 관계: normalized_relation_type을 엣지 타입으로 적재한다.
// 관계 종류는 엣지 속성이 아니라 타입이어야 타입 패턴 매칭이 가능하다
// (docs/neo4j/neo4j_관계_정규화_점검.md 발견 1).
// 순수 Cypher는 MERGE에 동적 타입을 쓸 수 없어 유형별 블록으로 나눈다.
// 아래 목록은 person_related_to_person.csv의 normalized_relation_type 전체와
// 일치해야 하며, 새 유형 추가 시 블록을 추가한다. 목록 밖 유형은
// 마지막 catch-all 블록이 RELATED_TO로 적재해 유실을 막는다.

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_FATHER'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_FATHER {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_MOTHER'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_MOTHER {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_BIOLOGICAL_FATHER'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_BIOLOGICAL_FATHER {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_BIOLOGICAL_MOTHER'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_BIOLOGICAL_MOTHER {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_CHILD'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_CHILD {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_GRANDFATHER'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_GRANDFATHER {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_GREAT_GRANDFATHER'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_GREAT_GRANDFATHER {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_FATHER_IN_LAW'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_FATHER_IN_LAW {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_SON_IN_LAW'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_SON_IN_LAW {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_HUSBAND'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_HUSBAND {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_WIFE'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_WIFE {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'SIBLING_OF'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:SIBLING_OF {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'LINEAGE_RELATED'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:LINEAGE_RELATED {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_TEACHER'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_TEACHER {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'HAS_STUDENT'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:HAS_STUDENT {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE row.normalized_relation_type = 'ASSOCIATED_WITH'
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:ASSOCIATED_WITH {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
} IN TRANSACTIONS OF 250 ROWS;

// catch-all: 위 유형 목록에 없는 신규/미정규화 유형은 RELATED_TO로 적재해 유실을 막는다.
// 반입 후 검증에서 RELATED_TO 건수가 0이 아니면 유형 블록 추가가 필요하다는 신호다.
LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
    WITH row WHERE NOT row.normalized_relation_type IN [
        'HAS_FATHER', 'HAS_MOTHER', 'HAS_BIOLOGICAL_FATHER', 'HAS_BIOLOGICAL_MOTHER',
        'HAS_CHILD', 'HAS_GRANDFATHER', 'HAS_GREAT_GRANDFATHER',
        'HAS_FATHER_IN_LAW', 'HAS_SON_IN_LAW', 'HAS_HUSBAND', 'HAS_WIFE',
        'SIBLING_OF', 'LINEAGE_RELATED', 'HAS_TEACHER', 'HAS_STUDENT', 'ASSOCIATED_WITH'
    ]
    MATCH (start:Person {person_id: row.start_person_id})
    MATCH (target:Person {person_id: row.end_person_id})
    MERGE (start)-[r:RELATED_TO {person_relation_id: row.person_relation_id}]->(target)
    SET r += row
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
    SET r += row
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

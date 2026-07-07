LOAD CSV WITH HEADERS FROM 'file:///relations/term_has_canonical_category.csv' AS row
CALL (row) {
    MATCH (start:Term {term_id: row.start_term_id})
    MATCH (target:CanonicalCategory {category_id: row.end_category_id})
    MERGE (start)-[r:HAS_CATEGORY]->(target)
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

LOAD CSV WITH HEADERS FROM 'file:///relations/person_related_to_person.csv' AS row
CALL (row) {
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

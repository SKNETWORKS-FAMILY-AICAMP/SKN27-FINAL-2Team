CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/terms.csv' AS row
    WITH row WHERE row.term_id IS NOT NULL AND trim(row.term_id) <> ''
    MERGE (n:Term {term_id: row.term_id})
    SET n += row
    SET n.topterm_id = toIntegerOrNull(row.topterm_id)
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/events.csv' AS row
    WITH row WHERE row.event_id IS NOT NULL AND trim(row.event_id) <> ''
    MERGE (n:Event {event_id: row.event_id})
    SET n += row
    SET n.start_year = toIntegerOrNull(row.start_year),
        n.end_year = toIntegerOrNull(row.end_year),
        n.start_month = toIntegerOrNull(row.start_month),
        n.end_month = toIntegerOrNull(row.end_month),
        n.start_reign_year = toIntegerOrNull(row.start_reign_year),
        n.end_reign_year = toIntegerOrNull(row.end_reign_year)
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/people.csv' AS row
    WITH row WHERE row.person_id IS NOT NULL AND trim(row.person_id) <> ''
    MERGE (n:Person {person_id: row.person_id})
    SET n += row
    SET n.birth_year = toIntegerOrNull(row.birth_year),
        n.death_year = toIntegerOrNull(row.death_year)
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/canonical_categories.csv' AS row
    WITH row WHERE row.category_id IS NOT NULL AND trim(row.category_id) <> ''
    MERGE (n:CanonicalCategory {category_id: row.category_id})
    SET n += row
    SET n.depth = toIntegerOrNull(row.depth),
        n.term_count = toIntegerOrNull(row.term_count),
        n.direct_term_count = toIntegerOrNull(row.direct_term_count)
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/source_event_categories.csv' AS row
    WITH row WHERE row.event_category_id IS NOT NULL AND trim(row.event_category_id) <> ''
    MERGE (n:SourceEventCategory {event_category_id: row.event_category_id})
    SET n += row
    SET n.event_count = toIntegerOrNull(row.event_count)
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/periods.csv' AS row
    WITH row WHERE row.period_id IS NOT NULL AND trim(row.period_id) <> ''
    MERGE (n:Period {period_id: row.period_id})
    SET n += row
    SET n.period_order = toIntegerOrNull(row.period_order),
        n.start_year = toIntegerOrNull(row.start_year),
        n.end_year = toIntegerOrNull(row.end_year),
        n.term_count = toIntegerOrNull(row.term_count),
        n.event_count = toIntegerOrNull(row.event_count)
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/source_urls.csv' AS row
    WITH row WHERE row.source_url_id IS NOT NULL AND trim(row.source_url_id) <> ''
    MERGE (n:SourceUrl {source_url_id: row.source_url_id})
    SET n += row
    SET n.source_count = toIntegerOrNull(row.source_count)
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/event_groups.csv' AS row
    WITH row WHERE row.event_group_id IS NOT NULL AND trim(row.event_group_id) <> ''
    MERGE (n:EventGroup {event_group_id: row.event_group_id})
    SET n += row
    SET n.event_count = toIntegerOrNull(row.event_count)
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/event_facets.csv' AS row
    WITH row WHERE row.event_facet_id IS NOT NULL AND trim(row.event_facet_id) <> ''
    MERGE (n:EventFacet {event_facet_id: row.event_facet_id})
    SET n += row
    SET n.source_event_category_count = toIntegerOrNull(row.source_event_category_count),
        n.event_count = toIntegerOrNull(row.event_count)
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/countries.csv' AS row
    WITH row WHERE row.country_id IS NOT NULL AND trim(row.country_id) <> ''
    MERGE (n:Country {country_id: row.country_id})
    SET n += row
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/regions.csv' AS row
    WITH row WHERE row.region_id IS NOT NULL AND trim(row.region_id) <> ''
    MERGE (n:Region {region_id: row.region_id})
    SET n += row
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/economic_domains.csv' AS row
    WITH row WHERE row.economic_domain_id IS NOT NULL AND trim(row.economic_domain_id) <> ''
    MERGE (n:EconomicDomain {economic_domain_id: row.economic_domain_id})
    SET n += row
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/taxonomy_facets.csv' AS row
    WITH row WHERE row.taxonomy_facet_id IS NOT NULL AND trim(row.taxonomy_facet_id) <> ''
    MERGE (n:TaxonomyFacet {taxonomy_facet_id: row.taxonomy_facet_id})
    SET n += row
    SET n.taxonomy_facet_depth = toIntegerOrNull(row.taxonomy_facet_depth),
        n.child_category_count = toIntegerOrNull(row.child_category_count),
        n.descendant_category_count = toIntegerOrNull(row.descendant_category_count),
        n.term_count = toIntegerOrNull(row.term_count),
        n.direct_term_count = toIntegerOrNull(row.direct_term_count)
} IN TRANSACTIONS OF 1000 ROWS;

CALL {
    LOAD CSV WITH HEADERS FROM 'file:///nodes/search_tags.csv' AS row
    WITH row WHERE row.search_tag_id IS NOT NULL AND trim(row.search_tag_id) <> ''
    MERGE (n:SearchTag {search_tag_id: row.search_tag_id})
    SET n += row
} IN TRANSACTIONS OF 1000 ROWS;

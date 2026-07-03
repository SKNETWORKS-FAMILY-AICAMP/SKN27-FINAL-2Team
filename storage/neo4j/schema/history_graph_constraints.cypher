CREATE CONSTRAINT term_id_unique IF NOT EXISTS
FOR (n:Term)
REQUIRE n.term_id IS UNIQUE;

CREATE CONSTRAINT event_id_unique IF NOT EXISTS
FOR (n:Event)
REQUIRE n.event_id IS UNIQUE;

CREATE CONSTRAINT person_id_unique IF NOT EXISTS
FOR (n:Person)
REQUIRE n.person_id IS UNIQUE;

CREATE CONSTRAINT canonical_category_id_unique IF NOT EXISTS
FOR (n:CanonicalCategory)
REQUIRE n.category_id IS UNIQUE;

CREATE CONSTRAINT source_event_category_id_unique IF NOT EXISTS
FOR (n:SourceEventCategory)
REQUIRE n.event_category_id IS UNIQUE;

CREATE CONSTRAINT period_id_unique IF NOT EXISTS
FOR (n:Period)
REQUIRE n.period_id IS UNIQUE;

CREATE CONSTRAINT source_url_id_unique IF NOT EXISTS
FOR (n:SourceUrl)
REQUIRE n.source_url_id IS UNIQUE;

CREATE CONSTRAINT event_group_id_unique IF NOT EXISTS
FOR (n:EventGroup)
REQUIRE n.event_group_id IS UNIQUE;

CREATE CONSTRAINT event_facet_id_unique IF NOT EXISTS
FOR (n:EventFacet)
REQUIRE n.event_facet_id IS UNIQUE;

CREATE CONSTRAINT country_id_unique IF NOT EXISTS
FOR (n:Country)
REQUIRE n.country_id IS UNIQUE;

CREATE CONSTRAINT region_id_unique IF NOT EXISTS
FOR (n:Region)
REQUIRE n.region_id IS UNIQUE;

CREATE CONSTRAINT economic_domain_id_unique IF NOT EXISTS
FOR (n:EconomicDomain)
REQUIRE n.economic_domain_id IS UNIQUE;

CREATE CONSTRAINT taxonomy_facet_id_unique IF NOT EXISTS
FOR (n:TaxonomyFacet)
REQUIRE n.taxonomy_facet_id IS UNIQUE;

CREATE CONSTRAINT search_tag_id_unique IF NOT EXISTS
FOR (n:SearchTag)
REQUIRE n.search_tag_id IS UNIQUE;

CREATE INDEX term_name_index IF NOT EXISTS
FOR (n:Term)
ON (n.name);

CREATE INDEX event_name_index IF NOT EXISTS
FOR (n:Event)
ON (n.name);

CREATE INDEX person_name_index IF NOT EXISTS
FOR (n:Person)
ON (n.name);

CREATE INDEX canonical_category_path_index IF NOT EXISTS
FOR (n:CanonicalCategory)
ON (n.category_path);

CREATE INDEX source_url_url_index IF NOT EXISTS
FOR (n:SourceUrl)
ON (n.url);

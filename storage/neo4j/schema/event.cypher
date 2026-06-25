MATCH (n)
WHERE n:Event OR n:Person OR n:SubjectCategory OR n:Period
DETACH DELETE n;

CREATE CONSTRAINT event_id_unique IF NOT EXISTS
FOR (e:Event)
REQUIRE e.event_id IS UNIQUE;

CREATE CONSTRAINT person_name_hanja_unique IF NOT EXISTS
FOR (p:Person)
REQUIRE (p.person_name, p.person_hanja) IS UNIQUE;

CREATE CONSTRAINT subject_category_name_unique IF NOT EXISTS
FOR (sc:SubjectCategory)
REQUIRE sc.name IS UNIQUE;

CREATE CONSTRAINT period_name_unique IF NOT EXISTS
FOR (p:Period)
REQUIRE p.name IS UNIQUE;

LOAD CSV WITH HEADERS FROM 'file:///event.csv' AS row
WITH
    trim(coalesce(row.event_id, '')) AS event_id,
    trim(coalesce(row.event_name, '')) AS raw_event_name,
    trim(coalesce(row.subject_category, '')) AS subject_category,
    trim(coalesce(row.period, '')) AS period,
    trim(coalesce(row.event_date, '')) AS event_date
WITH
    event_id,
    CASE
        WHEN raw_event_name CONTAINS '(' AND raw_event_name CONTAINS ')' THEN trim(split(raw_event_name, '(')[0])
        WHEN NOT (raw_event_name CONTAINS '(' AND raw_event_name CONTAINS ')') THEN raw_event_name
    END AS event_name,
    CASE
        WHEN raw_event_name CONTAINS '(' AND raw_event_name CONTAINS ')' THEN trim(split(split(raw_event_name, '(')[1], ')')[0])
        WHEN NOT (raw_event_name CONTAINS '(' AND raw_event_name CONTAINS ')') THEN ''
    END AS event_hanja,
    subject_category,
    period,
    event_date
WHERE event_id <> '' AND event_name <> ''
MERGE (e:Event {event_id: event_id})
SET e.event_name = event_name,
    e.event_date = event_date
CALL (e, event_hanja) {
    WITH e, event_hanja
    WHERE event_hanja <> ''
    SET e.event_hanja = event_hanja
}
CALL (e, subject_category) {
    WITH e, [category IN split(subject_category, ',') WHERE trim(category) <> '' | trim(category)] AS subject_categories
    UNWIND subject_categories AS subject_category_name
    MERGE (sc:SubjectCategory {name: subject_category_name})
    MERGE (e)-[:HAS_SUBJECT]->(sc)
}
CALL (e, period) {
    WITH e, period
    WHERE period <> ''
    OPTIONAL MATCH (period_candidate:TermTimes)
    WHERE trim(period_candidate.name) = period
        OR trim(period_candidate.name) = period + '시대'
        OR replace(trim(period_candidate.name), '시대', '') = period
    WITH
        e,
        period,
        period_candidate,
        CASE
            WHEN period_candidate IS NULL THEN 99
            WHEN trim(period_candidate.name) = period + '시대' THEN 0
            WHEN trim(period_candidate.name) = period THEN 1
            WHEN replace(trim(period_candidate.name), '시대', '') = period THEN 2
        END AS match_rank
    ORDER BY match_rank
    WITH e, period, collect(period_candidate)[0] AS matched_period
    CALL (e, matched_period) {
        WITH e, matched_period
        WHERE matched_period IS NOT NULL
        MERGE (e)-[:IN_PERIOD]->(matched_period)
    }
    CALL (e, period, matched_period) {
        WITH e, period, matched_period
        WHERE matched_period IS NULL
        MERGE (new_period:Period {name: period})
        MERGE (e)-[:IN_PERIOD]->(new_period)
    }
}
CALL (e, event_name, event_hanja) {
    WITH e, event_name, event_hanja
    MATCH (e)-[:IN_PERIOD]->(event_period:TermTimes)
    MATCH (event_term:TermName)
    WHERE event_term.term_name = event_name
        AND (
            trim(coalesce(event_term.term_times, '')) = event_period.name
            OR replace(trim(coalesce(event_term.term_times, '')), '시대', '') = replace(event_period.name, '시대', '')
        )
        AND (
            event_hanja = ''
            OR trim(coalesce(event_term.term_ch, '')) = ''
            OR trim(coalesce(event_term.term_ch, '')) = event_hanja
        )
    MERGE (e)-[r:MATCHED_TERM]->(event_term)
    SET r.match_source = 'event_name_period'
};

LOAD CSV WITH HEADERS FROM 'file:///event_relation.csv' AS row
WITH
    trim(coalesce(row.event_id, '')) AS event_id,
    trim(coalesce(row.event_name, '')) AS raw_event_name,
    trim(coalesce(row.person_name, '')) AS raw_person_name
WITH
    event_id,
    CASE
        WHEN raw_event_name CONTAINS '(' AND raw_event_name CONTAINS ')' THEN trim(split(raw_event_name, '(')[0])
        WHEN NOT (raw_event_name CONTAINS '(' AND raw_event_name CONTAINS ')') THEN raw_event_name
    END AS event_name,
    CASE
        WHEN raw_event_name CONTAINS '(' AND raw_event_name CONTAINS ')' THEN trim(split(split(raw_event_name, '(')[1], ')')[0])
        WHEN NOT (raw_event_name CONTAINS '(' AND raw_event_name CONTAINS ')') THEN ''
    END AS event_hanja,
    CASE
        WHEN raw_person_name CONTAINS '(' AND raw_person_name CONTAINS ')' THEN trim(split(raw_person_name, '(')[0])
        WHEN NOT (raw_person_name CONTAINS '(' AND raw_person_name CONTAINS ')') THEN raw_person_name
    END AS person_name,
    CASE
        WHEN raw_person_name CONTAINS '(' AND raw_person_name CONTAINS ')' THEN trim(split(split(raw_person_name, '(')[1], ')')[0])
        WHEN NOT (raw_person_name CONTAINS '(' AND raw_person_name CONTAINS ')') THEN ''
    END AS person_hanja
WHERE event_id <> '' AND person_name <> ''
MERGE (e:Event {event_id: event_id})
ON CREATE SET e.event_name = event_name
CALL (e, event_hanja) {
    WITH e, event_hanja
    WHERE event_hanja <> '' AND e.event_hanja IS NULL
    SET e.event_hanja = event_hanja
}
MERGE (p:Person {
    person_name: person_name,
    person_hanja: person_hanja
})
MERGE (p)-[:APPEARS_IN]->(e)
CALL (p, e) {
    WITH p, e
    MATCH (e)-[:IN_PERIOD]->(event_period)
    MERGE (p)-[:IN_PERIOD]->(event_period)
}
CALL (p, e, person_name, person_hanja) {
    WITH p, e, person_name, person_hanja
    MATCH (e)-[:IN_PERIOD]->(event_period:TermTimes)
    MATCH (person_term:TermName)
    WHERE person_term.term_name = person_name
        AND (
            trim(coalesce(person_term.term_times, '')) = event_period.name
            OR replace(trim(coalesce(person_term.term_times, '')), '시대', '') = replace(event_period.name, '시대', '')
        )
        AND (
            person_hanja = ''
            OR trim(coalesce(person_term.term_ch, '')) = ''
            OR trim(coalesce(person_term.term_ch, '')) = person_hanja
        )
    MERGE (p)-[r:MATCHED_TERM]->(person_term)
    SET r.match_source = 'person_name_period'
};

MATCH (n)
WHERE n:TermName OR n:TermTimes OR n:TermLink
DETACH DELETE n;

CREATE CONSTRAINT term_name_unique IF NOT EXISTS
FOR (t:TermName)
REQUIRE (t.term_name, t.term_ch, t.term_times) IS UNIQUE;

CREATE CONSTRAINT term_times_unique IF NOT EXISTS
FOR (tt:TermTimes)
REQUIRE tt.name IS UNIQUE;

CREATE CONSTRAINT term_lk_unique IF NOT EXISTS
FOR (lk:TermLink)
REQUIRE lk.value IS UNIQUE;

LOAD CSV WITH HEADERS FROM 'file:///history_terms.csv' AS row
WITH
    trim(coalesce(row.term_name, '')) AS term_name,
    trim(coalesce(row.term_ch, '')) AS term_ch,
    trim(coalesce(row.term_year, '')) AS term_year,
    trim(coalesce(row.term_times, '')) AS term_times,
    trim(coalesce(row.term_lk, '')) AS raw_term_lk,
    trim(coalesce(row.term_desc, '')) AS term_desc
WITH
    term_name,
    term_ch,
    term_year,
    term_times,
    CASE
        WHEN raw_term_lk = '_NULL_' THEN ''
        WHEN raw_term_lk <> '_NULL_' THEN raw_term_lk
    END AS term_lk,
    term_desc
WHERE term_name <> '' AND term_times <> '현대'
MERGE (t:TermName {
    term_name: term_name,
    term_ch: term_ch,
    term_times: term_times
})
SET t.term_name = term_name,
    t.term_ch = term_ch,
    t.term_year = term_year,
    t.term_times = term_times,
    t.term_desc = term_desc
WITH t, term_times, term_lk
CALL (t, term_times) {
    WITH t, term_times
    WHERE term_times <> ''
    MERGE (tt:TermTimes {name: term_times})
    MERGE (t)-[:HAS_PERIOD]->(tt)
}
CALL (t, term_lk) {
    WITH t, term_lk
    WHERE term_lk <> ''
    MERGE (lk:TermLink {value: term_lk})
    MERGE (t)-[:HAS_CATEGORY]->(lk)
};

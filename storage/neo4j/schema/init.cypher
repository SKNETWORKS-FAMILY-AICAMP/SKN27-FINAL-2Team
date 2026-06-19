CREATE CONSTRAINT term_name_unique IF NOT EXISTS
FOR (t:TermName)
REQUIRE t.name IS UNIQUE;

CREATE CONSTRAINT term_times_unique IF NOT EXISTS
FOR (tt:TermTimes)
REQUIRE tt.name IS UNIQUE;

CREATE CONSTRAINT term_lk_unique IF NOT EXISTS
FOR (lk:TermLink)
REQUIRE lk.value IS UNIQUE;

LOAD CSV WITH HEADERS FROM 'file:///역사용어.csv' AS row
WITH row,
    trim(coalesce(row,term_name, '')) AS term_name
    trim(coalesce(row.term_ch, '')) AS term_ch,
    trim(coalesce(row.term_year, '')) AS term_year,
    trim(coalesce(row.term_times, '')) AS term_times,
    trim(coalesce(row.term_lk, '')) AS term_lk,
    trim(coalesce(row.term_desc, '')) AS term_desc
WHERE term_name <> ''
WITH row,
    term_name,
    term_ch,
    term_year,
    term_times,
    term_name + '|' + term_ch + '|' + term_year+'|' + term_times AS term_key

MERGE (t:TermName {term_key : term_key})

MERGE ()

SET t.term_name = term_name,
    t.term_ch = term_ch,
    t.term_year = term_year,
    t.term_times = term_times,
    t.term_lk = trim(coalesce(row.term_lk, '')),
    t.term_desc = trim(coalesce(row.term_desc, ''));
CREATE SCHEMA IF NOT EXISTS qgen;

CREATE TABLE IF NOT EXISTS qgen.basis_packs (
    pack_id              TEXT PRIMARY KEY,
    topic_set_id         TEXT NOT NULL,
    source_dataset       TEXT NOT NULL,
    source_question_id   TEXT NOT NULL,
    target_article_id    TEXT NOT NULL REFERENCES rag.encykorea_articles(article_id),
    target_label         TEXT NOT NULL,
    topic_type           TEXT NOT NULL,
    question_task        TEXT NOT NULL,
    stem_pattern         TEXT NOT NULL,
    relation_axis_id     TEXT NOT NULL,
    material_type        TEXT NOT NULL,
    major_type           TEXT NOT NULL,
    minor_type           TEXT NOT NULL,
    difficulty_label     TEXT NOT NULL,
    difficulty_source    TEXT NOT NULL DEFAULT 'v41_original',
    classifier_version   TEXT NOT NULL,
    topic_resolution_method TEXT NOT NULL,
    material_clue_basis  TEXT,
    material_evidence_chunks JSONB NOT NULL DEFAULT '[]'::jsonb,
    semantic_status       TEXT NOT NULL DEFAULT 'pending'
        CHECK (semantic_status IN ('pending', 'pass', 'fail')),
    semantic_validation   JSONB NOT NULL DEFAULT '{}'::jsonb,
    status               TEXT NOT NULL DEFAULT 'pending_basis'
        CHECK (status IN ('pending_basis', 'rag_ready', 'needs_review')),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_dataset, source_question_id, question_task, stem_pattern),
    UNIQUE (topic_set_id, question_task, stem_pattern, relation_axis_id)
);

ALTER TABLE qgen.basis_packs
    ADD COLUMN IF NOT EXISTS difficulty_source TEXT NOT NULL DEFAULT 'v41_original';

ALTER TABLE qgen.basis_packs
    ADD COLUMN IF NOT EXISTS semantic_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (semantic_status IN ('pending', 'pass', 'fail'));

ALTER TABLE qgen.basis_packs
    ADD COLUMN IF NOT EXISTS semantic_validation JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE qgen.basis_packs
    ADD COLUMN IF NOT EXISTS material_clue_basis TEXT;

ALTER TABLE qgen.basis_packs
    ADD COLUMN IF NOT EXISTS material_evidence_chunks JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS qgen.basis_items (
    basis_item_id        BIGSERIAL PRIMARY KEY,
    pack_id              TEXT NOT NULL REFERENCES qgen.basis_packs(pack_id) ON DELETE CASCADE,
    role                 TEXT NOT NULL CHECK (role IN ('answer', 'distractor')),
    slot_no              SMALLINT NOT NULL CHECK (slot_no BETWEEN 0 AND 4),
    article_id           TEXT NOT NULL REFERENCES rag.encykorea_articles(article_id),
    truth_owner_label    TEXT NOT NULL,
    fact_search_hint     TEXT NOT NULL DEFAULT '',
    fact_basis           TEXT,
    evidence_chunks      JSONB NOT NULL DEFAULT '[]'::jsonb,
    basis_source         TEXT NOT NULL DEFAULT 'encykorea_rag',
    semantic_status      TEXT NOT NULL DEFAULT 'pending'
        CHECK (semantic_status IN ('pending', 'pass', 'fail')),
    semantic_validation  JSONB NOT NULL DEFAULT '{}'::jsonb,
    status               TEXT NOT NULL DEFAULT 'pending_basis'
        CHECK (status IN ('pending_basis', 'rag_ready', 'needs_review')),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pack_id, slot_no),
    CHECK (
        (role = 'answer' AND slot_no = 0)
        OR (role = 'distractor' AND slot_no BETWEEN 1 AND 4)
    )
);

ALTER TABLE qgen.basis_items
    ADD COLUMN IF NOT EXISTS semantic_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (semantic_status IN ('pending', 'pass', 'fail'));

ALTER TABLE qgen.basis_items
    ADD COLUMN IF NOT EXISTS semantic_validation JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE qgen.basis_items
    ADD COLUMN IF NOT EXISTS fact_search_hint TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS basis_packs_generation_idx
    ON qgen.basis_packs(question_task, stem_pattern, difficulty_label, status);

CREATE INDEX IF NOT EXISTS basis_items_owner_idx
    ON qgen.basis_items(article_id, status);

CREATE INDEX IF NOT EXISTS basis_packs_semantic_idx
    ON qgen.basis_packs(semantic_status, status);

CREATE INDEX IF NOT EXISTS basis_packs_ready_idx
    ON qgen.basis_packs(question_task, stem_pattern, difficulty_label)
    WHERE status = 'rag_ready' AND semantic_status = 'pass';

-- Role-neutral, reusable facts derived only from semantically approved basis items.
CREATE TABLE IF NOT EXISTS qgen.choice_facts (
    choice_fact_id       TEXT PRIMARY KEY,
    article_id           TEXT NOT NULL REFERENCES rag.encykorea_articles(article_id),
    truth_owner_label    TEXT NOT NULL,
    owner_type           TEXT NOT NULL,
    relation_axis_id     TEXT NOT NULL,
    fact_basis           TEXT NOT NULL,
    fact_fingerprint     TEXT NOT NULL,
    evidence_chunks      JSONB NOT NULL,
    basis_source         TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (article_id, relation_axis_id, fact_fingerprint),
    CHECK (NULLIF(BTRIM(fact_basis), '') IS NOT NULL),
    CHECK (JSONB_TYPEOF(evidence_chunks) = 'array' AND JSONB_ARRAY_LENGTH(evidence_chunks) > 0)
);

CREATE TABLE IF NOT EXISTS qgen.choice_fact_sources (
    choice_fact_id       TEXT NOT NULL REFERENCES qgen.choice_facts(choice_fact_id) ON DELETE CASCADE,
    basis_item_id        BIGINT NOT NULL UNIQUE REFERENCES qgen.basis_items(basis_item_id) ON DELETE CASCADE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (choice_fact_id, basis_item_id)
);

CREATE INDEX IF NOT EXISTS choice_facts_lookup_idx
    ON qgen.choice_facts(relation_axis_id, owner_type, article_id);

CREATE INDEX IF NOT EXISTS choice_fact_sources_fact_idx
    ON qgen.choice_fact_sources(choice_fact_id);

CREATE OR REPLACE VIEW qgen.choice_fact_catalog AS
SELECT
    f.choice_fact_id,
    f.article_id,
    f.truth_owner_label,
    f.owner_type,
    f.relation_axis_id,
    f.fact_basis,
    f.evidence_chunks,
    f.basis_source,
    COUNT(*) AS source_count,
    ARRAY_AGG(DISTINCT i.role ORDER BY i.role) AS source_roles,
    ARRAY_AGG(DISTINCT p.difficulty_label ORDER BY p.difficulty_label) AS difficulty_labels,
    ARRAY_AGG(DISTINCT p.question_task ORDER BY p.question_task) AS question_tasks,
    ARRAY_AGG(DISTINCT p.stem_pattern ORDER BY p.stem_pattern) AS stem_patterns,
    ARRAY_AGG(DISTINCT p.source_question_id ORDER BY p.source_question_id) AS source_question_ids,
    EXISTS (
        SELECT 1
        FROM JSONB_ARRAY_ELEMENTS(f.evidence_chunks) evidence
        WHERE evidence->>'article_id' = f.article_id
    ) AS has_direct_owner_evidence
FROM qgen.choice_facts f
JOIN qgen.choice_fact_sources s USING (choice_fact_id)
JOIN qgen.basis_items i USING (basis_item_id)
JOIN qgen.basis_packs p USING (pack_id)
GROUP BY f.choice_fact_id;

CREATE OR REPLACE VIEW qgen.choice_fact_cooccurrence AS
SELECT
    p.pack_id AS source_pack_id,
    p.source_question_id,
    p.relation_axis_id,
    p.question_task,
    p.stem_pattern,
    p.difficulty_label,
    anchor_source.choice_fact_id AS anchor_choice_fact_id,
    anchor_item.article_id AS anchor_article_id,
    anchor_item.role AS anchor_source_role,
    candidate_source.choice_fact_id AS candidate_choice_fact_id,
    candidate_item.article_id AS candidate_article_id,
    candidate_item.role AS candidate_source_role
FROM qgen.choice_fact_sources anchor_source
JOIN qgen.basis_items anchor_item USING (basis_item_id)
JOIN qgen.basis_packs p USING (pack_id)
JOIN qgen.basis_items candidate_item
  ON candidate_item.pack_id = anchor_item.pack_id
 AND candidate_item.basis_item_id <> anchor_item.basis_item_id
JOIN qgen.choice_fact_sources candidate_source
  ON candidate_source.basis_item_id = candidate_item.basis_item_id
WHERE p.status = 'rag_ready' AND p.semantic_status = 'pass'
  AND anchor_item.status = 'rag_ready' AND anchor_item.semantic_status = 'pass'
  AND candidate_item.status = 'rag_ready' AND candidate_item.semantic_status = 'pass';

-- A fact can be used in a frame only after its role has been reviewed against
-- that frame's material clue and question contract. Full four-choice
-- combinations are intentionally not materialized; they are assembled from
-- these approved edges at runtime.
CREATE TABLE IF NOT EXISTS qgen.frame_choice_compatibility (
    frame_pack_id        TEXT NOT NULL REFERENCES qgen.basis_packs(pack_id) ON DELETE CASCADE,
    choice_fact_id       TEXT NOT NULL REFERENCES qgen.choice_facts(choice_fact_id) ON DELETE CASCADE,
    role                 TEXT NOT NULL CHECK (role IN ('answer', 'distractor')),
    source_kind          TEXT NOT NULL CHECK (source_kind IN ('original_pack', 'expanded_pool')),
    status               TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'pass', 'fail')),
    reason               TEXT NOT NULL DEFAULT '',
    reviewed_by          TEXT NOT NULL DEFAULT '',
    reviewed_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (frame_pack_id, choice_fact_id),
    CHECK (status = 'pending' OR NULLIF(BTRIM(reviewed_by), '') IS NOT NULL),
    CHECK (status <> 'fail' OR NULLIF(BTRIM(reason), '') IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS frame_choice_compatibility_ready_idx
    ON qgen.frame_choice_compatibility(frame_pack_id, role, choice_fact_id)
    WHERE status = 'pass';

-- Seed existing source-pack relationships as pending. Review, rather than the
-- old semantic_status flag, decides whether they can be recombined.
INSERT INTO qgen.frame_choice_compatibility (
    frame_pack_id,
    choice_fact_id,
    role,
    source_kind
)
SELECT
    i.pack_id,
    s.choice_fact_id,
    i.role,
    'original_pack'
FROM qgen.basis_items i
JOIN qgen.choice_fact_sources s USING (basis_item_id)
JOIN qgen.basis_packs p USING (pack_id)
WHERE p.status = 'rag_ready' AND p.semantic_status = 'pass'
  AND i.status = 'rag_ready' AND i.semantic_status = 'pass'
ON CONFLICT (frame_pack_id, choice_fact_id) DO NOTHING;

CREATE OR REPLACE VIEW qgen.composable_frame_choices AS
SELECT
    c.frame_pack_id,
    c.role,
    c.source_kind,
    f.choice_fact_id,
    f.article_id,
    f.truth_owner_label,
    f.owner_type,
    f.relation_axis_id,
    f.fact_basis,
    f.fact_fingerprint,
    f.evidence_chunks
FROM qgen.frame_choice_compatibility c
JOIN qgen.choice_facts f USING (choice_fact_id)
WHERE c.status = 'pass';

DROP VIEW IF EXISTS qgen.composable_frames;
DROP VIEW IF EXISTS qgen.frame_combination_capacity;
DROP VIEW IF EXISTS qgen.frame_choice_integrity;

CREATE VIEW qgen.frame_choice_integrity AS
SELECT
    p.pack_id AS frame_pack_id,
    COUNT(*) FILTER (WHERE c.role = 'answer') AS answer_count,
    COUNT(*) FILTER (WHERE c.role = 'distractor') AS distractor_count,
    COUNT(DISTINCT c.choice_fact_id) FILTER (WHERE c.role = 'distractor') AS distinct_distractor_count,
    COUNT(DISTINCT f.article_id) FILTER (WHERE c.role = 'distractor') AS distinct_distractor_owner_count,
    COUNT(DISTINCT f.fact_fingerprint) FILTER (WHERE c.role = 'distractor') AS distinct_distractor_fact_count,
    COUNT(*) FILTER (WHERE JSONB_ARRAY_LENGTH(f.evidence_chunks) = 0) AS missing_evidence_count
FROM qgen.basis_packs p
JOIN qgen.frame_choice_compatibility c
  ON c.frame_pack_id = p.pack_id AND c.status = 'pass'
JOIN qgen.choice_facts f
  ON f.choice_fact_id = c.choice_fact_id
WHERE p.status = 'rag_ready' AND p.semantic_status = 'pass'
GROUP BY p.pack_id;

-- Count four-choice sets conservatively by distinct eligible owner. A frame
-- may keep multiple reviewed facts for one owner, but those do not inflate
-- its production capacity.
CREATE VIEW qgen.frame_combination_capacity AS
WITH answers AS (
    SELECT frame_pack_id, MIN(article_id) AS article_id
    FROM qgen.composable_frame_choices
    WHERE role = 'answer'
    GROUP BY frame_pack_id
    HAVING COUNT(*) = 1
), counts AS (
    SELECT
        d.frame_pack_id,
        COUNT(DISTINCT d.article_id) AS eligible_owner_count
    FROM qgen.composable_frame_choices d
    JOIN qgen.basis_packs p
      ON p.pack_id = d.frame_pack_id
    JOIN answers answer
      ON answer.frame_pack_id = d.frame_pack_id
    WHERE d.role = 'distractor'
      AND d.article_id NOT IN (p.target_article_id, answer.article_id)
    GROUP BY d.frame_pack_id
)
SELECT
    frame_pack_id,
    eligible_owner_count,
    eligible_owner_count * (eligible_owner_count - 1) *
        (eligible_owner_count - 2) * (eligible_owner_count - 3) / 24 AS combination_count
FROM counts
WHERE eligible_owner_count >= 4;

CREATE VIEW qgen.composable_frames AS
SELECT
    integrity.frame_pack_id,
    integrity.answer_count,
    integrity.distractor_count,
    integrity.distinct_distractor_count,
    integrity.distinct_distractor_owner_count,
    integrity.distinct_distractor_fact_count,
    capacity.eligible_owner_count,
    capacity.combination_count
FROM qgen.frame_choice_integrity integrity
JOIN qgen.frame_combination_capacity capacity USING (frame_pack_id)
WHERE integrity.answer_count = 1
  AND integrity.missing_evidence_count = 0
  AND capacity.combination_count > 0;

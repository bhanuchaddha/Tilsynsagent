-- Grounding: decisions the agent makes by reading the source plan document,
-- with no person in the path.
--
-- Two changes, and the first one reverses a decision migration 002 made.
--
-- 002 tightened diffs.outcome to ('file','escalate'), stating that 'ignore'
-- "is always a claim about a document the rule layer never opens, so it is
-- not reachable deterministically". That reasoning was correct and is now
-- spent: the ground node *does* open the document. A grounded ignore is a
-- positive claim - "clause 6.3 governs this field and it says the reader
-- sees nothing new" - backed by a quotable clause id, not an absence of
-- action. That is a different thing from the rule-layer ignore 002 removed,
-- and it is auditable in exactly the way 002 required and the rule layer
-- could not provide.

ALTER TABLE diffs DROP CONSTRAINT diffs_outcome_check;
ALTER TABLE diffs ADD CONSTRAINT diffs_outcome_check
    CHECK (outcome IN ('file', 'escalate', 'ignore'));

-- What the document said, for every decision grounded in it.
--
-- This table is the evidence trail for the only decisions in this system
-- nobody reviews. Without it a grounded filing is an assertion; with it,
-- clause_id and clause_quote can be checked against the document by code
-- (obs/grounded.py) months later, which is what CLAUDE.md's one rule
-- requires of an autonomous decision.
--
-- UNIQUE on diff_id for the same reason escalations is: interrupt() re-runs
-- a node from the top on resume, so a write can be attempted twice for one
-- diff.
CREATE TABLE groundings (
    id                    BIGSERIAL PRIMARY KEY,
    diff_id               BIGINT NOT NULL REFERENCES diffs (id) UNIQUE,
    -- The clause the decision rests on, and its text copied out of the
    -- document. Both are what a reader checks; neither is optional for a
    -- decided grounding (an abstention never reaches this table - it
    -- escalates instead).
    clause_id             TEXT NOT NULL,
    clause_quote          TEXT NOT NULL,
    reasoning             TEXT NOT NULL,
    outcome               TEXT NOT NULL CHECK (outcome IN ('file', 'ignore')),
    -- Operating evidence, not decision evidence: how big the document was
    -- and which clauses retrieval offered the model. Kept because "the model
    -- picked 6.3 out of these four" is a different and more useful fact than
    -- "the model said 6.3", and because retrieval quality is the thing most
    -- likely to drift silently as templates change.
    document_page_count   INTEGER,
    retrieved_clause_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    grounded_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_groundings_diff_id ON groundings (diff_id);

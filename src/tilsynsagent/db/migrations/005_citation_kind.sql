-- A grounded decision may rest on either a quoted document clause or a named
-- register field ("status changed from F to V"), both checkable against the
-- record by code. citation_kind says which. Exactly one kind per decision,
-- and the CHECK constraint below enforces that the matching columns are
-- actually populated.
ALTER TABLE groundings
    ADD COLUMN citation_kind TEXT NOT NULL DEFAULT 'clause',
    ADD COLUMN field_name    TEXT NOT NULL DEFAULT '',
    ADD COLUMN field_before  TEXT NOT NULL DEFAULT '',
    ADD COLUMN field_after   TEXT NOT NULL DEFAULT '';

-- clause_id and clause_quote stay NOT NULL (empty string, never null - the
-- same convention llm/ground.py uses) with the CHECK above carrying the
-- real requirement.
ALTER TABLE groundings
    ADD CONSTRAINT groundings_citation_is_complete CHECK (
        (citation_kind = 'clause' AND clause_id <> '' AND clause_quote <> '')
        OR
        (citation_kind = 'field' AND field_name <> ''
         AND (field_before <> '' OR field_after <> ''))
    );

-- Clause retrieval is gone; the model now gets the whole document.
ALTER TABLE groundings DROP COLUMN retrieved_clause_ids;

-- How much document text the model actually saw.
ALTER TABLE groundings ADD COLUMN document_chars INTEGER;

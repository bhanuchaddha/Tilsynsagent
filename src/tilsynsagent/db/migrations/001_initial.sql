-- Initial schema: sub-areas and their versions, the normalised diffs between
-- them, filings, escalations and their resolutions, and the run watermark.
--
-- The register arrives relational (komnr, lokplan_id, delnr, versionsnr are
-- foreign keys in all but name) and the domain is relational too: a sub-area
-- has versions, versions produce diffs, diffs produce filings or escalations,
-- and resolved escalations become golden-dataset cases. This schema follows
-- that shape rather than inventing a different one.

-- A sub-area (delområde) is identified by (lokplan_id, delnr), independent of
-- version. Its row is created the first time it is seen and never updated -
-- everything that changes over time lives in sub_area_versions.
CREATE TABLE sub_areas (
    id            BIGSERIAL PRIMARY KEY,
    lokplan_id    BIGINT NOT NULL,
    delnr         TEXT NOT NULL,
    komnr         INTEGER NOT NULL,
    kommunenavn   TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (lokplan_id, delnr)
);

-- One version of a sub-area as fetched from the WFS feed. Append-only: a new
-- fetch that differs from the last version seen for this sub-area inserts a
-- new row, never updates an old one, so the full history stays reconstructable.
CREATE TABLE sub_area_versions (
    id                 BIGSERIAL PRIMARY KEY,
    sub_area_id        BIGINT NOT NULL REFERENCES sub_areas (id),
    feature_id         TEXT NOT NULL UNIQUE,
    versionsnr         INTEGER NOT NULL,
    status             TEXT,
    datoopdt           TIMESTAMPTZ NOT NULL,
    maxbygnhjd         DOUBLE PRECISION,
    maxetager          DOUBLE PRECISION,
    bebygpct           DOUBLE PRECISION,
    zonestatus         TEXT,
    anvendelsegenerel  TEXT,
    doklink            TEXT,
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sub_area_versions_sub_area_id ON sub_area_versions (sub_area_id, datoopdt);

-- A transition between two versions of the same sub-area where at least one
-- watched field differs (rules.engine.WATCHED_FIELDS). changed_fields is
-- stored in the same shape as the golden dataset's changed_fields, so a diff
-- row can be replayed through rules/engine.py unchanged.
CREATE TABLE diffs (
    id                 BIGSERIAL PRIMARY KEY,
    sub_area_id        BIGINT NOT NULL REFERENCES sub_areas (id),
    before_version_id  BIGINT REFERENCES sub_area_versions (id),
    after_version_id   BIGINT NOT NULL REFERENCES sub_area_versions (id) UNIQUE,
    changed_fields     JSONB NOT NULL,
    outcome            TEXT NOT NULL CHECK (outcome IN ('file', 'escalate', 'ignore')),
    rule               TEXT,
    rule_set           TEXT NOT NULL,
    detected_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_diffs_sub_area_id ON diffs (sub_area_id);
CREATE INDEX idx_diffs_outcome ON diffs (outcome);

-- A filed decision: outcome = 'file', with the model-written summary for the
-- record. One-to-one with the diff that produced it.
CREATE TABLE filings (
    id          BIGSERIAL PRIMARY KEY,
    diff_id     BIGINT NOT NULL REFERENCES diffs (id) UNIQUE,
    summary     TEXT NOT NULL,
    citation    TEXT NOT NULL,
    filed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- An escalation: outcome = 'escalate', whether decided by a rule (R4/R5/R6/R7)
-- or by the LLM assessment step on a case the rule engine did not cover.
-- thread_id is the LangGraph thread id whose checkpoint is paused on
-- interrupt() for this escalation, so a resolution knows which run to resume.
CREATE TABLE escalations (
    id                       BIGSERIAL PRIMARY KEY,
    diff_id                  BIGINT NOT NULL REFERENCES diffs (id) UNIQUE,
    thread_id                TEXT NOT NULL,
    what_is_unclear          TEXT NOT NULL,
    what_a_person_must_decide TEXT,
    citation                 TEXT NOT NULL,
    escalated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_escalations_thread_id ON escalations (thread_id);

-- How a person resolved an escalation. Carries the fields a golden-dataset
-- case needs (evals/golden/README.md: label, reason, rule) so a resolved
-- escalation becomes an escalation-derived case without re-keying it.
CREATE TABLE escalation_resolutions (
    id            BIGSERIAL PRIMARY KEY,
    escalation_id BIGINT NOT NULL REFERENCES escalations (id) UNIQUE,
    label         TEXT NOT NULL CHECK (label IN ('file', 'escalate', 'ignore')),
    reason        TEXT NOT NULL,
    rule          TEXT,
    resolved_by   TEXT NOT NULL,
    resolved_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A single row holding the high-water mark for the WFS change feed. Mirrors
-- sources.plandata.WatermarkStore's on-disk shape, in the database so a
-- scheduled run picks up where the last one left off regardless of which
-- machine ran it.
CREATE TABLE watermark (
    id                 INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_datoopdt      TIMESTAMPTZ NOT NULL,
    seen_at_watermark  JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

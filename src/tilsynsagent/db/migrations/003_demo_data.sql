-- Marks rows created by the Phase 3 demo seed (demo/seed.py) so a reset can
-- wipe test data without touching live run history.
--
-- Tagged on sub_areas only, not every table: every other table in the FK
-- chain (sub_area_versions, diffs, filings, escalations,
-- escalation_resolutions) hangs off sub_area_id, so one flag on the root
-- identifies a whole demo case's rows via a join. See demo/reset.py.
ALTER TABLE sub_areas ADD COLUMN is_test_data BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX idx_sub_areas_is_test_data ON sub_areas (is_test_data) WHERE is_test_data;

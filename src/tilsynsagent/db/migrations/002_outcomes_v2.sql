-- The rule engine never reaches outcome 'ignore': ignore is always a claim
-- about a document the rule layer never opens, so it is not reachable
-- deterministically.
--
-- escalation_resolutions.label keeps 'ignore' untouched - a human resolving
-- an escalation, or the grounding path, may legitimately conclude "nothing
-- to notify."

ALTER TABLE diffs DROP CONSTRAINT diffs_outcome_check;
ALTER TABLE diffs ADD CONSTRAINT diffs_outcome_check CHECK (outcome IN ('file', 'escalate'));

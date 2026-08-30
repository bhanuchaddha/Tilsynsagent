-- Rule set v2 drops 'ignore' as a rule-engine outcome (docs/rules.md,
-- "The two outcomes"): ignore is always a claim about a document the rule
-- layer never opens, so it is not reachable deterministically. Safe to
-- tighten this CHECK: the graph's skip node never wrote a diffs row (see
-- graph.py's _skip_node), so no existing data violates it.
--
-- escalation_resolutions.label keeps 'ignore' untouched - a human resolving
-- an escalation may legitimately conclude "nothing to notify," which is the
-- Phase 6 grounded-ignore path and the only place 'ignore' is a valid
-- decision.

ALTER TABLE diffs DROP CONSTRAINT diffs_outcome_check;
ALTER TABLE diffs ADD CONSTRAINT diffs_outcome_check CHECK (outcome IN ('file', 'escalate'));

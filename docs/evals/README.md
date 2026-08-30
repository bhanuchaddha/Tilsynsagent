# Evals: how correctness is established

This directory holds committed, dated baselines produced by
`evals/run_baseline.py`. Each `baseline-<date>.md` is a snapshot: git SHA,
rule set version, resolved model, dataset composition, every scorer with its
denominator and its failing case ids, cost, and a stability check - written
once and never edited after, the same discipline as a lab notebook entry.

## Why dated snapshots, not a live dashboard

Langfuse Cloud's free tier retains data 30 days. A number that lives only in
Langfuse expires; committing the same number (and the failing case ids that
justify it) into `docs/` here means it survives past that window and can be
compared against a later phase's run without depending on the vendor still
having the original data.

## What a baseline measures, and what it doesn't

The rule engine (`rules/engine.py`) already scores 100% against the 34
hand-labelled golden cases - see `tests/test_rules_engine.py`. That number
cannot move and a CI gate on it could never fire, so it is reported in every
baseline as a **precondition**, not the achievement being measured.

What each baseline actually measures is **LLM output fidelity**: whether
`assess()`/`summarise()` produce structurally valid, citation-faithful,
non-fabricating text for the cases that reach them. See `evals/README.md`
for the full scorer list and `evals/golden/README.md` for how the synthetic
NOT_COVERED cases fill a real gap in the 34 real ones.

Every baseline states explicitly what it does not measure - retrieval
quality, escalation usefulness, prompt-to-prompt regression over time. Those
require infrastructure (Phase 3's review queue, Phase 5's CI gate) that does
not exist yet at the time each baseline was recorded.

## Baselines

| Date | File | Notes |
|---|---|---|
| 2026-08-22 | [`baseline-2026-08-22.md`](baseline-2026-08-22.md) | First baseline. Rule set `zealand-local-plans-v1`. |
| 2026-08-29 | [`baseline-2026-08-29.md`](baseline-2026-08-29.md) | Rule set `zealand-local-plans-v2` (reader-centric two-outcome model). Dataset relabelled and re-run against the same code path; v1 snapshot above kept byte-for-byte unchanged for comparison. |

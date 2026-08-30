# Evals

Phase 2's eval suite: measures LLM output fidelity, the surface Phase 1 left
unmeasured. Not the rule engine - `tests/test_rules_engine.py` already
replays all 34 golden cases through `apply_rules` at 100%, and a CI gate on a
score that cannot move is not a gate. See `docs/evals/` for the committed
baseline this suite produced.

## Why this exists

Routing the 34 real golden cases through the graph:

| Route | Cases | LLM step exercised |
|---|---|---|
| file | 22 | `summarise` |
| escalate | 12 | none - rule-decided, skips `assess` |
| not_covered | 0 | `assess` - zero natural coverage |

There is no `ignore` route: the rule engine only ever files or escalates -
see `docs/rules.md`'s "The two outcomes" on why `ignore` is not reachable
from the register alone.

The 34 give `summarise` real coverage but leave `assess` completely
unexercised. `evals/golden/not_covered_cases.jsonl` is a small **synthetic**
set (5 cases as of this writing, no-visible-change revisions rather than the
zone-status-alone transitions v1 used - zone status became decidable under
v2's R2) built to give `assess` something to run against, so the baseline
isn't silently blind to half the LLM surface.

**This is temporary.** Once the running system escalates real cases through
the review queue (Phase 3), those become `origin: "escalation-derived"`
cases in `evals/golden/cases.jsonl` and the synthetic set should shrink or be
retired - a real `not_covered` case earned by the system beats a
hand-constructed one.

## Modules

| File | Role |
|---|---|
| `cases.py` | Loads `evals/golden/cases.jsonl` + `not_covered_cases.jsonl`, computes each case's expected route, and the Langfuse dataset item id (`case["id"]` verbatim). |
| `scorers.py` | Pure-code checks against LLM output - zero LLM-as-judge. See below. |
| `tasks.py` | Runs one case through `assess()`/`summarise()` directly (not through `graph.py`, which writes to Postgres as a side effect). |
| `sync_dataset.py` | Upserts every case into a Langfuse dataset (`tilsynsagent-golden`). Idempotent: re-running does not duplicate items. |
| `run_baseline.py` | Runs the dataset through the tasks, scores every result, prints the aggregate. `--no-langfuse` is the vendor-independent path; without it, cases route through Langfuse's own `run_experiment`, visible in the UI. |

## Scorers

All pure code, zero LLM-as-judge. That is not a stylistic choice: the
project's one rule (see root `CLAUDE.md`) requires every autonomous decision
traceable to what justified it, and a judge model's verdict is exactly an
untraceable decision - "the judge said so" is not a citation.

| Scorer | What it proves |
|---|---|
| `citation_fidelity` | The doklink appears verbatim in the output. The one rule, tested mechanically - the most important scorer here. |
| `no_invented_numbers` | Every numeral in the output traces to a before/after value in the record (or to legitimate context - a plan id, sub-area code, or the doklink URL). Handles Danish decimal commas. |
| `states_both_values` | Both the before and after value of at least one changed field appear in the output - the counterweight to `no_invented_numbers`, which a numberless summary would otherwise pass hollowly. |
| `did_not_read_the_document` | The PDF boundary in `docs/rules.md` ("the agent works from the register and cites the document, it does not interpret it") is held, not merely instructed. A keyword heuristic - a floor, not a proof. This boundary is Phase-2-scoped, not permanent: Phase 6 grounds decisions in the actual clause text of the source PDF, which will invert this scorer's premise once it lands. |
| `valid_structured_output` | Strict JSON schema held at scale. Phase 1 verified this with one call; every eval run is another sample of the same claim. |

Run-level: `rule_engine_coverage` (must be `1.0` - a **precondition gate**,
reported separately from the LLM scores above, never as this suite's
achievement), `llm_step_error_rate`, `mean_tokens_per_case`,
`total_cost_usd`.

## Running

```bash
# Local, vendor-independent - the number that gets committed to docs/evals/
uv run python -m evals.run_baseline --no-langfuse

# Smoke test on 3 cases
uv run python -m evals.run_baseline --no-langfuse --limit 3

# Repeat 3x to check run-to-run stability (recorded, never gated on -
# temperature=0 is not a determinism guarantee on a hosted MoE endpoint)
uv run python -m evals.run_baseline --no-langfuse --stability 3

# Full run through Langfuse's own experiment runner (requires the dataset
# to be synced first, and LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY set)
uv run python -m evals.sync_dataset
uv run python -m evals.run_baseline
```

## What this does not measure

See the "what this does not measure" section of the committed baseline in
`docs/evals/`. In short: whether an escalation was worth a person's
attention (no real escalation-derived data exists yet), retrieval quality
(no retrieval exists yet), and prompt-to-prompt regressions over time (no CI
gate exists yet - that is Phase 5).

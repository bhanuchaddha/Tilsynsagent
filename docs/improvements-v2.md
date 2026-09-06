# Improvements v2

A second round, opened after [`improvements.md`](improvements.md) was handed
to an implementation session. **Nothing here is being worked on yet.**

`improvements.md` (entries 1–8) covers the decision path: what the model sees,
what it returns, and the demo built on it. This file covers the layer around
it — where scoring runs, what generates the traffic it scores, and what is
still missing.

Numbering continues from 8 so the two files never collide.

---

## 9. Move online scoring to Langfuse-side evaluators

**Status:** identified 2026-09-06, not yet done.
**Changes stage 3 of [`plan.md`](plan.md).**

### What is there now

Scoring runs **in-process**. `runner.py:107` calls `score_completed_run`
after each run, and `obs/online.py:382` pushes the results to Langfuse with
`create_score`. Langfuse is a sink: it stores scores this codebase computed.

### What it should be

Langfuse evaluators configured to run against traces as they arrive. Scoring
happens outside the agent process.

### Why

- **A new scorer is a config change, not a deploy.** Today adding one means
  editing Python, testing it, and shipping.
- **Every trace gets scored, not a sample.** `FILING_SAMPLE_RATE = 0.25`
  (`obs/online.py:55`) means 75% of filings are never scored at all. That
  sampling exists to control cost from inside the process; on the Langfuse
  side it is a setting.
- **Scorers apply to history.** A new scorer can be run over traces already
  collected. In-process, a scorer only ever sees runs that happen after it
  ships.
- **A crashed run is still scored.** If the agent fails after producing a
  trace, in-process scoring never runs. Langfuse still has the trace.

### The strongest case: the semantic scorer

Improvement 8 needs a scorer that judges whether a citation **supports** its
conclusion — an LLM judge. Langfuse has LLM-as-judge as a first-class
feature, including agreement tracking against human labels.

Building it in-process means also building the judge harness, its sampling,
and its agreement measurement. `obs/judge.py` and
`docs/judge-configuration.md` already exist and are the place to start, but
the question of *where the judge runs* should be settled before more is built
on the in-process assumption.

### What stays in-process

`stayed_in_tool_surface` (`obs/online.py`) checks that a run wrote only what
its outcome permits — a filing must not also produce an escalation row. That
reads the **database**, not the trace. Langfuse cannot see it, so it stays
where it is.

This splits online scoring into two kinds, and the split is principled:

- **Trace-derived** properties (was a source cited, does the citation support
  the conclusion, did an uncovered case escalate) → Langfuse evaluators
- **System-state** properties (what rows the run actually wrote) → in-process

### To verify first

Per `CLAUDE.md`: no external capability becomes a dependency until one real
call against that exact capability has succeeded. Confirm on the free tier
that evaluator runs are not separately metered in a way that makes scoring
every trace impractical — the reason for `FILING_SAMPLE_RATE` was cost, and
moving the scorer does not by itself move the cost.

---

## 10. Run the agent on a schedule against the live register

**Status:** identified 2026-09-06, not yet done.

### The gap

`schedule.py` exists and the nightly workflow runs evals — but **nothing runs
the agent against the live register on a schedule.** Traces appear only when
someone runs it by hand.

### Why it matters more than it looks

The entire reliability layer is built for continuous production volume that
is not being produced:

- **Online scorers** have almost nothing to score
- **Drift detection** (`obs/drift.py`) has no history to detect drift in —
  drift is a change over time, and there is no time series
- **Alerts** (`obs/alerts.py`) fire on thresholds crossed by traffic
- **The annotation → promotion loop** (`obs/annotation.py` →
  `evals/promote.py`) needs production runs for a person to annotate

`docs/evals/nightly/` holds one file. That is the symptom.

### What to build

A scheduled run — the existing nightly workflow is the obvious host — that
fetches from the watermark and processes whatever the register has produced.
Real records, real documents, real traces.

**Verify per `CLAUDE.md` before depending on it:** one real scheduled run
must succeed end to end against plandata.dk before this counts as working.
Rate limits and feed behaviour under repeated automated access have not been
exercised.

### Why this is arguably the highest-leverage remaining work

Everything already built is waiting on this data. A reliability layer with no
traffic through it cannot be shown to work — and *"nothing is done until it is
demonstrable"* is the project's first working rule.

---

## 11. What is actually still missing — the honest list

**Status:** assessment, 2026-09-06.

Taking stock, because the gap list is shorter than it feels.

### Already built

| Capability | Where |
|---|---|
| Online scorers | `obs/online.py` (3), `obs/grounded.py` (4) |
| Offline scorers | `evals/scorers.py` |
| CI gate on the dataset, real model calls | `.github/workflows/evals.yml` |
| Nightly eval + regression check + alerting | `.github/workflows/nightly.yml` |
| Dataset sync | `evals/sync_dataset.py` |
| Trace → annotation → dataset loop | `obs/annotation.py`, `evals/promote.py` |
| Drift detection | `obs/drift.py` |
| Alerts, committed to git | `obs/alerts.py` |
| Prompt registry and executed rollback | `llm/prompts.py`, `llm/rollback.py` |
| LLM judge scaffolding | `obs/judge.py`, `docs/judge-configuration.md` |

### Actually missing

1. **A semantic scorer** — every existing scorer is structural (the quote is
   real, the quote is verbatim, an uncovered case escalated). Nothing checks
   whether a citation *supports* its conclusion. Improvement 8 depends on it;
   improvement 9 changes where it should live.
2. **Traffic** — improvement 10. The layer is built; nothing is flowing
   through it.
3. **The UI** — improvement 3 in `improvements.md`.

### The one-line assessment

**A well-built reliability layer with very little traffic through it.** The
missing work is not more mechanism — it is volume to exercise the mechanism,
and one scorer that judges meaning rather than form.

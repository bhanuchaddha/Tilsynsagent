# Architecture

What the agent does end to end, and why the model's role is smaller than it
sounds. Phase 1: the agent runs, unattended, against the live register. No
evals, no measurement, no reliability layer yet - that is Phase 2 and 3. This
document describes what exists now.

---

## The decision that shapes everything else

**The rules run in code. The model handles what the rules explicitly do not
cover.**

Four rules ([`docs/rules.md`](rules.md)) decide whether a change to a
municipal local plan matters. They are deterministic field comparisons - "did
the permitted use change", "did a value that used to be recorded go blank" -
and they are applied in plain Python, not by asking a language model to judge
each case. A model re-deriving "is 12.5 greater than 10" adds cost and
latency, and when it disagrees with the rule, that disagreement is almost
always "the model made an arithmetic mistake," not a real judgement call
worth having.

So the model is never asked to apply R1-R4. It is only invoked for the cases
the rule set is explicit about not covering: a sub-area renumbered or split
between versions, a plan cancelled without an obvious replacement, a revision
where no watched field differs from what was last stored, anything that would
require reading the source PDF. On the 34-case hand-labelled dataset this is a
minority of cases, but across the full historical record it is not rare - the
rule set itself documents that data-integrity edge cases and ambiguous
transitions are common, not exotic.

**The line this produces:** the model never decides what is compliant. It
decides what is worth a person's attention, and explains why. Every filed
decision names the rule that produced it; every escalation states what the
register alone cannot settle.

---

## The flow

```
  fetch ──▶ detect ──▶ classify ──▶ ┌─ covered ──▶ decide (code) ──▶ file
   WFS      diff vs    which rule    │                                 │
   query    last run   applies       │                            summarise
                                     │                              (LLM)
                                     └─ not covered ──▶ assess (LLM) ──▶ escalate
                                                        reads source PDF link, │
                                                        states what is      interrupt()
                                                        unclear             persist + pause
```

**fetch** — queries Plandata.dk's WFS endpoint for sub-areas updated since the
last run. A watermark (the highest `datoopdt` timestamp seen, plus the set of
record ids seen at that exact millisecond) means each run only asks for what
is new, rather than re-fetching the whole 110,000-record register.

**detect** — compares the newly fetched version of a sub-area against the
last version this system stored for it, across the five watched fields
(height, storeys, built percentage, zone status, permitted use). Produces a
normalised diff: which fields changed, and what they changed from and to. If
this is the first time the system has seen this sub-area, there is nothing to
compare against, so it is treated as unchanged - not filed, not escalated.

**classify / decide** — the diff is run through the rule engine, which
applies R1 through R4 in a fixed precedence order (data-integrity checks
first, then real value changes, then additions, then removals - alone or
mixed with additions) and returns one of two outcomes plus the rule that
produced it - file or escalate - or "not covered" when no rule matches,
which the engine itself never resolves to "nothing happened."

**assess** (model, only on "not covered") — given the change, the exact
wording of what the rule set says it does not cover, and a link to the source
document, the model states what specifically is unclear and what a person
needs to decide. It does not read the PDF itself and does not guess at intent
it cannot verify from the record.

**summarise** (model, only on "file") — turns an already-made decision into a
short, factual record: what changed, which rule filed it, and a citation. The
decision is not the model's to make here; it is writing, not deciding.

**file / escalate** — two write tools, each with a declared permission
surface (which database tables it may write, which outcome value it may
record, how many calls it may make in one run) checked *before* the write
happens, not after. A call outside that surface is refused. Escalation calls
LangGraph's `interrupt()`, which pauses the whole run and persists its state
to Postgres; a person resolves it later and the run resumes from exactly
where it paused - including after the process that started it has exited and
the database has gone idle and reconnected.

---

## Why each infrastructure choice

| Piece | Choice | Why |
|---|---|---|
| Orchestration | LangGraph 1.0 | `interrupt()` / `Command(resume=...)` / `PostgresSaver` are the primitives production systems use for human-in-the-loop escalation (the same pattern Klarna's support agent uses). A framework a buyer has heard of costs nothing to explain. |
| LLM | Groq, `openai/gpt-oss-120b` | Free tier of 1,000 requests/day removes the eval-budget constraint that shaped the original Gemini-based plan (as low as ~20/day). Strict structured output (JSON schema, `strict: true`) is verified live before anything is built on it. |
| Database | Postgres on Neon | The register is relational in substance (`komnr`, `lokplan_id`, `delnr`, `versionsnr` behave as foreign keys even though the source is a flat WFS feed), and so is the domain: a sub-area has versions, versions produce diffs, diffs produce filings or escalations, escalations become dataset cases. Neon auto-suspends idle compute but wakes on connection, rather than requiring a manual unpause after a week idle - important for a system meant to run unattended and be live when someone opens it. |
| Observability | Langfuse Cloud (Phase 2+) | Free tier covers what evals and prompt versioning need without running ClickHouse/Redis/S3 locally. Its 30-day retention is a known limitation: anything that needs to outlive that (a baseline score, a caught regression, a rollback timing) is committed into `docs/` at the moment it happens. |

---

## What the rule engine actually checks

Source: [`src/tilsynsagent/rules/engine.py`](../src/tilsynsagent/rules/engine.py).

Precedence, first match wins:

1. **R1** - physically impossible records (more storeys than metres of
   height, a height of exactly 0, or built percentage falling to exactly 0)
   are escalated regardless of what else changed, because a conclusion drawn
   from an unreliable record is itself unreliable.
2. **R2** - any watched field changing from one value to a different value -
   permitted use, a dimensional limit, or zone status - is filed. Use
   outranks dimensions outranks zone, in the reason text only; the outcome is
   file regardless.
3. **R3** - a field going from blank to a value, with nothing lost in the
   same revision, is filed. The reader now sees a limit they did not see
   before.
4. **R4** - any field going from a value to blank, alone or mixed with
   fields gaining values in the same revision, is escalated. The figure a
   reader would quote is gone, and the register alone cannot say why.

A revision where the sub-area has been seen before but no watched field
differs from what was last stored falls through all four rules and is
explicitly routed to the model as "not covered," which always escalates from
there - the register alone cannot say what moved, only that something did.

---

## The review queue (Phase 3)

An escalation pauses the run with `interrupt()` and persists its state to
Postgres. `tilsynsagent review` launches a Streamlit screen
(`src/tilsynsagent/review/app.py`) over the open escalations: what changed,
what the agent could not decide, which rule fired or that none did, a link to
the source document. Answering one writes the resolution
(`db/repo.insert_escalation_resolution`) and resumes the paused run against
its stored thread id (`graph.resume_run`) so it runs to completion. A
resolution can optionally be appended to the golden dataset as a new case
tagged `escalation-derived` - a manual button, not automatic, so the dataset
stays a definition of what is correct rather than a record of what the agent
did.

`tilsynsagent seed-demo` / `reset-demo` (`src/tilsynsagent/demo/`) feed
hand-labelled golden cases through the real graph as if they had arrived from
the register, so the review queue can be demonstrated without waiting on the
live register to produce the right kind of change. Rows they create are
marked `is_test_data` (migration `003_demo_data.sql`); reset deletes only
those rows and their LangGraph checkpoints, never live run history.

## What is not built yet

No evaluation-blocking CI gate, no prompt versioning, no retrieval/grounding
- those are Phase 5 and 6. No online eval on live (non-golden) runs - Phase
4. `ignore` is not a route the eval pipeline can score yet: an escalation
resolved `ignore` through the review screen is deliberately excluded from
"Add to dataset" until Phase 6 gives it a route.

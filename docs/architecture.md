# Architecture

What the agent does end to end, and why the model's role is smaller than it
sounds. Phase 1: the agent runs, unattended, against the live register. No
evals, no measurement, no reliability layer yet - that is Phase 2 and 3. This
document describes what exists now.

---

## The decision that shapes everything else

**The rules run in code. The model handles what the rules explicitly do not
cover.**

Seven rules ([`docs/rules.md`](rules.md)) decide whether a change to a
municipal local plan matters. They are deterministic field comparisons - "did
the permitted use change", "did a value that used to be recorded go blank" -
and they are applied in plain Python, not by asking a language model to judge
each case. A model re-deriving "is 12.5 greater than 10" adds cost and
latency, and when it disagrees with the rule, that disagreement is almost
always "the model made an arithmetic mistake," not a real judgement call
worth having.

So the model is never asked to apply R1-R7. It is only invoked for the cases
the rule set is explicit about not covering: a sub-area renumbered or split
between versions, a plan cancelled without an obvious replacement, anything
that would require reading the source PDF. On the 34-case hand-labelled
dataset this is a minority of cases, but across the full historical record it
is not rare - the rule set itself documents that data-integrity edge cases
and ambiguous transitions are common, not exotic.

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
applies R1 through R7 in a fixed precedence order (data-integrity checks
first, then real value changes, then additions, then removals, then mixed
changes) and returns one of three outcomes plus the rule that produced it:
file, escalate, or "not covered."

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

1. **R5** - physically impossible records (more storeys than metres of
   height, or a height of exactly 0) are escalated regardless of what else
   changed, because a conclusion drawn from an unreliable record is itself
   unreliable.
2. **R6** - built percentage falling to exactly 0 is escalated.
3. **R1 / R2** - a real change to permitted use, or to a dimensional limit,
   is filed. Use outranks dimensions: it decides what may be built at all.
4. **R3** - a field going from blank to a value, with nothing else changing,
   is ignored. The register was completed, not changed.
5. **R4** - a field going from a value to blank is escalated. The figure a
   reader would quote is gone, and the register alone cannot say why.
6. **R7** - some fields gaining values while others lose them, in the same
   revision, is escalated as more likely a record being reworked than a real
   rule change.

A zone-status change occurring alone, with no use or dimensional change
alongside it, falls through all seven rules and is explicitly routed to the
model - the rule set does not state what a reclassification alone means, and
the engine does not guess.

---

## What is not built yet

No evaluation harness, no baseline score, no CI gate, no prompt versioning -
those are Phase 2 and 3. No UI: the record of what happened is the
`escalations` and `filings` tables plus the run log. This phase exists so
those later phases have something real to measure; it does not measure
itself.

# Implementation plan

Derived from [`improvements.md`](improvements.md) (the work) and
[`understanding.md`](understanding.md) (the system today, and the six-case
demo this builds toward).

**Read both of those first.** This document is the sequence and the traps,
not the reasoning — the reasoning is in `improvements.md` and is not repeated
here.

---

## Working rules that apply to every stage

From `CLAUDE.md`, and they bind this work:

1. **Nothing is done until it is demonstrable.** Code written is not done.
2. **Every real failure becomes a test case.** A fix is not complete until the
   case that caught it is in the eval suite.
3. **Baselines get recorded, however bad.** Do not tune before recording the
   first number.
4. **Every feature ships with demo data and a reset.**
5. The one rule: **every autonomous decision must be traceable to the source
   that justified it, and the agent must escalate rather than guess.**

Two additions specific to this plan:

- **Record a baseline before stage 1 and after every stage.** Stages 1–3
  change what the model sees; without a before-number, "it got better" is an
  assertion.
- **343 tests pass today.** That is the floor. A stage that reduces coverage
  is not finished.

---

## State of the working tree

Uncommitted changes from an earlier session, already made and passing:

- `docs/incidents/` → `docs/alerts/`, with the "incident" concept removed from
  `obs/alerts.py`, `drift.py`, `annotation.py`, `online.py`, `demo/reset.py`,
  `review/app.py`, `docs/architecture.md`, `.github/workflows/nightly.yml`
- `evals/golden/grounding_cases.jsonl` — golden set v2, five cases (GR-001…005)
  carrying the grounded outcome and `expected_clause_id`
- `evals/cases.py` — `load_grounding_cases()` and `grounding-v2` routing

Commit these first, separately, so the stages below start from a clean tree.

---

## Stage 0 — baseline

Record the current numbers before changing anything.

    uv run python -m evals.run_baseline --no-langfuse

Commit as `docs/evals/baseline-<date>.md`, following the format of the three
existing baselines. **Do not edit it afterwards** — the existing ones are
comparable precisely because none of them were revised.

Also record, from a demo run, the current **grounding rate** (proportion of
uncovered cases the model decides rather than escalates). Stages 1–3 should
move it, and there is no way to say so without this number.

---

## Stage 1 — one rewrite of the grounding contract

**Improvements 4, 5 and 6 together.** They all rewrite `ground.py`'s prompt
and return type. Done separately that is three rewrites of the same code and
three baselines that cannot be compared. Done once it is one coherent change:
*the model sees everything, and names its source.*

### 1a. Remove clause retrieval (improvement 4)

Delete:
- `src/tilsynsagent/documents/clauses.py` (241 lines)
- `tests/test_clauses.py` (149 lines)
- the `split_clauses` / `retrieve_for_fields` calls in `graph.py:417-418`
- `retrieved_clause_ids` from `GraphState` (`graph.py:115`)

Keep `documents/cache.py` and `documents/extract.py` — fetching and text
extraction are still needed.

`build_prompt` takes the **document text**, not a clause list.

### 1b. Pass the whole record (improvement 5)

`build_prompt` receives the full `SubAreaRecord` before and after — every
field in `sources/plandata.py:54`, not only the five watched ones. `status`,
`versionsnr`, `datoopdt` and the rest all reach the model.

The five watched fields keep their special status **in the rule engine
only**. R1–R4 are unchanged.

### 1c. The output contract (improvement 6)

`Grounding` gains the ability to cite **either** a clause **or** a named
register field, and `is_decided` must accept both — otherwise a correct
field-based decision is discarded as uncheckable, and case 2 of the demo
cannot work.

Abstention returns **what was found**: the passages and fields that bear on
the change, quoted with ids, then why they do not settle it. Not "the clauses
given do not cover this".

### The trap in this stage

`obs/grounded.py:90` — **`clause_id_exists` validates the cited clause against
`retrieved_clause_ids`**, which 1a deletes. It cannot be kept as-is and it
must not be dropped: it is the scorer that catches a fabricated clause number,
and grounded decisions are the only ones no person reviews.

Rewrite it to check the cited clause id against the **full document text**.
That is a stronger check than the current one, and it is the only version that
survives 1a. `clause_is_verbatim` similarly checks against the whole document
rather than a retrieved clause's text.

`db/migrations/004_grounding.sql:48` has a `retrieved_clause_ids` column.
Write a migration `005` — do not edit `004`.

### Done when

- Baseline re-run and committed; grounding rate compared to stage 0
- `clause_id_exists` and `clause_is_verbatim` pass against full documents
- A grounded decision citing a **register field** is accepted end to end
- An abstention names what it found
- Test count is not lower than 343

---

## Stage 2 — offline evals ground against the real PDF (improvement 2)

Golden set v2 already carries a real `source.document` URL and
`requires_document: true` on every case. The eval runner ignores it.

Fetch through the same `documents/cache.py` path production uses — a scorer
using a different path measures something the system does not do. Assert the
outcome **and** the cited source.

**The trap:** a document that fails to fetch must fail the suite loudly. A
skipped grounding case reporting as a pass is worse than no case at all.
Cache aggressively; these are 30 MB PDFs.

**Done when** GR-001…005 are scored on their real documents, and a deliberately
broken URL fails the suite rather than skipping.

---

## Stage 3 — the semantic scorer (improvement 8's hard part)

> **Read [`improvements-v2.md`](improvements-v2.md) entry 9 before starting
> this stage.** It argues the semantic scorer should run as a Langfuse-side
> evaluator rather than in-process, which changes what gets built here. That
> question is open and should be settled first.

An online scorer that catches **citation does not support conclusion**.

Everything existing is structural — the quote is real, the quote is verbatim.
This one judges meaning, so it is an LLM judge. `docs/judge-configuration.md`
and `obs/judge.py` already exist; extend rather than start over.

This is the single hardest piece of work in the plan and the one demo 6
depends on. **Record its agreement with human labels before relying on it** —
a judge nobody has measured is not evidence.

---

## Stage 4 — the demo set (improvement 7, then 8)

Build in this order, so each case is demonstrable as it lands:

| Case | Needs | Data |
|---|---|---|
| 1 — R2, no model | nothing | 16 real cases exist |
| 3 — filed from clause 6.3 | stage 1 | exists |
| 4 — ignored via clause 11.1 | stage 1 | exists |
| 2 — filed from `status: F → V` | stage 1c | GR-002, GR-004 exist |
| 5 — escalated with findings | stage 1c | **build** — a record whose extra fields *and* document are both suggestive but jointly inconclusive |
| 6 — wrong decision, loop closes | stage 3 | **build** — misleading document line + a prompt version carrying the shortcut, kept in the registry beside the fixed one |

`demo/documents.py` already builds documents through the entire real path
(disk → `file://` → cache → pypdf), and `demo/seed.py` already drives golden
cases through the real graph. Both extend routinely.

**The `drifted` template becomes dead weight** once stage 1a removes the
splitter it exists to break. Delete it — improvement 8 replaces the argument
it was making, and makes it better.

Every case ships with demo data and a reset (`demo/reset.py`).

---

## Stage 5 — the UI (improvement 3)

Next.js, replacing `review/app.py`. Two screens:

**Run log** — every processed record, one row each. Not only escalations: the
filed and ignored decisions are the volume, and they are what shows the system
working. Each row: the record, what changed, which path decided it (rule id or
cited source), the outcome, when.

**Operator queue** — escalations only, heading stating what must be decided.
Clicking opens three panes: the **document** on the left; the **extraction** in
the middle (five fields before/after, what the model found, and — equally
important — what it could not find); the **decision** on the right.

The operator must never leave the screen to check the agent's reasoning
against the source.

Left last deliberately: it renders what stages 1–4 produce, and building it
first means building it twice.

---

## Stage 6 — remove `assess` (improvement 1)

Last, and only after stage 1 has settled.

`assess` is one model call whose output is read only on the escalation path,
and stage 1c makes `ground` return a better version of the same text — from
the model that actually read the document.

`graph.py:572` already falls back:
`state.get("assessment_what_is_unclear") or state["rule_reason"]`.

**Check first:** whether any eval scorer grades `assess` output directly. If
one does, removing the node silently drops a metric, and the replacement on
the abstention text must be established before the removal — a baseline is
not comparable across a scorer that changed shape.

Deferred to last because it is the only stage that removes a human-facing
explanation, and it should be removed once its replacement is proven, not on
the expectation that it will be.

---

## Order, and why

    0  baseline
    1  grounding contract        (improvements 4 + 5 + 6, one change)
    2  offline evals on real PDFs (improvement 2)
    3  semantic scorer            (improvement 8, hard part)
    4  demo cases                 (improvements 7, 8)
    5  UI                         (improvement 3)
    6  remove assess              (improvement 1)

Stage 1 first because everything else observes its output. Stage 6 last
because it deletes something whose replacement stage 1 creates. The UI is
late because it displays what the earlier stages produce.

---

## What to confirm before starting

1. **Cost.** Stage 1 sends whole documents — ~27k tokens versus ~4k, roughly
   25x per grounded record. Decided: not a design constraint, more models can
   be integrated if throughput binds. Measure it during stage 1 so the
   decision is informed rather than assumed.
2. **Case 5's data** must be constructed, not found. Agree what "suggestive
   but jointly inconclusive" means concretely before building the document.
3. **The judge in stage 3** needs measured agreement with human labels before
   demo 6 rests on it.

# How the LLM is used, and what decides what

Plain-language account of where the model sits in this system, what it is
allowed to decide, and what it is not. Written for a reader who has not seen
the code.

---

## The shape

    detect ─► decide (code) ─┬─ file      (R2/R3) ──────────► done, no human
                             ├─ escalate  (R1/R4) ──────────► human
                             │
                             └─ not_covered ─► ground ─┬─ file    ► done
                                                       ├─ ignore  ► done
                                                       └─ can't   ► human

Deterministic Python decides first. The model is only reached when the rules
decline to decide.

---

## What the code decides

Five fields are watched: height (`maxbygnhjd`), storeys (`maxetager`),
built percentage (`bebygpct`), zone status (`zonestatus`), permitted use
(`anvendelsegenerel`).

Four rules, first match wins:

| Rule | Condition | Outcome |
|---|---|---|
| R1 | Record is physically impossible — storeys exceed height in metres, height recorded as 0, built % falls to 0 | **escalate** |
| R2 | A watched field changed from one value to a different value | **file** |
| R3 | Field(s) gained a value, none lost | **file** |
| R4 | A field lost its value | **escalate** |

R1 is checked first on purpose: a conclusion drawn from a broken record is
unreliable however routine the change looks.

**R1 and R4 never reach the model.** No document can say whether the register
transposed two numbers, or why it dropped a value. Those are data-integrity
problems, not questions of meaning.

---

## Why `not_covered` exists

The register publishes a new version whenever the municipality republishes a
record — for any reason. The record carries many more fields than the five we
watch. So a new version can arrive with all five of our fields identical.

That happens when they changed something we don't watch, fixed a typo, or
re-published administratively.

We cannot skip it, because two very different situations look identical from
the five fields alone:

- they corrected a typo — nothing happened
- they changed a height limit **in the plan document text**, and the register
  field has not caught up — something real happened

Telling those apart is the entire reason the model opens the document.

---

## What the model decides

`ground` is given the change and the source plan document. It answers one
question: **is there anything in this document that changes what may be built
on this land?**

Three outcomes:

**file** — the document shows a real change affecting what can be built.
Example: register unchanged, but §6.3 reads *"maximum height for area 3 is
reduced from 12 m to 8.5 m."* Filed, with that clause cited.

**ignore** — the document confirms nothing relevant changed. Example:
*"corrected typographical error in §4.2; no changes to provisions."*
This is not "nothing happened, why are we here" — it is the stronger,
citable claim: *we read the document and confirmed nothing relevant happened.*

**cannot decide** — no clause settles it. A scanned map with no readable
text, or a document that discusses the area but never mentions what may be
built. Goes to a human, with the model's reasoning about what the clauses
did not cover.

---

## The contract that makes a decision actionable

The model returns structured output: a decision, a clause ID, and a verbatim
quote of that clause.

**A decision is only acted on autonomously if all three are present.**
`Grounding.is_decided` (`llm/ground.py:95`) is deliberately stricter than the
model's own `can_decide` flag — if the model claims it can decide but returns
no clause ID, no quote, or an outcome outside `file`/`ignore`, the code
overrides it and escalates.

The rule: **a citation exists → decide. No citation → human.**

### What the model is given, and what it is not

Today the model sees exactly four things:

1. the sub-area description
2. the **five watched fields**, before → after
3. the document link
4. **four clauses** selected from that document by a retrieval step

It does **not** see the rest of the register — `status`, `versionsnr`,
`datoopdt`, municipality, plan id. So it cannot say *"they republished
because the plan was adopted"*, even when that is the whole explanation.

It does **not** see the whole document either. A splitter picks ~4 clauses
(~4k chars) based on which fields changed, as a cost optimisation.

**Both of these are limitations, not design principles**, and both are
recorded for removal — improvements 4 and 5 in
[`improvements.md`](improvements.md). The intended shape is: the model sees
the whole document and every register field, and decides from all of it.

### Why not a confidence score

A model's self-reported confidence is a number it invents. It is not
calibrated — 0.85 does not mean 85%. Tuning a threshold against it means
tuning against something that measures nothing.

"Did you quote a real clause" is checkable. Confidence is not.

### What this check does and does not cover

It is **structural**: it verifies a citation was produced. It does not yet
verify the quote appears in the document, or that it supports the conclusion.
A model can quote a real clause that does not say what it claims.

That gap is measured separately, after the fact, by the eval layer — not by
the decision path.

---

## The one rule this enforces

Every autonomous decision traces to the source that justified it:

- rule-decided cases trace to a rule ID (R1–R4)
- grounded cases trace to a quoted clause in a named document

There is no third path to an autonomous decision. Anything uncheckable
escalates.

---

## The demo — six cases, in order

**Target state.** Cases 1, 3 and 4 run today; 2, 5 and 6 depend on
improvements 3–6 and 8 in [`improvements.md`](improvements.md).

The order matters. Each case shows one more of the system than the last, and
the escalation comes late — by then the audience has watched it decide four
cases alone, so "it knew it could not decide" reads as judgment, not as a gap.

| # | Shows | Decided by | Outcome |
|---|---|---|---|
| 1 | rules decide, no model call | R2 | file |
| 2 | a register field we do not watch | `status: F → V` | file |
| 3 | a change invisible in the register | clause 6.3 | file |
| 4 | closing a case without filing | clause 11.1 | ignore |
| 5 | knowing it cannot decide | nothing conclusive | escalate |
| 6 | a wrong decision, caught in production | — | the loop |

---

### Case 1 — deterministic, no model

A watched field changed value. R2 fires. Filed.

The row shows before → after, **R2**, filed, and *decided by rule — no model
call*.

The point: the common case never reaches an LLM, and the reasoning is a rule
id, not a probability.

*Data: 16 real R2 cases in the golden set.*

### Case 2 — filed from a field the rules do not watch

All five watched fields identical, so the rules decline. The model sees the
whole record and finds `status: F → V` — *Forslag* → *Vedtaget*, the plan
formally adopted. Filed, naming that field as its source.

The point: the model reads **outside** the deterministic rule set, and can say
which field it used.

*Data: GR-002, GR-004 — real Egedal and Ballerup records. Blocked on
improvement 5.*

### Case 3 — filed from the document

Five fields identical, nothing decisive elsewhere in the record. The model
reads the plan and finds clause **6.3**:

> *"Inden for delområde I må der opføres 2 bygninger i 2 etager med hver 8
> boliger. Bebyggelsesprocenten må ikke overstige 40."*

A storey count and a built percentage, stated in prose. Filed, clause quoted
verbatim.

The point: a real change that is **invisible in the register entirely**.

Note the Danish decimal comma in the related clause 6.2 — **8,5 m**. A quote
that tidies it to 8.5 is no longer verbatim and fails the check.

*Data: GR-001, GR-003.*

### Case 4 — ignored, and the ignore is justified

Five fields identical, nothing in the other fields, and the document settles
it the other way — clause **11.1**:

> *"En ændring af planens administrative status medfører ikke ændringer i de
> bestemmelser, der gælder for områdets bebyggelse og anvendelse."*
> (A change in the plan's administrative status does not change the
> provisions governing building and use.)

Ignored, with that clause cited.

The point: the system closes a case autonomously **without filing anything** —
and can justify it. Not "nothing happened", but *we read the plan and
confirmed nothing relevant happened.*

### Case 5 — escalated, with everything it found

The full path runs. Something in the extra register fields is suggestive,
something in the document is related, and neither settles it. The model
collects **all of it** and hands it over.

The screen shows side by side: the document, the before/after values, the
fields and passages found relevant, **what could not be concluded**, and the
decision being asked for.

The point: the escalation is the system's strongest moment, not its failure.
It knew it could not decide, and it did the human's reading for them.

*Data: needs building — a record whose extra fields and document are both
suggestive but jointly inconclusive. Blocked on improvement 6.*

### Case 6 — a wrong decision, and the loop closing on it

A plan document contains a line that *sounds* dispositive — *"this change does
not affect the buildings in the area"* — in a plan where something relevant
did change. The prompt at that version carries a shortcut: a clause claiming
no impact means `ignore`.

The model cites the line verbatim and **ignores the case autonomously**.
Every structural check passes: a decision was made, a clause was quoted, the
quote is real. Offline evals stay green.

The decision is wrong — and a grounded ignore is the only outcome no person
reviews.

Then:

1. An online scorer flags it — the citation is real but does not support the
   conclusion
2. A person opens the trace, sees the cited line and the wrong outcome
3. The run is annotated with the correct label
4. `evals/promote.py` pulls it into the golden set as `AQ-###` — written to
   the working tree, **committed by a person**, because the dataset defines
   what is correct and production traffic must not edit its own specification
5. The prompt is fixed and versioned; the broken version stays available to
   roll back to
6. The CI gate replays the case on every pull request, forever

**The point, and the reason this case is last:** offline evals prove *you*
did not break it. They cannot prove the *world* did not — the golden set is
frozen while the register keeps producing shapes no one has labelled. This
case was not in the golden set and could not have been. Online scoring caught
it anyway, because it checks properties of a decision rather than comparing
to a known answer.

The full circuit on one case: **wrong autonomous decision → caught in
production → labelled → promoted into the dataset → prompt fixed → cannot
recur.**

*Needs: the demo document, the shortcut prompt version, and an online scorer
that judges whether a citation supports its conclusion — a semantic check,
which is the real work. See improvement 8.*

---
## Known redundancy

An `assess` step currently runs between `not_covered` and `ground`. It
produces prose for a human but never a decision, and `ground` now returns the
same explanation from better information — it has actually read the document.
Recorded as improvement 1 in [`improvements.md`](improvements.md).

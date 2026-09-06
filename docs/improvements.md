# Improvements

Changes identified as worth making, with the reasoning that led to them.
Each entry states what is wrong now, why it is wrong, and what removing or
changing it costs.

---

## 1. Remove the `assess` node

**Status:** identified 2026-09-06, not yet done.

### What is there now

The not-covered branch runs two model calls in sequence:

    not_covered -> assess -> ground -+- file
                                     +- ignore
                                     +- escalate

`assess` sees only the diff. It returns `what_is_unclear` and
`what_a_person_must_decide` — prose for a human, never a decision.
`ground` then opens the source document and either decides with a cited
clause or abstains.

### Why it should go

`assess`'s output is only ever read on the escalation path. When `ground`
abstains it already returns a `reasoning` field describing, per
`llm/ground.py:86`, "what the clauses given do not cover" — the same
explanation, produced by the model that actually read the document.
`assess` is guessing at what is unclear from the diff alone; `ground` knows.

So the call is one extra model round-trip per uncovered case, permanently,
for text that is now duplicated by a better-informed step.

### Why it is still there

`ground` was added after `assess` and deliberately made purely additive
(`graph.py:15`): a record that escalated before still escalates, with the
same explanation. That was the right way to ship a step that can decide
autonomously — the new path could only widen behaviour, never change it.
That safety margin has served its purpose.

### What depends on it

- `graph.py:99-100` — two `GraphState` fields.
- `graph.py:185,203,335` — the node, its edge to `ground`, and the routing
  target at `graph.py:331`.
- `graph.py:572,581` — the escalation note. **Already falls back:**
  `state.get("assessment_what_is_unclear") or state["rule_reason"]`, with
  `grounded_reasoning` appended when present. Removing `assess` degrades
  this path to rule reason + grounding reasoning, which is the pair a
  person actually needs.
- `llm/prompts.py:53` — `ASSESS_PROMPT_NAME` and its pinned default text,
  registered in Langfuse.
- `evals/tasks.py`, `evals/cases.py`, `tests/test_llm_live.py`,
  `tests/test_experiment.py` — eval tasks and tests that exercise it.

### What it buys

- One fewer model call per uncovered case: lower latency, lower quota use
  (relevant on Groq's free tier).
- The escalation note comes from the model that read the document rather
  than from one that saw only five field values.
- One fewer prompt to version, evaluate, and keep in the registry.

### What to check before doing it

Whether any eval scorer measures `assess` output directly. If a scorer
grades `what_is_unclear`, removing the node removes the metric, and the
replacement metric on `grounded_reasoning` has to be established first —
a baseline is not comparable across a scorer that silently changed shape.

---

## 2. Offline evals must ground against the real PDF

**Status:** identified 2026-09-06, not yet done.

Golden set v2 (`evals/golden/grounding_cases.jsonl`) carries a real
plandata.dk document URL in `source.document` and `requires_document: true`
on every case. The offline eval runner does not yet fetch it.

Until it does, offline evals score the rule engine and the field diff, but
never the step that actually makes the autonomous decision. A grounded
outcome that is right for the wrong reason — right answer, wrong clause, or a
quote that is not verbatim — passes offline today.

**What to change:** the eval task layer should fetch `source.document`
through the same `documents/cache.py` path production uses, run the real
`ground` node against it, and assert both the outcome *and*
`expected_clause_id`. Same fetch, same parser, same clause splitter — a
scorer that used a different path would be measuring something the system
does not do.

Fetching real 30 MB PDFs makes the suite slow and network-dependent, so the
fetch must be cached and the suite must fail loudly rather than silently
skipping when a document cannot be retrieved. A skipped grounding case that
reports as a pass is worse than no case at all.

---

## 3. Replace the review UI with a Next.js application

**Status:** identified 2026-09-06, not yet done.

The current Streamlit app (`review/app.py`) is not good enough to operate or
to demonstrate. Replace it with a Next.js UI.

### Screen 1 — the run log

Every processed record, one row each. Not only escalations: the filed and
ignored decisions are most of the volume and are what shows the system
working. Each row carries the record, what changed, which path decided it
(rule ID or grounded clause), the outcome, and when.

### Screen 2 — the operator queue

Escalations only. The heading states what the operator must decide.

Clicking a row opens a three-part view:

**Left — the source document**, rendered in the page, scrolled to the
relevant part where one is known.

**Middle — what the agent extracted:** the five register fields as before /
after values, then what grounding found in the document, and — equally
important — **what it could not find**. "Clause 6.2 and 6.3 did not parse" is
the fact that explains why this reached a human, and it is the fact currently
hardest to see.

**Right — the decision.** What the operator is being asked, and the action.

The purpose of the layout is that the operator never has to leave the screen
to check the agent's reasoning against the source. The document, the
extraction, and the decision are visible at once.

---

## 4. Remove clause retrieval — send the whole document

**Status:** identified 2026-09-06, not yet done.
**Supersedes part of improvement 2.**

### What is there now

`documents/clauses.py` (241 lines) splits a plan document on `CLAUSE_RE` and
selects roughly four clauses (~4k chars) matching the changed fields. Only
those reach the model. `llm/ground.py`'s `build_prompt` says the whole
document is never sent, on grounds of cost — ~27k tokens versus ~4k, about
25x per grounded record.

### Why it should go

**Retrieval is the system's largest failure source, and it fails silently.**
The drifted-template demo does not show the model deciding wrongly. It shows
the splitter failing to match a clause shape, retrieval returning nothing
useful, and the model abstaining on a question it was never shown. Grounding
rate falls ~90% → ~35% and the model was correct every time — it simply never
saw the answer.

Sending the whole document removes that class of failure entirely. An
abstention then means what it says: the model read the plan and nothing in it
governs this change.

**It is simpler to explain.** Rules decide what they can. What they cannot,
the model reads the document and decides. What it cannot cite goes to a
person. There is no retrieval step to describe or defend.

**It fits the architecture that already exists.** A deterministic system with
intelligence plugged into the gap it declines to cover. When the model keeps
finding the same pattern in documents, that pattern gets codified as a new
rule in deterministic code — R5, R6 — and stops needing the model at all.
The model is a discovery mechanism for rules not yet written. Retrieval gets
in the way of that by deciding in advance what the model is allowed to notice.

### On auditability

This does not weaken the audit. The requirement is unchanged and is the only
one that matters: **the model must say where its decision came from.**

- deciding from the document → quote the clause verbatim, with its id
- deciding from a register field → name the field

A reference is always present, so a decision can always be traced to what
justified it. Whether the model saw four clauses or the whole plan does not
change that — and checking the quote against the full source document is a
stronger check than checking it against a pre-filtered subset.

The elaborate framing of retrieval as an auditability mechanism was
over-investment. The citation is the audit.

### On cost

The token cost is real and is not a reason to keep this. The current model's
free-tier limit is not a design constraint — more models can be integrated if
throughput becomes the binding problem. That is a question to revisit if and
when it is reached, not to build a subsystem against in advance.

### What to remove

- `documents/clauses.py` and `tests/test_clauses.py` (~390 lines)
- clause retrieval and `render_clauses` from `llm/ground.py`'s `build_prompt`;
  the prompt takes the document text instead
- the clause-selection call in `graph.py`'s `_ground_node`

### What to keep

- **The abstention path.** Meaning changes from "retrieval found nothing" to
  "the model read the plan and nothing governs this", but the route is the
  same: no citation → escalate.
- **`clause_id_exists` and `clause_is_verbatim`** (`obs/drift.py`). These
  become *more* important, not less: they now check the citation against the
  full document, which is what the audit rests on.
- `documents/cache.py` and `extract.py` — fetching and text extraction are
  still needed.

### Knock-on effects

- **Improvement 2** simplifies: the offline eval fetches the PDF and sends it
  whole. No clause splitting, no `expected_clause_id` matching against
  retrieved subsets — the expected clause is checked against the citation the
  model returns.
- **Demo 3 needs rebuilding.** The drifted template exists to break the
  clause splitter. With no splitter, it demonstrates nothing. The argument for
  online evaluation still holds and still needs a demo — but it has to be
  built on a failure that survives this change. Establish what that is before
  removing the splitter, or the demo is lost with it.

---

## 5. Give the model the whole register record, not only the five fields

**Status:** identified 2026-09-06, not yet done.
**Pairs with improvement 4.**

### What is there now

`llm/ground.py`'s `build_prompt` is given `changed_fields` — the diff across
the **five watched fields only**. The rest of the record never reaches the
model.

The register carries more than those five. `SubAreaRecord`
(`sources/plandata.py:54`) also holds `status`, `versionsnr`, `datoopdt`,
`kommunenavn`, `komnr`, `lokplan_id`, `delnr`, `feature_id`, `doklink`.

### Why this is wrong

The uncovered case is *by definition* the one where all five watched fields
are identical. So the model is handed an empty diff and asked what changed —
while the fields that did move are withheld from it.

`status` **F → V** is the clearest example. That is a plan formally adopted:
*Forslag* becomes *Vedtaget*. It is the actual explanation for cases GR-002
and GR-004, and the model never sees it. It has to infer from clause 11.1
that *something* administrative happened, when the register states plainly
what it was.

### What to change

Pass the **whole record**, before and after, not just the watched-field diff.
The model decides from everything available: every register field, and the
full document (improvement 4).

The five watched fields keep their special status **in the rule engine** —
they are what R1–R4 are written against, and that does not change. What
changes is that the model, which only runs where those rules declined, is no
longer restricted to the same five.

### Effect on the citation

The reference requirement is unchanged and covers this directly:

- decided from the document → quote the clause verbatim, with its id
- decided from a register field → **name the field**, e.g. `status: F → V`

Either way the decision names its source. `Grounding` needs a way to express
a field-based citation alongside the clause-based one, and
`is_decided` must accept a named field as a valid reference — otherwise a
correct field-based decision is thrown away as uncheckable.

### Why it is worth doing

It is the same argument as improvement 4, applied to the register instead of
the document: stop deciding in advance what the model is allowed to notice.
If it keeps finding that `status` F → V means "adopted, nothing new to see",
that becomes a deterministic rule — R5 — and stops needing a model call at
all. That promotion cannot happen while the model is prevented from seeing
the field.

---

## 6. One output contract: decision, source, and — when abstaining — what was found

**Status:** identified 2026-09-06, not yet done.
**Consolidates the citation requirement across improvements 4 and 5.**

Whatever the model returns, it names where it came from. Three cases, and the
third is the one not currently satisfied.

### Deciding from the document

Outcome (`file` / `ignore`), the clause id, and the clause quoted **verbatim**.
Verbatim means exactly as written — Danish decimal commas included. A quote
that "tidies" `8,5 m` to `8.5 m` is not verbatim and fails the check.

### Deciding from a register field

Outcome, and the **field named** with its before → after values —
`status: F → V`. No clause is required, because no clause was relied on.
Needs `Grounding.is_decided` to accept a named field as a valid reference
(improvement 5), or a correct field-based decision is discarded as
uncheckable.

### Abstaining — the part that is missing

Today abstention returns `reasoning` describing, per `llm/ground.py:86`,
"what the clauses given do not cover". That is framed as an absence, and it
hands a person nothing to start from.

What it should return instead is **what was found**:

- the passages, clauses or fields that bear on this change — quoted, with
  their ids
- and then why they do not settle it

*"Clause 6.2 sets a maximum height of 8,5 m and clause 9.2 governs terrain
regulation. Neither states whether the adopted plan changes the permitted
storey count, which is what moved here."*

That is a starting point. "The clauses given do not cover this" is not.

**Why it matters beyond wording:** this text is what the operator reads. It is
the middle pane of the escalation screen in improvement 3 — what the agent
found, and what it could not conclude. An abstention that names the relevant
passages turns a human review from "read the whole plan yourself" into
"confirm or reject this reading", which is a different amount of work.

### The invariant

Every model output names its source. Decisions name what justified them;
abstentions name what was examined and found insufficient. There is no output
that says only "I don't know".

---

## 7. The demo set — five cases, in order

**Status:** target state, defined 2026-09-06. Depends on improvements 3–6.

The end state this all builds toward: open the UI and walk five cases in
sequence, each showing one more of the system than the last. The escalation
is last because it is only impressive once the audience has seen how much the
system decides on its own.

### Case 1 — deterministic, no model

A watched field changed value. R2 fires. Filed.

The row shows: before → after, **R2**, filed, and *decided by rule — no model
call*. Establishes that the common case never reaches an LLM, and that the
reasoning is a rule id, not a probability.

**Data: exists.** 16 real R2 cases in `evals/golden/cases.jsonl`.

### Case 2 — filed from a register field the rules do not watch

All five watched fields identical. The rules decline. The model sees the full
record and finds `status: F → V` — the plan was formally adopted. Filed, with
that field named as the source.

Shows the model reading **outside** the deterministic rule set and deciding
from it — and being able to say which field it used.

**Data: exists** (GR-002, GR-004 — real Egedal and Ballerup records).
**Blocked on improvement 5**: the model cannot currently see `status`.

### Case 3 — filed from the document

Five fields identical, nothing decisive in the other register fields. The
model reads the plan and finds clause **6.3** — a storey count and a built
percentage stated in prose that the register does not carry. Filed, clause
quoted verbatim.

Shows a real change that is **invisible in the register entirely**.

**Data: exists** (GR-001, GR-003; clauses 6.2 and 6.3 in `DEMO_CLAUSES`).

### Case 4 — ignored, and the ignore is justified

Five fields identical, nothing in the other fields, and the document settles
it the other way: clause **11.1** — *an administrative status change does not
alter the provisions governing building and use.*

Ignored, with that clause cited.

Shows the system closing a case autonomously **without filing anything** —
the outcome most systems cannot justify. Not "nothing happened", but *we read
the plan and confirmed nothing relevant happened.*

**Data: exists** (clause 11.1 in `DEMO_CLAUSES`).

### Case 5 — escalated, with everything the model found

The full path runs. The model finds something suggestive in the extra
register fields, something related in the document, and neither settles it.
It collects **all of it** and hands it to the operator.

The screen shows, side by side: the document, the before/after values, the
fields and passages the model found relevant, **what it could not conclude**,
and the decision the operator is being asked for.

Shows the escalation as the system's strongest moment, not its failure — it
knew it could not decide, and it did the human's reading for them.

**Data: needs building.** A record whose extra fields *and* document are both
suggestive but jointly inconclusive does not exist yet. Constructible from a
real record plus a demo document written to be genuinely ambiguous — not
empty, not decisive.
**Blocked on improvement 6**: abstention must return what was found, not what
was missing.

---

### Is the data there?

Mostly yes. Cases 1–4 rest on real plandata.dk records already in the golden
set, and on clauses already in `DEMO_CLAUSES`. Case 5 has to be built.

`demo/documents.py` already generates plan documents through the entire real
path — written to disk, served over `file://`, fetched by `documents/cache.py`,
parsed by pypdf. Adding a template is routine. `demo/seed.py` already drives
real golden cases through the real graph, so a fifth case joins the same way.

### Is the target reachable?

Yes, and it depends only on work already recorded here: improvement 5 for
case 2, improvement 6 for case 5, improvement 3 for the screens all five are
shown on.

### One thing to settle first

The `drifted` template exists to break the clause splitter, which improvement
4 removes. It is currently the only demonstration of why offline evals are
insufficient — a real argument that must not be lost. Decide what failure
carries that argument **before** the splitter goes, not after.

---

## 8. Demo 6 — the loop closing on a wrong autonomous decision

**Status:** target state, defined 2026-09-06. Replaces the `drifted` template
as the demonstration of why offline evals are insufficient.
**Supersedes the open question at the end of improvement 7.**

The `drifted` template demonstrates the point by breaking a parser that
improvement 4 deletes. This demonstrates the same point with a **wrong
decision the model makes confidently** — which is a better failure, because it
is the one that actually keeps people awake.

### The setup

A demo document containing a line that *sounds* dispositive but is not —
something to the effect of *"this change does not affect the buildings in the
area"* — sitting in a plan where something relevant did in fact change.

The prompt at that version contains a shortcut: if a clause states the change
has no impact, decide `ignore`. Plausible, and passes every offline eval,
because no golden case covers this shape.

### The run

The model reads the line, cites it verbatim, and **ignores the case
autonomously**. Every structural check passes: a decision was made, a clause
was quoted, the quote is real and verbatim. Offline evals stay green.

The decision is wrong. A grounded ignore is the only outcome no person
reviews.

### The catch

An online scorer flags it — the citation is real but does not support the
conclusion. Nothing here required knowing the right answer in advance, which
is the entire argument: **the case was not in the golden set, and could not
have been.**

### The loop

1. Scorer flags the run → alert in `docs/alerts/`
2. A person opens the trace, sees the cited line and the wrong outcome
3. The run is annotated with the correct label
4. `evals/promote.py` pulls it into the golden set as `AQ-###`,
   `origin: annotation-derived` — **written to the working tree, committed by
   a person**, because the dataset defines what is correct and production
   traffic must not edit its own specification
5. The prompt is fixed: a clause claiming no impact is not sufficient grounds
   to ignore
6. The CI gate replays the case on every pull request, forever

### Why this is the demo

It shows the full circuit on one case: **a wrong autonomous decision → caught
in production → labelled → promoted into the dataset → prompt fixed → cannot
recur.** Offline evals could not have caught it. Online scoring could, and did.

It also demonstrates the rollback and the prompt registry in their real
context — the fix is a new prompt version, and the previous one is still
there to roll back to.

### What is needed

- A demo document with the misleading line (a `DEMO_CLAUSES` addition and a
  template — `demo/documents.py` already builds documents through the real
  fetch and parse path).
- A prompt version carrying the shortcut, kept in the registry alongside the
  fixed one so the demo can be replayed from either.
- An online scorer that catches *citation does not support conclusion* —
  a semantic check, not a structural one. This is the real work: the existing
  grounded scorers verify the quote is real and verbatim, not that it means
  what the model claimed.

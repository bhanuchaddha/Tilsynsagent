# Golden dataset

Real changes drawn from Plandata.dk history, labelled against
[`docs/rules.md`](../../docs/rules.md).

**34 cases**, in [`cases.jsonl`](cases.jsonl), one JSON object per line.

**Labelled before the agent was built.** This ordering is the point: a dataset
written after the system exists tends to encode what the system already does
rather than what it should do. These labels are a standard the agent can fail
against, not a description of its behaviour.

Six of the 34 (`ZL-024`–`ZL-029`) were originally labelled `ignore` under rule
set v1 and are now labelled `file` under v2's R3 - the same field-populated-
for-the-first-time cases, mechanically relabelled by applying v2's rules
rather than re-labelled by hand. See "Composition," below, on why they carry
`origin: "rule-derived-v2"` rather than `"hand-labelled"`.

## What a case records

| Field | Meaning |
|---|---|
| `id` | stable case identifier |
| `label` | `file` / `escalate` — the correct outcome |
| `reason` | one line, why that is the correct outcome |
| `rule` | which rule in `docs/rules.md` decides it. **`null` on escalation-derived cases** — their label came from a person, not a rule |
| `escalated_rule` | escalation-derived cases only: the rule that *escalated* the case to a person (or `null` if the engine did not cover it) |
| `rule_set` | which rule set it was judged against |
| `origin` | `hand-labelled`; `rule-derived-v2` for the six cases relabelled by v2's rules rather than by hand; or `escalation-derived` once the running system contributes cases |
| `source` | municipality, plan, sub-area, and a link to the source document |
| `before` / `after` | the two versions, with all five watched fields |
| `changed_fields` | just what differed |

`rule_set` exists because rule sets are documents, and more than one can be
evaluated against the same change. `origin` distinguishes cases written up
front from cases mechanically relabelled when the rule set changed, from
cases the system earned by getting something wrong.

`ignore` is not a possible label: the rule engine only ever files or
escalates - see `docs/rules.md`'s "The two outcomes."

## Composition

| Label | Cases |
|---|---|
| file | 22 |
| escalate | 12 |

Every rule R1–R4 has at least two cases:

| Rule | Cases | What it covers |
|---|---|---|
| R1 | 5 | physically impossible record |
| R2 | 16 | a watched field changed to a different value |
| R3 | 6 | field populated for the first time |
| R4 | 7 | a field lost its value, alone or mixed with a gain |

The set is deliberately weighted toward hard cases. Cases the system gets
obviously right teach nothing, so the proportion here does not match the
proportion in the wild — R3-style noise (a field populated for the first
time) is far more common in the register than these six cases suggest, even
though v2 now files it rather than ignoring it.

## Provenance

Drawn from 37,717 historical sub-area records across Zealand (Region
Hovedstaden and Region Sjælland), which contain 1,148 transitions where at least
one watched field changed. The 34 were sampled across change shapes and spread
across municipalities so no single municipality dominates.

Two cases come from outside Zealand. They record a pattern — height and storey
values transposed between versions — that does not occur in the Zealand data,
and the rule it teaches is worth having. They are marked by their municipality.

## Growing the set

**Every real failure becomes a case here.** When the running system gets
something wrong, the fix is not complete until the case that caught it is in
this file, with `origin` set to `escalation-derived`.

That is also what makes improvement measurable rather than asserted: a case that
entered the set because the system failed can be re-run afterwards to show the
failure is closed.

**An escalation-derived case is not the same kind of object as a hand-labelled
one, and the schema says so.** It records what a *person* decided about a case
the engine escalated — and the interesting ones are precisely those where the
person disagreed with the engine. So `rule` is `null` (no rule produced this
label) and the escalating rule is recorded under `escalated_rule`. Putting the
escalating rule in `rule` would assert that R-whatever *files* a case it
actually escalates, and `tests/test_rules_engine.py`'s 100%-engine-agreement
check would fail on a claim the dataset never meant to make.

These cases are therefore excluded from that engine-agreement check and from
the founding-34 composition counts the recorded baselines describe — a
baseline's composition table has to stay comparable to the run that produced
it. They are checked by their own test instead: the engine must still
escalate them, and a case the engine has stopped escalating fails loudly
rather than drifting, because that means either the rule set moved underneath
the dataset or the case is now covered and should be relabelled deliberately.

## The synthetic NOT_COVERED set

`not_covered_cases.jsonl`, in this same directory, is a separate file - never
merged into `cases.jsonl`. It exists because these 34 real cases contain zero
NOT_COVERED transitions, which left the `assess()` LLM step with no eval
coverage at all. Under v2 a NOT_COVERED case is a no-visible-change revision
(the sub-area has been seen before, a new version exists, but no watched
field differs from what was last stored) rather than v1's zone-status-alone
transitions, since v2's R2 makes zone status decidable on its own. See
`evals/README.md` for why this synthetic set is temporary and what should
retire it.

# Golden dataset

Real changes drawn from Plandata.dk history, hand-labelled against
[`docs/rules.md`](../../docs/rules.md).

**34 cases**, in [`cases.jsonl`](cases.jsonl), one JSON object per line.

**Labelled before the agent was built.** This ordering is the point: a dataset
written after the system exists tends to encode what the system already does
rather than what it should do. These labels are a standard the agent can fail
against, not a description of its behaviour.

## What a case records

| Field | Meaning |
|---|---|
| `id` | stable case identifier |
| `label` | `file` / `escalate` / `ignore` — the correct outcome |
| `reason` | one line, why that is the correct outcome |
| `rule` | which rule in `docs/rules.md` decides it |
| `rule_set` | which rule set it was judged against |
| `origin` | `hand-labelled`, or `escalation-derived` once the running system contributes cases |
| `source` | municipality, plan, sub-area, and a link to the source document |
| `before` / `after` | the two versions, with all five watched fields |
| `changed_fields` | just what differed |

`rule_set` exists because rule sets are documents, and more than one can be
evaluated against the same change. `origin` distinguishes cases written up front
from cases the system earned by getting something wrong.

## Composition

| Label | Cases |
|---|---|
| file | 16 |
| escalate | 12 |
| ignore | 6 |

Every rule R1–R7 has at least two cases:

| Rule | Cases | What it covers |
|---|---|---|
| R1 | 14 | permitted use changed |
| R2 | 2 | dimensional limit changed |
| R3 | 6 | field populated for the first time |
| R4 | 5 | limit disappeared from the register |
| R5 | 3 | physically impossible record |
| R6 | 2 | built percentage fell to zero |
| R7 | 2 | mixed additions and removals |

The set is deliberately weighted toward hard cases. Cases the system gets
obviously right teach nothing, so the proportion here does not match the
proportion in the wild — R3-style noise is far more common in the register than
these six cases suggest.

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

# Golden dataset

Real changes drawn from Plandata.dk history, labelled against `rules.md` as
*should file* / *should escalate*.

One JSON object per line in `cases.jsonl`.

## What a case records

| Field | Meaning |
|---|---|
| `id` | stable case identifier |
| `label` | `file` / `escalate` — the correct outcome |
| `reason` | one line, why that is the correct outcome |
| `rule` | which rule decides it; `null` on escalation-derived cases |
| `escalated_rule` | escalation-derived cases only: the rule that escalated the case to a person |
| `rule_set` | which rule set it was judged against |
| `origin` | `hand-labelled`, `rule-derived-v2`, or `escalation-derived` |
| `source` | municipality, plan, sub-area, and a link to the source document |
| `before` / `after` | the two versions, with all watched fields |
| `changed_fields` | just what differed |

`ignore` is not a possible label: the rule engine only ever files or escalates.

## Growing the set

Every real failure becomes a case here. When the running system gets
something wrong, the case that caught it is added with `origin` set to
`escalation-derived`.

## The synthetic NOT_COVERED set

`not_covered_cases.jsonl`, in this same directory, is a separate file, never
merged into `cases.jsonl`. It gives the `assess()` LLM step eval coverage for
cases with no watched field changed.

# The LLM judge: its configuration, written down verbatim

The judge is a **Langfuse-configured evaluator**. It is not in this
repository's code, it cannot be reviewed in a pull request, it cannot be
unit-tested, and it will not exist if this project is recreated in a fresh
Langfuse workspace.

That is the single most fragile arrangement in this system, and this file is
the only mitigation available: the configuration is transcribed here exactly,
so it can be recreated by hand and so a reader can see what it actually says
rather than being told it exists.

`src/tilsynsagent/obs/judge.py` does **not** contain the judge. It measures
the judge's agreement with human labels, which is a different and smaller job.

## Why there is a judge at all

Three failure classes, and each layer catches what the others structurally
cannot:

| Failure | Caught by | Why not the others |
|---|---|---|
| Quotes a clause not in the document | Code scorer (`obs/grounded.py`) | Purely mechanical |
| **Quotes a real clause that doesn't support the conclusion** | **The judge** | **Needs reading and reasoning** |
| Register and document genuinely disagree | A human | Needs authority over what is true |

The code scorers can prove a quote is real, verbatim, and mentions the right
vocabulary. They cannot read a clause about maximum height and tell you it
does not settle a question about permitted use. That requires reading, which
is what the judge is for.

## The surviving rule

**A judge may never decide an outcome, nor be the only thing between a
decision and a reader.**

The judge's score routes nothing. It flags a grounded decision for human
attention. Its agreement with human labels is measured every night and
committed to `docs/evals/judge-agreement-<date>.md`, which is what makes it
*evaluated rather than trusted* — a model's verdict is not a citation, and
"the judge said so" would be exactly the untraceable decision this project's
one rule forbids.

## Configuration, verbatim

Recreate in Langfuse under **Evaluators → New evaluator**.

| Field | Value |
|---|---|
| Name | `judge_clause_supports_conclusion` |
| Scope | Traces |
| Filter | tag equals `grounded` |
| Sampling | 100% |
| Model | `openai/gpt-oss-120b` (Groq) |
| Temperature | 0 |
| Score name | `judge_clause_supports_conclusion` |
| Score data type | NUMERIC (0 or 1) |

### Variable mapping

| Variable | Source |
|---|---|
| `changed_fields` | Trace input → `changed_fields` |
| `clause_id` | Trace output → `grounded_clause_id` |
| `clause_quote` | Trace output → `grounded_clause_quote` |
| `outcome` | Trace output → `grounded_outcome` |
| `reasoning` | Trace output → `grounded_reasoning` |

### Prompt

```
You are checking one decision made by an automated agent that reads Danish
local plan (lokalplan) documents.

The agent was told that these fields changed in the plan register:
{{changed_fields}}

It quoted this clause from the plan document:
Clause {{clause_id}}: "{{clause_quote}}"

From that clause it concluded: {{outcome}}
Its reasoning: {{reasoning}}

Your question is narrow, and you must not widen it.

Does the quoted clause actually support the conclusion drawn from it?

You are NOT asked whether the conclusion is correct in the world, whether the
quote is real, or whether the clause is well chosen. Assume the quote is
accurate. Judge only whether a careful reader, given that clause and only that
clause, could reasonably reach that conclusion about that change.

Answer 0 if the clause is about a different subject than the changed field, if
it says nothing that bears on whether a reader would see something new, or if
the conclusion requires a fact the clause does not contain.

Answer 1 if the clause genuinely settles the question, even briefly.

If you are unsure, answer 0. A decision flagged for human review costs five
minutes. A wrong decision passed through reaches a reader.
```

### Reasoning recorded

Enable **"Include reasoning"** so the judge's own explanation lands on the
score. Without it a flagged decision says only "0", which a person reviewing
the queue cannot act on any faster than reading the document themselves.

## How it is measured

`tilsynsagent nightly` pairs each judge score with the verdict a person left
on the same trace in the annotation queue, and writes
`docs/evals/judge-agreement-<date>.md`.

The report separates the two directions of disagreement and never averages
them:

- **`judge_false_flag`** — judge flagged it, human said it was fine. Costs
  attention. The cheap direction.
- **`judge_false_pass`** — judge passed it, human said it was wrong. **The
  number that matters.** A judge that over-flags is annoying; a judge that
  under-flags is the thing standing between a bad decision and a reader, not
  doing its job.

Agreement is computed in the nightly rather than at decision time because
human labels arrive days after the judge scores they are about. Measuring
inline would measure an empty set and report a confident figure about nothing.

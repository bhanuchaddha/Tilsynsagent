# Tilsynsagent

*Tilsyn* is Danish for supervision or oversight.

A demo agent showing how an AI agent in production should look: the
reliability layer around the agent, not just the agent itself.

An agent that watches public Danish regulatory and municipal publications,
detects what changed, and answers one question against a written rule set:
does the reader now see something on land they care about that they could
not see before? It then either files that with a structured summary or
escalates it to a human with its reasoning attached.

It runs unattended on a schedule, uses tools to do its work, makes
autonomous decisions, and knows when to escalate instead of guessing.

## The one rule

> Every autonomous decision must be traceable to the source that justified
> it, and the agent must escalate rather than guess when the rule set does
> not cover the case.

## How it works

```
  ┌──────────┐   ┌────────────────┐   ┌─────────┐   ┌────────┐
  │  fetch   │──▶│ detect change  │──▶│ extract │──▶│ decide │
  └──────────┘   └────────────────┘   └─────────┘   └───┬────┘
    public                                              │
    Danish                            ┌─────────────────┴──────┐
    sources                           │                        │
                                      ▼                        ▼
                                 ┌─────────┐            ┌────────────┐
                                 │  file   │            │  escalate  │
                                 │ + cited │            │ + reasoning│
                                 │  source │            │  attached  │
                                 └─────────┘            └────────────┘
```

Every step is traced. Any run can be opened and read back decision by
decision, with tokens and cost attached.

## Reliability layer

- **Golden dataset** — real changes from real sources, labelled against a
  written rule set as *should file* / *should escalate*, each with a reason.
- **Tracing** — every run, tool call, decision, and token in Langfuse Cloud.
- **Evals in CI** — the golden dataset runs as an eval suite; a pull request
  that lowers the score does not merge.
- **Decision-level checks** — verified in code, not by a model judge:
  did the cited source actually contain the change it was cited for, and
  did the agent escalate when the rule set was silent.
- **Versioned prompts** — prompts and config live in a registry separate
  from the code deploy, so a prompt can be rolled back without shipping code.
- **Cost per run** — published, not estimated.

## Sources

Public Danish regulatory and municipal publications. Nothing private,
confidential, or access-restricted is used at any point.

## Stack

| Layer | Choice |
|---|---|
| Orchestration | LangGraph, with `interrupt()` / `Command(resume=...)` for human-in-the-loop escalation |
| LLM | Groq, `openai/gpt-oss-120b` |
| Database | Postgres on Neon |
| Observability / evals | Langfuse Cloud |

## Running it

```bash
uv sync
cp .env.example .env   # fill in GROQ_API_KEY and DATABASE_URL at minimum
uv run pytest          # unit + integration tests against mocks
uv run tilsynsagent    # runs the agent end to end
```

Configuration is env-only. No keys or connection strings are tracked in
this repo.

## Licence

MIT. See [LICENSE](LICENSE).

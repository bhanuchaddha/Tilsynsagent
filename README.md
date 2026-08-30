# Tilsynsagent

*Tilsyn* is Danish for supervision or oversight.

**This is a demo/example project, not a production system.** It exists to
show, concretely, what it takes to run an AI agent in production: an
escalation path, a hand-labelled golden dataset, traced runs, evals gating CI,
versioned prompts, and a rollback that has actually been executed — not just
the agent itself. It is built and written up for that purpose; treat it as a
worked example and reference architecture, not as a live regulatory tool.

An agent that watches public Danish regulatory and municipal publications,
detects what changed, and answers one question against a written rule set:
does the reader now see something on land they care about that they could not
see before? It then either files that with a structured summary or escalates
it to a human with its reasoning attached.

It runs unattended on a schedule, uses tools to do its work, makes autonomous
decisions, and — the part that took the most effort — knows when not to.

**Status:** in development. See [Current state](#current-state) for what
actually runs today.

---

## The problem this is about

Building an agent that does multi-step work with tools is, in 2026, not
difficult. Plenty of them exist. The difficulty is answering the questions that
follow:

- When it made that decision, what exactly did it base it on?
- How do you know it is not getting worse than it was last month?
- What happens when it hits a case nobody wrote a rule for?
- What did that run cost?
- A prompt was edited and quality dropped. How long to get back?

Most agent projects cannot answer these. This one is built to answer all five,
and the agent itself is mostly a vehicle for demonstrating that.

## The one rule

> **Every autonomous decision must be traceable to the source that justified it,
> and the agent must escalate rather than guess when the rule set does not cover
> the case.**

Everything else in the design follows from this. An agent that produces a
confident answer it cannot ground, or that acts on a case its rules do not
cover, is failing even when its output happens to be right.

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

The escalation branch was built at the same time as the filing branch, not added
after the first incident. That ordering is deliberate: an escalation path
retrofitted onto a system that was designed to always produce an answer tends to
be one that never fires.

Every step above is traced. Any single run can be opened and read back
decision by decision, with the tokens and cost attached.

## Reliability layer

This is the actual substance of the project.

**Golden dataset.** Real changes from real sources, labelled against a written
rule set as *should file / should escalate*, each with a one-line reason.
Written before the agent was, so the definition of correct was not quietly
shaped by whatever the system happened to do. `ignore` is not a label the rule
set can reach on its own - see [`docs/rules.md`](docs/rules.md) on why.

**Tracing.** Every run, every tool call, every decision, every token, in
Langfuse Cloud. Free tier retains data 30 days, which is why anything that
needs to outlive that - a baseline, a regression, an incident - gets
committed into [`docs/evals/`](docs/evals/) at the moment it happens, not
just linked to. Langfuse is MIT-licensed and self-hostable if the free tier
is outgrown, which is also the path to EU data residency for a Danish
deployment; see `CLAUDE.md`'s stack table for the reasoning.

**Evals in CI.** The golden dataset runs as an eval suite. A pull request that
lowers the score does not merge.

**Decision-level checks.** Separate from any model-as-judge scoring, verified in
code: did the cited source actually contain the change it was cited for? Did the
agent escalate when the rule set was silent?

**Versioned prompts.** Prompts and config live in a registry separate from the
code deploy, so a prompt can be rolled back without shipping code.

**Cost per run.** Published, not estimated.

## Sources

Public Danish regulatory and municipal publications. Everything the agent reads
is publicly available; nothing private, confidential, or access-restricted is
used at any point.

Specific sources are listed in [`docs/sources.md`](docs/sources.md) once each has
been verified fetchable with a live call.

## Current state

This section describes what is actually working, not what is planned.

- [x] Rule set written ([`docs/rules.md`](docs/rules.md)), golden dataset labelled
      ([`evals/golden/cases.jsonl`](evals/golden/cases.jsonl))
- [x] Agent runs end to end, files or escalates unattended (LangGraph graph,
      rule engine, LLM assess/summarise, Postgres-backed escalation via
      `interrupt()`)
- [x] Runs traced, two dated baselines recorded ([`docs/evals/`](docs/evals/)) —
      one per rule-set version, v1 and v2
- [ ] Evals gating CI
- [ ] Prompt registry, rollback executed and timed
- [ ] Public status page

Phase 1 (the agent itself, deterministic rule engine, escalation path) and
Phase 2 (tracing, evals, baseline measurement) are built and tested. CI gating
and Phase 3 (prompt registry, rollback) are not yet built — see
[`docs/architecture.md`](docs/architecture.md) for what exists today and what
is explicitly deferred.

## Running it

```bash
uv sync
cp .env.example .env   # fill in GROQ_API_KEY and DATABASE_URL at minimum
uv run pytest          # unit + integration tests against mocks
uv run tilsynsagent    # runs the agent end to end
```

Configuration is env-only — copy `.env.example` to `.env` and fill it in. No
keys or connection strings are tracked in this repo.

## Why this exists

This project is a teaching artifact for how to build and reason about an AI
agent meant to run unattended: what makes its decisions trustworthy, how to
know when it's getting worse, and what to do when it hits a case nobody wrote
a rule for. The domain (Danish municipal planning registers) was chosen
because it has real public data and genuine ambiguity, not because the author
has a stake in Danish planning law.

## Licence

MIT. See [LICENSE](LICENSE).

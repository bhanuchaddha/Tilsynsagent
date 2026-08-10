# Tilsynsagent

*Tilsyn* is Danish for supervision or oversight.

An agent that watches public Danish regulatory and municipal publications,
detects what changed, decides whether each change matters against a written rule
set, and then either files it with a structured summary or escalates it to a
human with its reasoning attached.

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

**Golden dataset.** Real changes from real sources, hand-labelled against a
written rule set as *should file / should escalate / should ignore*, each with a
one-line reason. Written before the agent was, so the definition of correct was
not quietly shaped by whatever the system happened to do.

**Tracing.** Every run, every tool call, every decision, every token, in
self-hosted Langfuse. Self-hosted specifically so the data stays where it should
for EU deployment.

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

Nothing runs yet. This section gets updated as pieces land, and it describes what
is actually working — not what is planned.

- [ ] Rule set written, golden dataset labelled
- [ ] Agent runs end to end, files or escalates unattended
- [ ] Runs traced, baseline eval score recorded
- [ ] Evals gating CI
- [ ] Prompt registry, rollback executed and timed
- [ ] Public status page

## Running it

Setup instructions land with the first working version.

Configuration is env-only — copy `.env.example` to `.env` and fill it in. No
keys or connection strings are tracked in this repo.

## Licence

MIT. See [LICENSE](LICENSE).

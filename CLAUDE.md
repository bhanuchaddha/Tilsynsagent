# Tilsynsagent

*Tilsyn* is Danish for supervision or oversight.

**Demo/example project.** Built to explain, concretely, how to develop and
operate an AI agent for production use — the reliability layer, not just the
agent — and to serve as reference material for that explanation. It is not a
live regulatory tool.

An agent that watches public Danish regulatory and municipal publications,
detects what changed, and answers one question against a written rule set:
does the reader now see something on land they care about that they could not
see before? It then either files that with a structured summary or escalates
it to a human with its reasoning attached.

The interesting part is not the agent. It is the reliability layer around it: a
hand-labelled golden dataset, traced runs, evals gating CI, versioned prompts,
and a rollback that has actually been executed.

## The one rule

**Every autonomous decision must be traceable to the source that justified it,
and the agent must escalate rather than guess when the rule set does not cover
the case.**

Any change that makes a decision untraceable, or that lets the agent act on an
uncovered case instead of escalating, is a regression — regardless of how much
it improves any other number.

## Working rules

1. **Nothing is done until it is demonstrable.** "The code is written" is not
   done. Done means it can be run and shown.
2. **The escalation path and the golden dataset come first, not last.** They are
   what systems like this normally skip.
3. **Every real failure becomes a test case.** A fix is not complete until the
   case that caught it is in the eval suite.
4. **Public sources only.** Nothing private, confidential, or access-restricted
   enters this repo or the running system.
5. **Secrets are env-only.** `.env.example` is committed, `.env` is not.
6. **Baselines get recorded, however bad.** Do not tune before recording the
   first number.
7. **Every feature ships with demo data and a reset.** Demonstrable on demand,
   without waiting for the live register to produce the right kind of change.
   Demo data is marked as such in the database and a reset clears it without
   touching live run history.

## Verification

No external source becomes a dependency until one real call against that exact
capability has succeeded. Documented behaviour is not evidence — rate limits,
encoding, structure drift, and access policy only surface on a live call.

Integrations are exercised against the real source before they are considered
working. Unit tests use mocks, and mocks agree with whatever assumption produced
them.

## Documentation

Docs describe what the system does and how its correctness is established, in
plain language, for a reader who has not seen the code. State what is built and
what is not.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph 1.0 | `interrupt()` / `Command(resume=...)` / `PostgresSaver` for human-in-the-loop escalation - the same primitives production systems (e.g. Klarna's support agent) use. Considered and rejected Agno for a better raw feature match, but the framework choice has to be explained in every conversation this project exists to support. |
| LLM | Groq, `openai/gpt-oss-120b`, behind `GROQ_MODEL` | Free tier: 1,000 requests/day, versus Gemini's ~20/day, which had shaped several compromises in the original plan. Strict structured output (JSON schema, `strict: true`) verified live before anything depended on it. |
| Database | Postgres on Neon | The register is relational in substance even though the source is a flat WFS feed. Chosen over Supabase because Supabase pauses free projects after a week idle and requires a manual unpause; Neon auto-suspends but wakes on connection - required for a system meant to run unattended and be live on demand. |
| Observability / evals | Langfuse Cloud (Phase 2+) | Free tier covers evals, datasets, and prompt versioning without self-hosting ClickHouse/Redis/S3. 30-day retention means anything that must outlive that gets committed into `docs/` at the moment it happens, not linked to. |

See [`docs/architecture.md`](docs/architecture.md) for the code/LLM decision
split and the full request flow.

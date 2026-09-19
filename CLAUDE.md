# Tilsynsagent

Demo agent showing how an AI agent in production should look — the
reliability layer around the agent, not just the agent itself.

## Working rules

1. Nothing is done until it is demonstrable: runnable and showable, not just written.
2. Public sources only. Nothing private, confidential, or access-restricted enters this repo.
3. Secrets are env-only. `.env.example` is committed, `.env` is not.
4. Every feature ships with demo data and a reset that clears it without touching live run history.

## Verification

Integrations are exercised against the real source before they are
considered working. Unit tests use mocks.

See [README.md](README.md) for what the system does and its stack.

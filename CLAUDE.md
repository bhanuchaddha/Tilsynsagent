# Tilsynsagent

*Tilsyn* is Danish for supervision or oversight.

An agent that watches public Danish regulatory and municipal publications,
detects what changed, decides whether each change matters against a written
rule set, and then either files it with a structured summary or escalates it to
a human with its reasoning attached.

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

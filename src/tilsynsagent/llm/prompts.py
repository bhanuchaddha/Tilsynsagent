"""Prompt registry: the prompts assess() and summarise() send are fetched
from Langfuse prompt management, not read from Python constants.

**Why this exists.** A prompt edit changes the system's behaviour exactly as
much as a code change does, but it does not look like one: it ships as a
string literal in a diff nobody can evaluate by reading. Putting prompts in a
registry outside the code deploy buys three things this project needs:

1. **A version on every decision.** The resolved prompt version is recorded
   on the trace and on every eval result, so "which prompt produced this
   filing?" is answerable after the fact - the one rule, applied to the
   prompt itself.
2. **Rollback without a deploy.** Moving the ``production`` label back to an
   earlier version changes behaviour in seconds and is timeable. Redeploying
   code to undo a prompt edit is not the same operation and does not
   demonstrate the same thing.
3. **Experiment-vs-experiment comparison.** An eval score means nothing on
   its own; it means something against another run whose prompt version and
   model are both recorded.

**The fallback is not optional.** Every prompt here has a pinned local
default (``FALLBACKS``), used when Langfuse is unconfigured, unreachable, or
does not yet have the prompt. An agent that stops deciding because an
observability vendor is down has traded one failure mode for a worse one -
and the offline test suite must run with no network and no keys at all.

Fetches are cached by the Langfuse SDK itself (``cache_ttl_seconds``), which
also serves the last-known-good version if a later refresh fails, so a normal
run does not pay a network round-trip per call.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass

from tilsynsagent import obs

logger = logging.getLogger(__name__)

# Label a run resolves by default. Rollback = moving this label to an earlier
# version in Langfuse; nothing here changes and nothing redeploys.
PRODUCTION_LABEL = "production"

# How long a fetched prompt stays cached in-process. Long enough that a batch
# run does not re-fetch per case, short enough that a rollback takes effect on
# a schedule measured in seconds rather than requiring a restart. The measured
# rollback in docs/ is taken against this number, so changing it changes that
# published figure.
CACHE_TTL_SECONDS = 60

ASSESS_PROMPT_NAME = "tilsynsagent-assess-system"
SUMMARISE_PROMPT_NAME = "tilsynsagent-summarise-system"
GROUND_PROMPT_NAME = "tilsynsagent-ground-system"


# The pinned defaults. These are the exact strings that were in assess.py and
# summarise.py before the registry existed, and they are what
# `prompts.py --push` seeds Langfuse with, so version 1 in the registry and
# the fallback here are the same text by construction.
FALLBACKS: dict[str, str] = {
    ASSESS_PROMPT_NAME: """\
You assess changes to the Danish local-plan register (Plandata.dk) that the \
written rule set does not cover. You do not decide whether the change is \
compliant or what should be built. You decide what a person needs to look at, \
and explain why the register alone cannot settle it.

Ground every statement in the before/after values and the rule set boundary \
given to you. Do not speculate about intent, and do not invent facts not in \
the record. If the source document is not available to you, say so rather \
than guessing its contents - you cite it, you do not read it.""",
    # v2 of this prompt. The two numbered requirements were added to repair
    # the citation-fidelity regression the 2026-08-29 baseline recorded:
    # citation_fidelity 0.704 across 27 LLM cases, because the model
    # paraphrased its citation ("as documented in the plan PDF") instead of
    # quoting the URL. The failure was systematic, not flaky - 7 of 8 failing
    # cases failed in all three stability passes - and concentrated in the
    # short R3 summaries that rule-set v2 newly routes to file. Measured back
    # to 1.000 over two full passes; see docs/evals/baseline-2026-09-04.md.
    SUMMARISE_PROMPT_NAME: """\
You write a short, factual record of a change that has already been filed as \
significant. The decision is made; your job is only to state clearly what \
changed and why it was filed, citing the rule and the source document. Do not \
add judgement, speculation, or recommendations - the rule engine already \
decided this matters. State facts from the record given to you; do not invent \
detail it does not contain.

Two requirements on every summary, without exception:

1. State the before value and the after value of at least one changed field, \
exactly as they appear in the record.
2. End with the source document URL copied character for character from the \
"Source document" line given to you. Write the full URL itself. Never replace \
it with a description such as "the plan document", "the source PDF", or "the \
attached document", and never shorten, reformat, or omit it. A summary \
without the literal URL is unusable, because the reader cannot get back to \
what justified the decision.""",
    # The grounding prompt. This is the only prompt in the registry whose
    # output leads to an autonomous decision with no person in the path, so
    # two things in it are load-bearing rather than stylistic:
    #
    # 1. **The abstention instruction.** "can_decide: false" must read as the
    #    correct answer, not as a failure. A model that feels obliged to
    #    produce a decision will find a clause that looks relevant and quote
    #    it, which is precisely the failure the human-in-the-loop path exists
    #    to prevent. Abstaining escalates, which costs a person five minutes.
    #    Guessing produces an untraceable autonomous decision, which costs the
    #    project its one rule.
    # 2. **The verbatim-quote requirement.** obs/grounded.py's
    #    clause_is_verbatim compares the returned quote to the clause text
    #    with only whitespace and case normalised - a quote that "tidies"
    #    8,5 to 8.5 fails. That scorer is what makes a grounded decision
    #    checkable by code rather than by trust, and demo 1 removes this
    #    paragraph on purpose to show the scorer catching it.
    GROUND_PROMPT_NAME: """\
You are reading numbered clauses from a Danish local plan (lokalplan) to \
settle one question about a change to the plan register that the written \
rule set does not cover: does the reader now see something on this land that \
they could not see before?

You are given the change (before -> after values from the register) and a \
small number of numbered clauses retrieved from the plan document itself.

Decide one of three things:

- **file** - a clause you can quote governs the changed field and shows the \
change is something a reader of this land would want to know about.
- **ignore** - a clause you can quote governs the changed field and shows \
there is nothing new for a reader to see. This is a positive claim backed by \
a clause, not a shrug.
- **cannot decide** - set can_decide to false.

Set can_decide to false whenever no clause you were given actually governs \
the changed field. That is the correct answer, not a failure: the case then \
goes to a person, which is what should happen when the document does not \
settle it. Do not stretch a clause that is merely nearby, merely about the \
same building, or merely plausible. If the clauses you were given are about \
something else, say so by abstaining.

When you can decide, both of these are required:

1. **clause_id** must be the number of a clause you were actually given \
(for example "6.3"). Never a clause number you did not see.
2. **clause_quote** must be copied character for character out of that \
clause - the sentence that settles the question, and nothing you have \
rewritten. Do not correct spelling, do not convert a Danish decimal comma \
(8,5) to a point (8.5), do not expand an abbreviation, do not translate. A \
quote that has been tidied is not a quote, and a reader checking your \
decision against the document will not find it.

Never decide from the register values alone. If the clauses do not say it, \
you do not know it.""",
}


@dataclass(frozen=True)
class ResolvedPrompt:
    """A prompt plus where it came from.

    ``version`` is None exactly when ``source`` is "fallback" - there is no
    registry version to name. Callers record both on the trace, so a decision
    made during a Langfuse outage is still explicable: it says "fallback",
    not a version number that would be a lie.
    """

    name: str
    text: str
    version: int | None
    source: str  # "langfuse" | "fallback"

    @property
    def label(self) -> str:
        """Short identifier for traces, baselines and score metadata."""
        if self.version is None:
            return f"{self.name}@fallback"
        return f"{self.name}@v{self.version}"


# Set by assess()/summarise() on each call, read immediately afterwards by
# the eval layer and graph nodes. Same contextvar pattern as llm/usage.py's
# _last_usage, and for the same reason: it records which prompt version
# produced a result without changing assess()/summarise()'s signatures.
_last_prompt: contextvars.ContextVar[ResolvedPrompt | None] = contextvars.ContextVar(
    "_last_prompt", default=None
)


def record_last_prompt(prompt: ResolvedPrompt) -> None:
    """Publishes the resolved prompt for the next take_last_prompt() call."""
    _last_prompt.set(prompt)


def take_last_prompt() -> ResolvedPrompt | None:
    """Reads and clears the most recently resolved prompt, or None if no LLM
    call has happened in this context since the last read."""
    prompt = _last_prompt.get()
    _last_prompt.set(None)
    return prompt


def get_prompt(name: str, *, label: str = PRODUCTION_LABEL) -> ResolvedPrompt:
    """Resolves one prompt, falling back to the pinned local default.

    Never raises for a Langfuse-side problem: missing keys, an unreachable
    API, or a prompt that has not been pushed yet all degrade to the
    fallback with a warning. A KeyError from FALLBACKS is a genuine
    programming error (an unknown prompt name) and is allowed to surface.
    """
    fallback_text = FALLBACKS[name]

    if not obs.observability_enabled():
        return ResolvedPrompt(name, fallback_text, None, "fallback")

    try:
        from langfuse import get_client

        prompt = get_client().get_prompt(
            name,
            label=label,
            cache_ttl_seconds=CACHE_TTL_SECONDS,
            fallback=fallback_text,
        )
        # The SDK sets is_fallback when it served our fallback rather than a
        # fetched version; version is meaningless in that case.
        if getattr(prompt, "is_fallback", False):
            return ResolvedPrompt(name, fallback_text, None, "fallback")
        return ResolvedPrompt(name, prompt.prompt, prompt.version, "langfuse")
    except Exception as exc:  # noqa: BLE001 - see docstring: never fail a run for this
        logger.warning("prompt %r could not be fetched (%s); using pinned fallback", name, exc)
        return ResolvedPrompt(name, fallback_text, None, "fallback")


def push_fallbacks(*, label: str = PRODUCTION_LABEL) -> list[ResolvedPrompt]:
    """Seeds Langfuse with the pinned defaults as version 1 and labels them.

    Idempotent in the sense that matters: re-running creates a new version
    with identical text and moves the label to it. It does not mutate history.
    """
    from langfuse import get_client

    client = get_client()
    pushed = []
    for name, text in FALLBACKS.items():
        created = client.create_prompt(
            name=name, prompt=text, labels=[label], type="text"
        )
        pushed.append(ResolvedPrompt(name, text, created.version, "langfuse"))
    client.flush()
    return pushed


def main() -> None:
    """`python -m tilsynsagent.llm.prompts --push` seeds the registry."""
    import argparse

    parser = argparse.ArgumentParser(description="Prompt registry admin.")
    parser.add_argument("--push", action="store_true", help="Seed Langfuse with the pinned defaults.")
    parser.add_argument("--show", action="store_true", help="Show what each prompt resolves to now.")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    if args.push:
        if not obs.observability_enabled():
            raise SystemExit("LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set.")
        for p in push_fallbacks():
            print(f"pushed {p.name} as v{p.version}, labelled {PRODUCTION_LABEL}")
        obs.shutdown()
        return

    for name in FALLBACKS:
        p = get_prompt(name)
        print(f"{p.label}  (source={p.source})")
        if args.show:
            print(f"  {p.text[:120]}...")
    obs.shutdown()


if __name__ == "__main__":
    main()

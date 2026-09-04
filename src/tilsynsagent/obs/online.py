"""Online scoring: judging live decisions where no golden case exists.

**Why this is a different thing from the eval suite.** `evals/` measures the
system against 39 hand-labelled cases whose correct answer is known. That
catches regressions in *changes*. It cannot catch drift in *inputs* - the
register keeps producing new shapes of transition, and none of them are in
the golden set. A system that scores 1.000 offline can still be steadily
degrading in production, and nobody would know until someone downstream
noticed.

So every scorer here works with **no expected output at all**. Each one
checks a property of the decision against the record the agent itself saw:

- ``cited_source_present`` - a filed decision cites the document the register
  gave for that record. The one rule, checked on live traffic rather than on
  a test set.
- ``escalated_when_uncovered`` - a case the rule set did not cover escalated
  rather than being acted on. This is the rule the project exists to hold,
  and it is checkable without knowing the right answer, because the property
  is structural.
- ``stayed_in_tool_surface`` - the run wrote only what its outcome permits. A
  filing must not also produce an escalation row, and vice versa.

**Sampling.** Every escalation is scored: escalations are rare, each one is a
call for human attention, and one bad escalation costs more than a hundred
routine filings. Filings are sampled (``FILING_SAMPLE_RATE``), because they
are the common case and scoring all of them buys precision nobody needs at a
cost in Langfuse quota that is not free.

Scores are attached to the run's own trace, so a low score is one click from
the decision that produced it. Everything here is a no-op when Langfuse is
unconfigured, exactly like the rest of ``obs/``.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from tilsynsagent.obs.langfuse_setup import observability_enabled

logger = logging.getLogger(__name__)

# Escalations are always scored - see the module docstring. Filings are the
# high-volume path; this fraction keeps a representative sample without
# spending quota on certainty nobody is asking for.
FILING_SAMPLE_RATE = 0.25


@dataclass(frozen=True)
class OnlineScore:
    name: str
    value: float  # 0.0 or 1.0
    comment: str


def should_score(*, outcome: str, thread_id: str, rate: float = FILING_SAMPLE_RATE) -> bool:
    """Whether this run gets scored.

    Deterministic in the thread id rather than random: the same run always
    makes the same decision, so a re-run (or a resumed escalation) does not
    flip in or out of the sample and produce two different score histories
    for one decision.
    """
    if outcome != "file":
        return True
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    digest = hashlib.sha256(thread_id.encode()).digest()
    # First two bytes as a fraction of 65536 - a stable uniform value in
    # [0, 1) derived from the thread id alone.
    bucket = int.from_bytes(digest[:2], "big") / 65536.0
    return bucket < rate


def cited_source_present(*, outcome: str, citation: str | None, doklink: str | None) -> OnlineScore:
    """A decision cites the source document the register gave for this record.

    Checked against the doklink on the record itself, not against a golden
    value - which is what makes it runnable on live traffic. A record whose
    doklink is absent upstream cannot be cited and is not counted as a
    failure of the agent; that is a source-data gap and is reported as such.
    """
    if not doklink:
        return OnlineScore(
            "cited_source_present", 1.0, "record has no doklink upstream - nothing to cite"
        )
    if not citation:
        return OnlineScore("cited_source_present", 0.0, f"{outcome} decision cited nothing")
    if citation.strip() != doklink.strip():
        return OnlineScore(
            "cited_source_present",
            0.0,
            f"cited {citation!r}, but the record's document is {doklink!r}",
        )
    return OnlineScore("cited_source_present", 1.0, "cites the record's own source document")


def escalated_when_uncovered(*, rule: str | None, outcome: str) -> OnlineScore:
    """A case the rule set did not cover escalated rather than being acted on.

    This is CLAUDE.md's one rule expressed as a property that needs no
    expected answer: when ``rule`` is None the engine returned NOT_COVERED,
    and the only permissible outcome is escalation. A filing with no rule
    behind it is the exact failure this project is built to prevent - the
    agent acting on a case nobody wrote a rule for.
    """
    if rule is not None:
        return OnlineScore(
            "escalated_when_uncovered", 1.0, f"covered by {rule} - rule-decided, not guessed"
        )
    if outcome == "escalate":
        return OnlineScore(
            "escalated_when_uncovered", 1.0, "uncovered case escalated rather than acted on"
        )
    return OnlineScore(
        "escalated_when_uncovered",
        0.0,
        f"uncovered case (no rule fired) resulted in {outcome!r} instead of escalating",
    )


def stayed_in_tool_surface(*, outcome: str, result: dict | None) -> OnlineScore:
    """The run wrote only what its outcome permits.

    A filing writes a filing row; an escalation writes an escalation row; a
    skip writes neither. A run that produced both has done something no code
    path is supposed to allow, and it matters more than a low-quality summary
    because it means the decision architecture itself leaked.
    """
    result = result or {}
    has_filing = result.get("filing_id") is not None
    has_escalation = result.get("escalation_id") is not None

    if has_filing and has_escalation:
        return OnlineScore(
            "stayed_in_tool_surface", 0.0, "run produced both a filing and an escalation"
        )
    if outcome == "file" and not has_filing:
        return OnlineScore("stayed_in_tool_surface", 0.0, "filed outcome wrote no filing row")
    if outcome == "escalate" and not has_escalation:
        return OnlineScore(
            "stayed_in_tool_surface", 0.0, "escalated outcome wrote no escalation row"
        )
    if outcome == "file" and has_escalation:
        return OnlineScore("stayed_in_tool_surface", 0.0, "filed outcome wrote an escalation row")
    if outcome == "escalate" and has_filing:
        return OnlineScore("stayed_in_tool_surface", 0.0, "escalated outcome wrote a filing row")
    return OnlineScore("stayed_in_tool_surface", 1.0, f"{outcome} wrote only what it may")


def score_run(
    *,
    outcome: str,
    rule: str | None,
    citation: str | None,
    doklink: str | None,
    result: dict | None,
) -> list[OnlineScore]:
    """Every online scorer, applied to one completed run's own record."""
    return [
        cited_source_present(outcome=outcome, citation=citation, doklink=doklink),
        escalated_when_uncovered(rule=rule, outcome=outcome),
        stayed_in_tool_surface(outcome=outcome, result=result),
    ]


def record_scores(scores: list[OnlineScore], *, thread_id: str) -> None:
    """Attaches scores to the run's Langfuse trace, by session id.

    A no-op without Langfuse keys, and never raises: a scoring failure must
    not fail a run that has already made and written its decision. Losing a
    score is an observability gap; losing the decision is a real incident.
    """
    if not observability_enabled():
        return
    try:
        from langfuse import get_client

        client = get_client()
        for score in scores:
            client.create_score(
                name=score.name,
                value=score.value,
                comment=score.comment,
                session_id=thread_id,
                data_type="NUMERIC",
            )
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("online scores for thread %s could not be recorded: %s", thread_id, exc)

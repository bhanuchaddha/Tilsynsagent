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

Grounded decisions get four more, in ``obs/grounded.py``, and they carry more
weight than anything here: a grounded decision is the only kind this system
makes that no person reviews, so its scorers are the only check it has. They
are applied by ``_grounded_scores`` below, from the same
``score_completed_run`` entry point, so there is no way to add a grounded run
to the system without also scoring it.

**Sampling.** Every escalation and every grounded decision is scored: escalations are rare, each one is a
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


def should_score(
    *,
    outcome: str,
    thread_id: str,
    rate: float = FILING_SAMPLE_RATE,
    grounded: bool = False,
) -> bool:
    """Whether this run gets scored.

    Deterministic in the thread id rather than random: the same run always
    makes the same decision, so a re-run (or a resumed escalation) does not
    flip in or out of the sample and produce two different score histories
    for one decision.
    """
    if grounded:
        # Never sampled, whatever the outcome. A grounded *file* has
        # outcome="file" and would otherwise fall into the filing sample, so
        # three quarters of the only decisions nobody reviews would carry no
        # check at all - which is the exact opposite of what sampling is for.
        # Sampling exists to spend less on the decisions that are already
        # guaranteed by a rule.
        return True
    if outcome != "file":
        # Escalations are rare and each one costs a person's attention.
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


def escalated_when_uncovered(
    *, rule: str | None, outcome: str, grounded: bool = False
) -> OnlineScore:
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
    if grounded:
        # An uncovered case the agent decided by reading the document. This
        # does not violate the one rule - the decision is traceable to a
        # quoted clause rather than to a rule - but it is only acceptable
        # because the grounded scorers check that clause. Kept as a distinct
        # comment, not folded into the pass above, so a reader of a trace can
        # tell "a rule covered it" from "the document covered it".
        return OnlineScore(
            "escalated_when_uncovered",
            1.0,
            f"uncovered case decided from the source document, not guessed ({outcome})",
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
    has_grounding = result.get("grounding_id") is not None

    # A grounded ignore is the one outcome that legitimately writes no filing
    # and no escalation - its whole record is the groundings row. Judged on
    # that row's presence instead, so "wrote only what it may" still means
    # something for it rather than being skipped.
    if outcome == "ignore":
        if has_escalation:
            return OnlineScore(
                "stayed_in_tool_surface", 0.0, "grounded ignore wrote an escalation row"
            )
        if has_filing:
            return OnlineScore(
                "stayed_in_tool_surface", 0.0, "grounded ignore wrote a filing row"
            )
        if not has_grounding:
            return OnlineScore(
                "stayed_in_tool_surface", 0.0, "ignore outcome wrote no grounding row"
            )
        return OnlineScore(
            "stayed_in_tool_surface", 1.0, "grounded ignore wrote only its grounding row"
        )

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
    grounded: bool = False,
) -> list[OnlineScore]:
    """Every online scorer, applied to one completed run's own record."""
    return [
        cited_source_present(outcome=outcome, citation=citation, doklink=doklink),
        escalated_when_uncovered(rule=rule, outcome=outcome, grounded=grounded),
        stayed_in_tool_surface(outcome=outcome, result=result),
    ]


def score_completed_run(result, *, record, thread_id: str) -> list[OnlineScore]:
    """Scores one finished graph invocation against the record it saw.

    Lives here rather than in runner.py because the demo seed drives the same
    graph and must produce the same scores - a demo that shows tracing but no
    scoring would be showing a different system from the one that runs
    unattended.

    Never raises: a scoring failure must not fail a run that has already made
    and written its decision. Returns the scores it recorded (empty when the
    run was deduplication, or was sampled out) so a caller can log them.
    """
    if not isinstance(result, dict):
        return []

    outcome_result = result.get("result", {}) or {}
    interrupts = result.get("__interrupt__")
    if interrupts:
        # A paused run: _escalate_node writes the escalation row *before* it
        # interrupts, so the decision is real and scoreable even though the
        # graph has not reached END.
        interrupt_value = getattr(interrupts[0], "value", None) or {}
        outcome = "escalate"
        outcome_result = {
            "escalation_id": interrupt_value.get("escalation_id"),
            "diff_id": interrupt_value.get("diff_id"),
        }
        citation = interrupt_value.get("citation")
    elif "grounding_id" in outcome_result:
        # A grounded decision: the agent read the document and acted with no
        # person in the path. Checked *before* filing_id, because a grounded
        # file writes both a grounding and a filing and must be scored as the
        # former - the grounded scorers are the only ones that check the
        # clause evidence, and this is the one population where nothing else
        # will catch a bad decision.
        #
        # This branch is why this function is not a chain of "if filing_id".
        # A new result shape falling through to `return []` would leave
        # exactly the unreviewed decisions silently unscored, which would
        # defeat the whole point of grounding them.
        outcome = result.get("outcome") or "file"
        citation = record.doklink
    elif "filing_id" in outcome_result:
        outcome, citation = "file", record.doklink
    elif "escalation_id" in outcome_result:
        outcome, citation = "escalate", record.doklink
    else:
        # Deduplication, not a decision - see graph.py's _skip_node.
        return []

    if not should_score(
        outcome=outcome, thread_id=thread_id, grounded=bool(result.get("grounded"))
    ):
        return []

    scores = score_run(
        outcome=outcome,
        rule=result.get("rule"),
        citation=citation,
        doklink=record.doklink,
        result=outcome_result,
        grounded=bool(result.get("grounded")),
    )
    scores.extend(_grounded_scores(result))
    record_scores(scores, thread_id=thread_id)
    for score in scores:
        if score.value < 1.0:
            logger.warning(
                "online score %s failed for thread %s: %s", score.name, thread_id, score.comment
            )
    return scores


def _grounded_scores(result: dict) -> list[OnlineScore]:
    """The grounded scorers, for a run that reached the ground node.

    Returns [] for every run that did not - a rule-decided filing has no
    clause to check and would only add hollow 1.0s to the window, which would
    dilute exactly the rates drift.py watches.

    The clause text the quote is checked against is re-read from the cached
    document rather than carried in graph state: state would make the check
    circular (the model's own claim about what it read, verifying itself),
    and the cache makes the re-read nearly free. A cache miss here means the
    quote cannot be verified, which is reported as a failure rather than
    silently skipped.
    """
    if "grounded" not in result:
        return []
    from tilsynsagent.obs.grounded import score_grounded_run

    grounded = bool(result.get("grounded"))
    clause_id = result.get("grounded_clause_id") or ""
    clause_text = ""
    if grounded and clause_id:
        clause_text = _clause_text_for(result, clause_id)

    return score_grounded_run(
        grounded=grounded,
        outcome=result.get("grounded_outcome") or "",
        clause_id=clause_id,
        clause_quote=result.get("grounded_clause_quote") or "",
        clause_text=clause_text,
        retrieved_clause_ids=result.get("retrieved_clause_ids") or [],
        changed_fields=result.get("changed_fields") or {},
    )


def _clause_text_for(result: dict, clause_id: str) -> str:
    """Re-reads the cited clause out of the document the run was given.

    Never raises: a scoring failure must not fail a decision that has already
    been written. An empty return makes clause_is_verbatim fail, which is the
    honest answer - "we could not check this" is much closer to a failure
    than to a pass, and a silent skip would let a whole window of grounded
    decisions go unverified while the rate still read 1.000.
    """
    record = result.get("record") or {}
    doklink = record.get("doklink") if isinstance(record, dict) else None
    if not doklink:
        return ""
    try:
        from tilsynsagent.documents import extract_text, fetch_document, split_clauses

        fetched = fetch_document(doklink)
        if not fetched.ok:
            return ""
        extracted = extract_text(fetched.content)
        if not extracted.ok:
            return ""
        for clause in split_clauses(extracted.text):
            if clause.clause_id == clause_id:
                return clause.text
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("clause text for %s could not be re-read: %s", clause_id, exc)
    return ""


def record_scores(scores: list[OnlineScore], *, thread_id: str) -> None:
    """Attaches scores to the run's Langfuse trace, by session id.

    A no-op without Langfuse keys, and never raises: a scoring failure must
    not fail a run that has already made and written its decision. Losing a
    score is an observability gap; losing the decision is a real alert.
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

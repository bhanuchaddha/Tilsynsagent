"""Online scorers and drift detection, offline. No LLM, no Langfuse.

These judge live decisions where no golden case exists, so their failure mode
is the quiet one: a scorer that always returns 1.0 looks exactly like a
healthy system.
"""

from __future__ import annotations

from tilsynsagent.obs.drift import (
    DEGRADED,
    MIN_WINDOW,
    ScorerWindow,
    evaluate_window,
)
from tilsynsagent.obs.online import (
    cited_source_present,
    escalated_when_uncovered,
    score_run,
    should_score,
    stayed_in_tool_surface,
)

DOC = "https://dokument.plandata.dk/20_1234_abc.pdf"


# --- cited_source_present ------------------------------------------------


def test_citation_matching_the_record_passes():
    assert cited_source_present(outcome="file", citation=DOC, doklink=DOC).value == 1.0


def test_citing_nothing_fails():
    score = cited_source_present(outcome="file", citation=None, doklink=DOC)
    assert score.value == 0.0


def test_citing_a_different_document_fails():
    """The one rule is traceability to *the source that justified it* - a
    real-looking URL that is not this record's document is worse than no
    citation, because it reads as evidence."""
    score = cited_source_present(
        outcome="file", citation="https://dokument.plandata.dk/other.pdf", doklink=DOC
    )
    assert score.value == 0.0


def test_missing_doklink_upstream_is_not_the_agents_failure():
    """A record the register gave no document for cannot be cited. Scoring
    that as an agent failure would bury real citation failures under a
    source-data gap."""
    score = cited_source_present(outcome="file", citation=None, doklink=None)
    assert score.value == 1.0
    assert "upstream" in score.comment


# --- escalated_when_uncovered --------------------------------------------


def test_uncovered_case_that_escalates_passes():
    assert escalated_when_uncovered(rule=None, outcome="escalate").value == 1.0


def test_uncovered_case_that_files_fails():
    """The single most important check in this module: the agent acting on a
    case nobody wrote a rule for is the failure the whole project exists to
    prevent (CLAUDE.md's one rule)."""
    score = escalated_when_uncovered(rule=None, outcome="file")
    assert score.value == 0.0
    assert "instead of escalating" in score.comment


def test_rule_decided_case_passes_whatever_it_decided():
    assert escalated_when_uncovered(rule="R2", outcome="file").value == 1.0
    assert escalated_when_uncovered(rule="R4", outcome="escalate").value == 1.0


# --- stayed_in_tool_surface ----------------------------------------------


def test_filing_that_wrote_a_filing_passes():
    assert stayed_in_tool_surface(outcome="file", result={"filing_id": 1}).value == 1.0


def test_run_that_wrote_both_a_filing_and_an_escalation_fails():
    """No code path allows this. If it ever happens the decision
    architecture has leaked, which matters more than any quality score."""
    score = stayed_in_tool_surface(
        outcome="file", result={"filing_id": 1, "escalation_id": 2}
    )
    assert score.value == 0.0


def test_filing_that_wrote_nothing_fails():
    assert stayed_in_tool_surface(outcome="file", result={}).value == 0.0


def test_escalation_that_wrote_a_filing_fails():
    score = stayed_in_tool_surface(outcome="escalate", result={"filing_id": 1})
    assert score.value == 0.0


# --- sampling ------------------------------------------------------------


def test_every_escalation_is_scored():
    """Escalations are rare and each one is a call for human attention. One
    bad escalation costs more than a hundred routine filings, so they are
    never sampled out."""
    assert all(
        should_score(outcome="escalate", thread_id=f"t-{i}", rate=0.01) for i in range(50)
    )


def test_filings_are_sampled():
    sampled = [should_score(outcome="file", thread_id=f"t-{i}", rate=0.25) for i in range(400)]
    assert 0 < sum(sampled) < 400, "a sample rate of 0.25 must neither take all nor none"


def test_sampling_is_stable_for_the_same_run():
    """Deterministic in the thread id, not random: a resumed escalation or a
    re-run must not flip in and out of the sample and produce two different
    score histories for one decision."""
    first = should_score(outcome="file", thread_id="1234-2-v7", rate=0.5)
    for _ in range(20):
        assert should_score(outcome="file", thread_id="1234-2-v7", rate=0.5) is first


# --- score_run -----------------------------------------------------------


def test_score_run_returns_every_ungrounded_scorer():
    scores = score_run(
        outcome="file", rule="R2", citation=DOC, doklink=DOC, result={"filing_id": 1}
    )
    assert {s.name for s in scores} == {
        "cited_source_present",
        "escalated_when_uncovered",
        "stayed_in_tool_surface",
    }


def test_every_threshold_has_a_scorer_that_can_produce_it():
    """No threshold in DEGRADED may name a scorer nothing ever emits.

    This used to be expressible as "score_run emits exactly DEGRADED", and
    that broke when grounded scorers arrived on a second path. The property
    it was protecting is still the one that matters, and is worth more than
    the shape it was written in: a threshold whose scorer never runs sits at
    "not scored in this window" forever, which reads as harmless in the drift
    output and is in fact a check that silently does nothing.
    """
    from tilsynsagent.obs.grounded import score_grounded_run

    ungrounded = {
        s.name
        for s in score_run(
            outcome="file", rule="R2", citation=DOC, doklink=DOC, result={"filing_id": 1}
        )
    }
    grounded = {
        s.name
        for s in score_grounded_run(
            grounded=True,
            outcome="file",
            clause_id="6.3",
            clause_quote="q",
            clause_text="6.3 q",
            retrieved_clause_ids=["6.3"],
            changed_fields={"maxetager": {}},
        )
    }
    assert set(DEGRADED) == ungrounded | grounded


# --- drift ---------------------------------------------------------------


def _healthy(n: int = 30) -> dict[str, ScorerWindow]:
    return {name: ScorerWindow(name, [1.0] * n) for name in DEGRADED}


def test_healthy_window_is_healthy():
    assert evaluate_window(_healthy()).status == "healthy"


def test_a_thin_window_is_inconclusive_not_healthy():
    """Three states, not two. "Not enough decisions to say" reported as
    healthy is how a degradation goes unnoticed during quiet traffic."""
    verdict = evaluate_window(_healthy(MIN_WINDOW - 1))
    assert verdict.status == "insufficient_evidence"
    assert not verdict.is_degraded


def test_citation_drift_trips_the_threshold():
    windows = _healthy()
    windows["cited_source_present"] = ScorerWindow(
        "cited_source_present", [1.0] * 25 + [0.0] * 5
    )
    verdict = evaluate_window(windows)
    assert verdict.is_degraded
    assert verdict.failing_scorers == ["cited_source_present"]


def test_one_structural_failure_trips_immediately():
    """The structural scorers are set to 1.0 on purpose: an uncovered case
    being acted on, or a run writing outside its tool surface, is a bug, not
    a quality drop, so there is no tolerance band to hide in."""
    windows = _healthy()
    windows["escalated_when_uncovered"] = ScorerWindow(
        "escalated_when_uncovered", [1.0] * 29 + [0.0]
    )
    assert evaluate_window(windows).is_degraded


def test_an_empty_window_never_reports_healthy():
    """fetch_windows returns {} when Langfuse is unreachable. An alerting
    path that turns "no evidence" into an all-clear is worse than one that
    crashes, because it is silent."""
    assert evaluate_window({}).status == "insufficient_evidence"

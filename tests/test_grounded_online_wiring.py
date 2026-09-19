"""That a grounded run is never silently unscored.

This is the failure the phase most needed to avoid: obs/online.py infers a
run's outcome from the shape of its result dict, and a new result shape that
fell through would `return []` - leaving exactly the decisions nobody reviews
with no check on them at all, while every rate still read 1.000.
"""

from __future__ import annotations

from tilsynsagent.obs.online import score_completed_run, stayed_in_tool_surface
from tilsynsagent.sources.plandata import SubAreaRecord

DOC = "https://dokument.plandata.dk/20_1_1.pdf"
# The whole document the scorers now check a citation against.
DOCUMENT = (
    "1.1 Lokalplanens formål.\n"
    "6.2 Bebyggelse må ikke opføres med en større højde end 8,5 m.\n"
)

RECORD = SubAreaRecord(
    feature_id="f1",
    lokplan_id=1,
    delnr="1",
    komnr=101,
    kommunenavn="Testby",
    versionsnr=2,
    status="Vedtaget",
    datoopdt="2026-01-01T00:00:00.000Z",
    maxbygnhjd=8.5,
    maxetager=None,
    bebygpct=40.0,
    zonestatus="Byzone",
    anvendelsegenerel="Boligområde",
    doklink=DOC,
)


def _result(**over):
    base = {
        "outcome": "file",
        "rule": None,
        "grounded": True,
        "grounded_outcome": "file",
        "grounded_citation_kind": "clause",
        "grounded_clause_id": "6.2",
        "grounded_clause_quote": "højde end 8,5 m",
        "changed_fields": {},
        "before": {**RECORD.to_dict(), "versionsnr": 1, "status": "Forslag"},
        "after": RECORD.to_dict(),
        "record": RECORD.to_dict(),
        "result": {"diff_id": 1, "grounding_id": 7, "filing_id": 3},
    }
    base.update(over)
    return base


def test_a_grounded_run_is_scored(monkeypatch):
    monkeypatch.setattr(
        "tilsynsagent.obs.online._document_text_for", lambda *a, **k: DOCUMENT
    )
    scores = score_completed_run(_result(), record=RECORD, thread_id="t-1")
    names = {s.name for s in scores}
    assert "clause_id_exists" in names
    assert "clause_is_verbatim" in names
    assert "abstained_when_ungrounded" in names


def test_a_grounded_ignore_is_scored_even_with_no_filing_row(monkeypatch):
    """A grounded ignore writes neither a filing nor an escalation. Under the
    old inference this fell straight through to `return []`."""
    monkeypatch.setattr(
        "tilsynsagent.obs.online._document_text_for", lambda *a, **k: DOCUMENT
    )
    result = _result(
        outcome="ignore",
        grounded_outcome="ignore",
        result={"diff_id": 1, "grounding_id": 7},
    )
    scores = score_completed_run(result, record=RECORD, thread_id="t-2")
    assert scores, "a grounded ignore must not be silently unscored"
    assert all(s.value == 1.0 for s in scores), [s for s in scores if s.value < 1.0]


def test_a_grounded_run_is_never_sampled_out(monkeypatch):
    """Filings are sampled; grounded decisions must not be, or the only check
    they have would be applied to a quarter of them."""
    monkeypatch.setattr(
        "tilsynsagent.obs.online._document_text_for", lambda *a, **k: DOCUMENT
    )
    for i in range(20):
        scores = score_completed_run(
            _result(outcome="ignore", grounded_outcome="ignore",
                    result={"diff_id": i, "grounding_id": i}),
            record=RECORD,
            thread_id=f"thread-{i}",
        )
        assert scores


def test_a_fabricated_clause_is_caught_end_to_end(monkeypatch):
    monkeypatch.setattr(
        "tilsynsagent.obs.online._document_text_for", lambda *a, **k: DOCUMENT
    )
    scores = score_completed_run(
        _result(grounded_clause_id="9.9"), record=RECORD, thread_id="t-3"
    )
    failing = {s.name for s in scores if s.value < 1.0}
    assert "clause_id_exists" in failing


def test_an_ungrounded_escalation_still_scores_only_the_old_scorers():
    result = {
        "outcome": "escalate",
        "rule": None,
        "result": {"diff_id": 1, "escalation_id": 2},
    }
    scores = score_completed_run(result, record=RECORD, thread_id="t-4")
    assert {s.name for s in scores} == {
        "cited_source_present",
        "escalated_when_uncovered",
        "stayed_in_tool_surface",
    }


def test_a_skip_is_still_not_a_decision():
    assert score_completed_run({"result": {"skipped": True}}, record=RECORD, thread_id="t") == []


def test_an_uncovered_grounded_decision_does_not_fail_the_one_rule(monkeypatch):
    """rule is None and the outcome is not escalate - which used to be the
    definition of the failure this project exists to prevent. A grounded
    decision is traceable to a quoted clause instead of to a rule, so it
    passes, and the comment says which."""
    monkeypatch.setattr(
        "tilsynsagent.obs.online._document_text_for", lambda *a, **k: DOCUMENT
    )
    scores = score_completed_run(_result(), record=RECORD, thread_id="t-5")
    score = next(s for s in scores if s.name == "escalated_when_uncovered")
    assert score.value == 1.0
    assert "source document" in score.comment


def test_a_field_cited_decision_is_scored_without_fetching_the_document(monkeypatch):
    """A field citation is checked against the record, so it stays checkable
    when the document has since 404'd - and must not pay for a fetch."""
    fetched = []
    monkeypatch.setattr(
        "tilsynsagent.obs.online._document_text_for",
        lambda *a, **k: fetched.append(1) or "",
    )
    scores = score_completed_run(
        _result(
            grounded_citation_kind="field",
            grounded_clause_id="",
            grounded_clause_quote="",
            grounded_field_name="status",
            grounded_field_before="Forslag",
            grounded_field_after="Vedtaget",
        ),
        record=RECORD,
        thread_id="t-6",
    )
    assert fetched == []
    names = {s.name for s in scores}
    assert "field_citation_is_real" in names
    assert "clause_id_exists" not in names
    assert all(s.value == 1.0 for s in scores), [s for s in scores if s.value < 1.0]


def test_a_fabricated_field_citation_is_caught_end_to_end(monkeypatch):
    monkeypatch.setattr(
        "tilsynsagent.obs.online._document_text_for", lambda *a, **k: ""
    )
    scores = score_completed_run(
        _result(
            grounded_citation_kind="field",
            grounded_clause_id="",
            grounded_clause_quote="",
            grounded_field_name="status",
            grounded_field_before="Forslag",
            grounded_field_after="Aflyst",
        ),
        record=RECORD,
        thread_id="t-7",
    )
    failing = {s.name for s in scores if s.value < 1.0}
    assert "field_citation_is_real" in failing


def test_a_grounded_ignore_that_also_wrote_a_filing_is_a_leak():
    score = stayed_in_tool_surface(
        outcome="ignore", result={"grounding_id": 1, "filing_id": 2}
    )
    assert score.value == 0.0


def test_an_ignore_with_no_grounding_row_is_a_leak():
    assert stayed_in_tool_surface(outcome="ignore", result={"diff_id": 1}).value == 0.0

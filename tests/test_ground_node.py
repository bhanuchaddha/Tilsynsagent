"""The ground node and its router: the pivot where an uncovered case can
reach a decision with no person in the path.

Nothing here calls Groq or Postgres. The node's job is a routing decision and
a state shape, and both are checkable without either.
"""

from __future__ import annotations

import pytest

from tilsynsagent import graph as g
from tilsynsagent.documents.clauses import Clause
from tilsynsagent.llm.ground import Grounding

RECORD = {
    "feature_id": "f1",
    "lokplan_id": 123,
    "delnr": "1",
    "komnr": 101,
    "kommunenavn": "Testby",
    "versionsnr": 2,
    "status": "Vedtaget",
    "datoopdt": "2026-01-01T00:00:00.000Z",
    "maxbygnhjd": 8.5,
    "maxetager": None,
    "bebygpct": 40.0,
    "zonestatus": "Byzone",
    "anvendelsegenerel": "Boligområde",
    "doklink": "https://dokument.plandata.dk/20_123_1.pdf",
}
CLAUSE_TEXT = "6.2 Bebyggelse må ikke opføres med en større højde end 8,5 m."


def _state(**over):
    return {
        "record": RECORD,
        "changed_fields": {"maxbygnhjd": {"before": 8.5, "after": 8.5}},
        "sub_area_id": 1,
        "before_version_id": 1,
        "after_version_id": 2,
        "rule": None,
        "rule_reason": "no rule fired",
        **over,
    }


# --- the router ------------------------------------------------------------


def test_a_grounded_run_acts():
    assert g._route_after_ground({"grounded": True}) == "grounded"


@pytest.mark.parametrize("state", [{"grounded": False}, {}])
def test_anything_else_goes_to_a_person(state):
    """There is exactly one condition under which this system acts on an
    uncovered case without review, and a missing key is not it."""
    assert g._route_after_ground(state) == "escalate"


# --- the node's four ways to abstain ---------------------------------------


def test_a_record_with_no_doklink_cannot_ground(monkeypatch):
    record = {**RECORD, "doklink": None}
    out = g._ground_node(_state(record=record))
    assert out["grounded"] is False
    assert not out["document_available"]
    assert "no doklink" in out["document_error"]


def test_a_failed_fetch_cannot_ground(monkeypatch):
    from tilsynsagent.documents.cache import FetchResult

    monkeypatch.setattr(g, "fetch_document", lambda *a, **k: FetchResult("u", error="HTTP 404"))
    out = g._ground_node(_state())
    assert out["grounded"] is False
    assert "404" in out["document_error"]


def test_an_unextractable_document_cannot_ground(monkeypatch):
    from tilsynsagent.documents.cache import FetchResult
    from tilsynsagent.documents.extract import ExtractResult

    monkeypatch.setattr(g, "fetch_document", lambda *a, **k: FetchResult("u", content=b"x"))
    monkeypatch.setattr(
        g, "extract_text", lambda _c: ExtractResult(page_count=9, error="no text layer")
    )
    out = g._ground_node(_state())
    assert out["grounded"] is False
    assert out["document_page_count"] == 9


def test_an_empty_retrieval_costs_no_model_call(monkeypatch):
    """The first three abstentions are decided in code. A run that cannot
    read its document must not spend tokens discovering that."""
    from tilsynsagent.documents.cache import FetchResult
    from tilsynsagent.documents.extract import ExtractResult

    called = []
    monkeypatch.setattr(g, "fetch_document", lambda *a, **k: FetchResult("u", content=b"x"))
    monkeypatch.setattr(g, "extract_text", lambda _c: ExtractResult(text="t", page_count=5))
    monkeypatch.setattr(g, "split_clauses", lambda _t: [Clause("1.1", "1.1 noget")])
    monkeypatch.setattr(g, "retrieve_for_fields", lambda *a, **k: [])
    monkeypatch.setattr(g, "ground", lambda **k: called.append(1))

    out = g._ground_node(_state())
    assert out["grounded"] is False
    assert called == []
    assert out["document_available"] is True


def _wire(monkeypatch, grounding: Grounding):
    from tilsynsagent.documents.cache import FetchResult
    from tilsynsagent.documents.extract import ExtractResult

    monkeypatch.setattr(
        g, "fetch_document", lambda *a, **k: FetchResult("u", content=b"x", cache_hit=True)
    )
    monkeypatch.setattr(g, "extract_text", lambda _c: ExtractResult(text="t", page_count=41))
    monkeypatch.setattr(g, "split_clauses", lambda _t: [Clause("6.2", CLAUSE_TEXT)])
    monkeypatch.setattr(g, "retrieve_for_fields", lambda *a, **k: [Clause("6.2", CLAUSE_TEXT)])
    monkeypatch.setattr(g, "ground", lambda **k: grounding)
    monkeypatch.setattr(g, "take_last_usage", lambda: None)


def test_a_model_abstention_escalates_with_its_reasoning(monkeypatch):
    _wire(
        monkeypatch,
        Grounding(
            can_decide=False,
            outcome="",
            clause_id="",
            clause_quote="",
            reasoning="No clause covers storeys.",
            citation="u",
        ),
    )
    out = g._ground_node(_state())
    assert out["grounded"] is False
    assert out["grounded_reasoning"] == "No clause covers storeys."
    assert out["retrieved_clause_ids"] == ["6.2"]


def test_a_decision_without_checkable_evidence_is_treated_as_abstention(monkeypatch):
    """can_decide=True with no clause id is not a decision this system will
    act on - an uncheckable autonomous decision is what the one rule forbids."""
    _wire(
        monkeypatch,
        Grounding(
            can_decide=True,
            outcome="file",
            clause_id="",
            clause_quote="",
            reasoning="r",
            citation="u",
        ),
    )
    assert g._ground_node(_state())["grounded"] is False


def test_an_outcome_outside_the_permitted_set_is_an_abstention(monkeypatch):
    _wire(
        monkeypatch,
        Grounding(
            can_decide=True,
            outcome="escalate",
            clause_id="6.2",
            clause_quote="x",
            reasoning="r",
            citation="u",
        ),
    )
    assert g._ground_node(_state())["grounded"] is False


@pytest.mark.parametrize("outcome", ["file", "ignore"])
def test_a_good_grounding_carries_its_evidence_into_state(monkeypatch, outcome):
    _wire(
        monkeypatch,
        Grounding(
            can_decide=True,
            outcome=outcome,
            clause_id="6.2",
            clause_quote="højde end 8,5 m",
            reasoning="Clause 6.2 settles it.",
            citation="u",
        ),
    )
    out = g._ground_node(_state())
    assert out["grounded"] is True
    assert out["grounded_outcome"] == outcome
    assert out["grounded_clause_id"] == "6.2"
    assert out["grounded_clause_quote"] == "højde end 8,5 m"
    assert out["document_page_count"] == 41
    assert out["document_cache_hit"] is True
    assert g._route_after_ground(out) == "grounded"


def test_state_stays_msgpack_safe(monkeypatch):
    """The Postgres checkpointer serialises state via msgpack. A dataclass or
    a Clause leaking into state would break checkpointing at runtime, not at
    import - so it is pinned here."""
    _wire(
        monkeypatch,
        Grounding(
            can_decide=True,
            outcome="file",
            clause_id="6.2",
            clause_quote="q",
            reasoning="r",
            citation="u",
        ),
    )
    out = g._ground_node(_state())
    for value in out.values():
        assert isinstance(value, (bool, int, float, str, list, dict)), value

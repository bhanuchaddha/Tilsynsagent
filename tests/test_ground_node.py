"""The ground node and its router: the pivot where an uncovered case can
reach a decision with no person in the path.

Nothing here calls Groq or Postgres. The node's job is a routing decision and
a state shape, and both are checkable without either.
"""

from __future__ import annotations

import pytest

from tilsynsagent import graph as g
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

BEFORE = {**RECORD, "versionsnr": 1, "status": "Forslag"}


def _state(**over):
    return {
        "record": RECORD,
        # Empty by definition on the not-covered route: no watched field changed.
        "changed_fields": {},
        "before": BEFORE,
        "after": RECORD,
        "sub_area_id": 1,
        "before_version_id": 1,
        "after_version_id": 2,
        "rule": None,
        "rule_reason": "no rule fired",
        **over,
    }


def _grounding(**over) -> Grounding:
    """A Grounding with every required field filled, overridable per test.

    The schema is strict and has ten fields; spelling all of them out in
    every test buries the one thing each test is actually about.
    """
    return Grounding(
        **{
            "can_decide": False,
            "outcome": "",
            "citation_kind": "",
            "clause_id": "",
            "clause_quote": "",
            "field_name": "",
            "field_before": "",
            "field_after": "",
            "findings": "",
            "reasoning": "r",
            "citation": "u",
            **over,
        }
    )


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



def _wire(monkeypatch, grounding: Grounding):
    from tilsynsagent.documents.cache import FetchResult
    from tilsynsagent.documents.extract import ExtractResult

    monkeypatch.setattr(
        g, "fetch_document", lambda *a, **k: FetchResult("u", content=b"x", cache_hit=True)
    )
    monkeypatch.setattr(
        g, "extract_text", lambda _c: ExtractResult(text=CLAUSE_TEXT, page_count=41)
    )
    monkeypatch.setattr(g, "ground", lambda **k: grounding)
    monkeypatch.setattr(g, "take_last_usage", lambda: None)


def test_a_model_abstention_escalates_with_what_it_found(monkeypatch):
    """An abstention hands over a reading, not an apology. findings is what
    the person picking this up starts from - see improvement 6."""
    _wire(
        monkeypatch,
        _grounding(
            findings="Clause 6.2 sets a maximum height of 8,5 m.",
            reasoning="Neither states whether the storey count changed.",
        ),
    )
    out = g._ground_node(_state())
    assert out["grounded"] is False
    assert out["grounded_findings"] == "Clause 6.2 sets a maximum height of 8,5 m."
    assert out["grounded_reasoning"] == "Neither states whether the storey count changed."


def test_a_decision_without_checkable_evidence_is_treated_as_abstention(monkeypatch):
    """can_decide=True with no clause id is not a decision this system will
    act on - an uncheckable autonomous decision is what the one rule forbids."""
    _wire(
        monkeypatch,
        _grounding(can_decide=True, outcome="file", citation_kind="clause"),
    )
    assert g._ground_node(_state())["grounded"] is False


def test_a_field_citation_missing_its_values_is_an_abstention(monkeypatch):
    """The field path must not be the loose one. A named field with neither
    a before nor an after value is as uncheckable as a missing quote."""
    _wire(
        monkeypatch,
        _grounding(
            can_decide=True, outcome="file", citation_kind="field", field_name="status"
        ),
    )
    assert g._ground_node(_state())["grounded"] is False


def test_an_unknown_citation_kind_is_an_abstention(monkeypatch):
    _wire(
        monkeypatch,
        _grounding(
            can_decide=True,
            outcome="file",
            citation_kind="vibes",
            clause_id="6.2",
            clause_quote="q",
        ),
    )
    assert g._ground_node(_state())["grounded"] is False


def test_an_outcome_outside_the_permitted_set_is_an_abstention(monkeypatch):
    _wire(
        monkeypatch,
        _grounding(
            can_decide=True,
            outcome="escalate",
            citation_kind="clause",
            clause_id="6.2",
            clause_quote="x",
        ),
    )
    assert g._ground_node(_state())["grounded"] is False


@pytest.mark.parametrize("outcome", ["file", "ignore"])
def test_a_good_grounding_carries_its_evidence_into_state(monkeypatch, outcome):
    _wire(
        monkeypatch,
        _grounding(
            can_decide=True,
            outcome=outcome,
            citation_kind="clause",
            clause_id="6.2",
            clause_quote="højde end 8,5 m",
            reasoning="Clause 6.2 settles it.",
        ),
    )
    out = g._ground_node(_state())
    assert out["grounded"] is True
    assert out["grounded_outcome"] == outcome
    assert out["grounded_citation_kind"] == "clause"
    assert out["grounded_clause_id"] == "6.2"
    assert out["grounded_clause_quote"] == "højde end 8,5 m"
    assert out["document_page_count"] == 41
    assert out["document_cache_hit"] is True
    assert g._route_after_ground(out) == "grounded"


@pytest.mark.parametrize("outcome", ["file", "ignore"])
def test_a_field_cited_grounding_is_acted_on(monkeypatch, outcome):
    """Demo case 2, and the reason is_decided accepts a field: status F -> V
    is the plan being formally adopted, and it is a fully traceable source.
    Before stage 1 this decision was discarded as uncheckable."""
    _wire(
        monkeypatch,
        _grounding(
            can_decide=True,
            outcome=outcome,
            citation_kind="field",
            field_name="status",
            field_before="Forslag",
            field_after="Vedtaget",
            reasoning="The plan was formally adopted.",
        ),
    )
    out = g._ground_node(_state())
    assert out["grounded"] is True
    assert out["grounded_citation_kind"] == "field"
    assert out["grounded_field_name"] == "status"
    assert out["grounded_field_before"] == "Forslag"
    assert out["grounded_field_after"] == "Vedtaget"
    assert out["grounded_clause_id"] == ""
    assert g._route_after_ground(out) == "grounded"


def test_the_model_sees_the_whole_record_and_the_whole_document(monkeypatch):
    """The stage 1 change, pinned. The 2026-09-06 baseline measured 0/5
    grounded because the model was handed an empty diff and four retrieved
    clauses; it must now receive both full versions and the full text."""
    from tilsynsagent.documents.cache import FetchResult
    from tilsynsagent.documents.extract import ExtractResult

    seen = {}
    monkeypatch.setattr(g, "fetch_document", lambda *a, **k: FetchResult("u", content=b"x"))
    monkeypatch.setattr(
        g, "extract_text", lambda _c: ExtractResult(text=CLAUSE_TEXT, page_count=41)
    )
    monkeypatch.setattr(g, "take_last_usage", lambda: None)
    monkeypatch.setattr(
        g, "ground", lambda **kw: seen.update(kw) or _grounding()
    )

    g._ground_node(_state())
    assert seen["document_text"] == CLAUSE_TEXT
    assert seen["before"]["status"] == "Forslag"
    assert seen["after"]["status"] == "Vedtaget"
    # The empty watched diff still reaches the prompt, as context rather than
    # as the question - build_prompt says so in words.
    assert seen["watched_changed_fields"] == {}


def test_state_stays_msgpack_safe(monkeypatch):
    """The Postgres checkpointer serialises state via msgpack. A dataclass
    leaking into state would break checkpointing at runtime, not at import -
    so it is pinned here."""
    _wire(
        monkeypatch,
        _grounding(
            can_decide=True,
            outcome="file",
            citation_kind="clause",
            clause_id="6.2",
            clause_quote="q",
        ),
    )
    out = g._ground_node(_state())
    for value in out.values():
        assert isinstance(value, (bool, int, float, str, list, dict)), value

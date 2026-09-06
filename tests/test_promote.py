"""Promotion: turning an annotated production run into a golden case.

The dataset is a definition of what is correct, not a log of what happened -
so these tests are about provenance staying visible and a verdict mapping to
the right label, not about the write itself.
"""

from __future__ import annotations

import json

import pytest

from evals.cases import expected_route
from evals.promote import ORIGIN, _label_for, append_cases, build_case, next_case_id


def _case(**over):
    base = dict(
        trace_id="tr-1",
        verdict="correct",
        comment="looks right",
        agent_outcome="file",
        changed_fields={"maxbygnhjd": {"before": 8.5, "after": 12.0}},
        before={"maxbygnhjd": 8.5},
        after={"maxbygnhjd": 12.0},
        source={"document": "https://x/1.pdf"},
        clause_id="6.2",
        clause_quote="højde end 8,5 m",
    )
    base.update(over)
    return build_case(**base)


def test_the_id_prefix_keeps_provenance_visible(tmp_path):
    """AQ- alongside ZL- and EC-: anyone reading cases.jsonl can see which
    cases came from watching production without cross-referencing anything."""
    path = tmp_path / "cases.jsonl"
    assert next_case_id(path) == "AQ-001"
    path.write_text(json.dumps({"id": "AQ-007"}) + "\n" + json.dumps({"id": "ZL-034"}) + "\n")
    assert next_case_id(path) == "AQ-008"


def test_a_correct_verdict_keeps_the_agents_own_outcome():
    assert _label_for("correct", "ignore") == "ignore"
    assert _label_for("correct", "file") == "file"


def test_should_have_escalated_becomes_an_escalate_label():
    assert _label_for("wrong - should have escalated", "file") == "escalate"


def test_decided_the_opposite_inverts_the_outcome():
    assert _label_for("wrong - decided the opposite", "file") == "ignore"
    assert _label_for("wrong - decided the opposite", "ignore") == "file"


def test_a_contested_truth_escalates():
    """The third failure class: the register and the document disagree. Not
    the agent being wrong - a case where the truth itself is contested, and
    the only correct behaviour is to put it in front of a person."""
    assert _label_for("register and document disagree", "file") == "escalate"


def test_an_unknown_verdict_escalates_rather_than_guessing():
    assert _label_for("something new nobody mapped", "file") == "escalate"


def test_a_promoted_case_carries_the_clause_the_agent_used():
    """A failing test that shows *what the agent read* when it was wrong is
    diagnosable; one that only shows it was wrong is not."""
    case = _case(verdict="wrong - decided the opposite")
    assert case["grounded_clause"]["clause_id"] == "6.2"
    assert case["origin"] == ORIGIN
    assert case["rule"] is None


@pytest.mark.parametrize(
    "verdict,agent,route",
    [
        ("correct", "file", "grounded_file"),
        ("correct", "ignore", "grounded_ignore"),
        ("wrong - should have escalated", "file", "escalate"),
        ("wrong - decided the opposite", "file", "grounded_ignore"),
    ],
)
def test_every_promoted_case_maps_to_a_route(verdict, agent, route):
    """A case the eval runner cannot route is a case that silently never
    runs - the promotion path's most likely quiet failure."""
    assert expected_route(_case(verdict=verdict, agent_outcome=agent)) == route


def test_appending_is_idempotent_on_trace_id(tmp_path):
    """A nightly re-reading completed queue items would otherwise re-append
    the same case every night, quietly double-weighting it in the score."""
    path = tmp_path / "cases.jsonl"
    case = _case()
    assert append_cases([case], path=path) == [case["id"]]
    assert append_cases([case], path=path) == []
    assert len(path.read_text().strip().splitlines()) == 1

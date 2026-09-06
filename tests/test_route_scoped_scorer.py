"""did_not_read_the_document, after grounding made its premise route-specific.

Scoped rather than deleted: on the ungrounded paths the original premise
holds exactly as before, and a claim to have read the PDF there is still the
fabrication this was built to catch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.scorers import ALL_SCORERS, did_not_read_the_document, score_all

CASE = {
    "id": "T-1",
    "source": {"document": "https://x/1.pdf"},
    "changed_fields": {},
    "before": {},
    "after": {},
}
GROUNDED_OUTPUT = "The document states in clause 6.2 that height may not exceed 8,5 m."


@pytest.mark.parametrize("route", ["file", "escalate", "not_covered"])
def test_the_boundary_still_holds_on_ungrounded_routes(route):
    assert did_not_read_the_document(CASE, GROUNDED_OUTPUT, route=route).value == 0.0


@pytest.mark.parametrize("route", ["grounded_file", "grounded_ignore"])
def test_grounded_output_is_not_penalised_for_quoting_the_document(route):
    """The ground node's whole job is to read clauses and quote them. Scoring
    that at 0.0 against a threshold pinned at 1.0 would turn CI red looking
    like a model regression, when the fault is a scorer whose premise expired."""
    assert did_not_read_the_document(CASE, GROUNDED_OUTPUT, route=route).value == 1.0


def test_an_unspecified_route_gets_the_stricter_behaviour():
    """A caller that has not thought about routes must not get a silent pass."""
    assert did_not_read_the_document(CASE, GROUNDED_OUTPUT).value == 0.0
    assert did_not_read_the_document(CASE, GROUNDED_OUTPUT, route=None).value == 0.0


def test_clean_output_passes_on_every_route():
    clean = "Height changed from 8.5 to 12.0. Source: https://x/1.pdf"
    for route in ("file", "grounded_file", None):
        assert did_not_read_the_document(CASE, clean, route=route).value == 1.0


def test_score_all_passes_the_route_only_where_it_matters():
    scores = score_all(CASE, GROUNDED_OUTPUT, route="grounded_file")
    assert {s.name for s in scores} == {s.__name__ for s in ALL_SCORERS}
    assert next(s for s in scores if s.name == "did_not_read_the_document").value == 1.0


def test_the_threshold_for_this_scorer_is_still_unforgiving():
    """It stays at 1.0. Scoping it did not weaken it - on the routes where it
    applies, a single failure is still a real signal."""
    thresholds = json.loads(
        (Path(__file__).resolve().parent.parent / "evals" / "thresholds.json").read_text()
    )
    assert thresholds["scorers"]["did_not_read_the_document"] == 1.0

"""Unit tests for review/app.py's pure logic: the golden-dataset append
helpers and the ignore/scoreable guard. No Streamlit runtime and no
database - app.py only calls st.* inside functions these tests don't
invoke, and streamlit.runtime.exists() is False under plain pytest so
importing the module does not launch main() (see app.py's bottom guard).

evals/golden/cases.jsonl itself is never touched: GOLDEN_CASES_PATH is
monkeypatched to a temp file per test.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tilsynsagent.review import app


@pytest.fixture
def golden_path(tmp_path, monkeypatch):
    path = tmp_path / "cases.jsonl"
    monkeypatch.setattr(app, "GOLDEN_CASES_PATH", path)
    return path


def _version_row(**overrides) -> dict:
    fields = dict(
        status="V",
        datoopdt=datetime(2026, 1, 1, tzinfo=UTC),
        versionsnr=1,
        maxbygnhjd=8.5,
        maxetager=2,
        bebygpct=40,
        zonestatus=None,
        anvendelsegenerel="Boligområde",
    )
    fields.update(overrides)
    return fields


def _detail(**overrides) -> dict:
    fields = dict(
        escalation_id=1,
        rule="R4",
        rule_set="zealand-local-plans-v2",
        kommunenavn="Testkommune",
        komnr=999,
        lokplan_id=123456,
        delnr="A",
        citation="https://dokument.plandata.dk/test.pdf",
        before=_version_row(),
        after=_version_row(versionsnr=2, bebygpct=None),
        changed_fields={"bebygpct": {"before": 40, "after": None}},
    )
    fields.update(overrides)
    return fields


def test_next_case_id_starts_at_ec_001_when_file_missing(golden_path):
    assert app._next_case_id() == "EC-001"


def test_next_case_id_increments_past_existing_ec_cases(golden_path):
    golden_path.write_text(
        json.dumps({"id": "ZL-001"}) + "\n" + json.dumps({"id": "EC-003"}) + "\n"
    )
    assert app._next_case_id() == "EC-004"


def test_append_to_golden_dataset_writes_expected_shape(golden_path):
    detail = _detail()
    case_id = app._append_to_golden_dataset(detail, "file", "test reason")

    assert case_id == "EC-001"
    lines = golden_path.read_text().splitlines()
    assert len(lines) == 1

    case = json.loads(lines[0])
    assert case["id"] == "EC-001"
    assert case["label"] == "file"
    assert case["reason"] == "test reason"
    # The escalating rule goes in escalated_rule, never in "rule": "rule"
    # means "the rule that produced this label", and this label came from a
    # person - often disagreeing with the engine, which is why the case is
    # worth keeping. See evals/golden/README.md and test_rules_engine.py.
    assert case["rule"] is None
    assert case["escalated_rule"] == "R4"
    assert case["rule_set"] == "zealand-local-plans-v2"
    assert case["origin"] == "escalation-derived"
    assert case["source"]["municipality"] == "Testkommune"
    assert case["source"]["plan_id"] == 123456
    assert case["source"]["sub_area"] == "A"
    assert case["source"]["document"] == "https://dokument.plandata.dk/test.pdf"
    assert case["changed_fields"] == {"bebygpct": {"before": 40, "after": None}}
    assert case["before"]["version"] == 1
    assert case["after"]["version"] == 2
    assert case["after"]["bebygpct"] is None


def test_append_to_golden_dataset_is_readable_by_evals_cases(golden_path, monkeypatch):
    """The exact shape evals/cases.py and expected_route() read - a case
    written by the review screen must round-trip through the real loader,
    not just look right by eye."""
    from evals import cases as evals_cases

    monkeypatch.setattr(evals_cases, "CASES_PATH", golden_path)

    app._append_to_golden_dataset(_detail(), "file", "test reason")

    loaded = evals_cases.load_golden_cases()
    assert len(loaded) == 1
    # Route and label part company for escalation-derived cases: the route is
    # what the system did (escalate, which is what put it in front of a
    # person), the label is what the person then decided. See evals/cases.py's
    # expected_route and tests/test_eval_cases.py.
    assert evals_cases.expected_route(loaded[0]) == "escalate"
    assert loaded[0]["label"] == "file"


def test_append_to_golden_dataset_appends_without_clobbering(golden_path):
    golden_path.write_text(json.dumps({"id": "ZL-001", "label": "file"}) + "\n")

    app._append_to_golden_dataset(_detail(), "escalate", "second reason")

    lines = golden_path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == "ZL-001"
    assert json.loads(lines[1])["id"] == "EC-001"
    assert json.loads(lines[1])["label"] == "escalate"


def test_version_dict_handles_none():
    assert app._version_dict(None) is None


def test_version_dict_maps_db_row_to_case_shape():
    row = _version_row(status="F", versionsnr=3, maxetager=None)
    result = app._version_dict(row)
    assert result == {
        "status": "F",
        "updated": row["datoopdt"].isoformat(),
        "version": 3,
        "maxbygnhjd": 8.5,
        "maxetager": None,
        "bebygpct": 40,
        "zonestatus": None,
        "anvendelsegenerel": "Boligområde",
    }


@pytest.mark.parametrize(
    "label,expected",
    [("file", True), ("escalate", True), ("ignore", False)],
)
def test_is_scoreable_label(label, expected):
    """Only 'ignore' is excluded from the dataset-append offer - the eval
    pipeline has no route for it until Phase 6 grounding lands
    (evals/README.md)."""
    assert app._is_scoreable_label(label) is expected

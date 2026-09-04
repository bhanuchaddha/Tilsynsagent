"""Unit tests per rule, plus the full golden-dataset correctness check.

The golden-dataset test is verification 3 from the Phase 1 plan: all 34 cases
through rules/engine.py, confirming it reproduces the labels. Not the Phase 2
eval - a correctness check on deterministic code, so it must be 100%.
"""

import json
from pathlib import Path

import pytest

from tilsynsagent.rules.engine import Outcome, apply_rules

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "evals" / "golden" / "cases.jsonl"


def load_golden():
    with open(GOLDEN_PATH) as f:
        return [json.loads(line) for line in f]


ALL_CASES = load_golden()

# Escalation-derived cases record what a *person* decided about a case the
# engine escalated - and the interesting ones are precisely those where the
# person disagreed with the engine (see review/app.py's append helper). They
# are therefore not part of the "the engine reproduces every label" check:
# asserting that would demand the engine already agree with a human override
# it has never been taught, which is the opposite of what the case records.
# They are checked separately below, and they still feed the LLM eval suite
# through evals/cases.py.
GOLDEN_CASES = [c for c in ALL_CASES if c.get("origin") != "escalation-derived"]
ESCALATION_DERIVED = [c for c in ALL_CASES if c.get("origin") == "escalation-derived"]


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["id"] for c in GOLDEN_CASES])
def test_golden_dataset(case):
    decision = apply_rules(case["changed_fields"], case["before"], case["after"])
    assert decision.outcome.value == case["label"], decision.reason
    if case["rule"] is not None:
        assert decision.rule == case["rule"], decision.reason


def test_golden_dataset_is_100_percent():
    """The Phase 1 plan requires this exact number, not just per-case passes."""
    mismatches = []
    for case in GOLDEN_CASES:
        decision = apply_rules(case["changed_fields"], case["before"], case["after"])
        ok = decision.outcome.value == case["label"] and (
            case["rule"] is None or decision.rule == case["rule"]
        )
        if not ok:
            mismatches.append(case["id"])
    assert mismatches == [], f"engine disagreed with golden labels: {mismatches}"


@pytest.mark.parametrize(
    "case", ESCALATION_DERIVED, ids=[c["id"] for c in ESCALATION_DERIVED] or ["none"]
)
def test_escalation_derived_cases_record_a_human_decision(case):
    """What these cases must hold instead.

    The engine must still *escalate* them - that is what put them in front of
    a person. What it must not do is claim the person's label as its own: an
    escalation-derived case carries rule=None (the label came from a human,
    not a rule) and escalated_rule naming what actually fired, or nothing if
    the case was NOT_COVERED.

    A case where the engine no longer escalates is a signal worth failing on:
    either the rule set changed underneath the dataset, or this case is now
    covered and should be relabelled deliberately rather than left to drift.
    """
    assert case["rule"] is None, (
        f"{case['id']}: 'rule' means 'the rule that produced this label', and this "
        "label came from a person - the escalating rule belongs in 'escalated_rule'"
    )
    assert "escalated_rule" in case
    decision = apply_rules(case["changed_fields"], case["before"], case["after"])
    assert decision.outcome.value in ("escalate", "not_covered"), (
        f"{case['id']}: the engine no longer escalates this case (says "
        f"{decision.outcome.value!r}) - relabel it deliberately rather than let it drift"
    )


# --- R1: physically impossible records are escalated -------------------------


def test_r1_more_storeys_than_metres():
    before = {"maxbygnhjd": 2, "maxetager": 10}
    after = {"maxbygnhjd": 10, "maxetager": 2}
    changed = {
        "maxbygnhjd": {"before": 2, "after": 10},
        "maxetager": {"before": 10, "after": 2},
    }
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.ESCALATE
    assert d.rule == "R1"


def test_r1_zero_height():
    before = {"maxbygnhjd": 8.5, "maxetager": 2}
    after = {"maxbygnhjd": 0, "maxetager": 2}
    changed = {"maxbygnhjd": {"before": 8.5, "after": 0}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.ESCALATE
    assert d.rule == "R1"


def test_r1_bebygpct_to_zero():
    before = {"bebygpct": 40}
    after = {"bebygpct": 0}
    changed = {"bebygpct": {"before": 40, "after": 0}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.ESCALATE
    assert d.rule == "R1"


def test_r1_bebygpct_zero_does_not_fire_when_no_prior_value():
    """bebygpct appearing as 0 for the first time is R3 (an addition), not
    R1 - there was nothing to fall from, so the record is not impossible."""
    before = {"bebygpct": None}
    after = {"bebygpct": 0}
    changed = {"bebygpct": {"before": None, "after": 0}}
    d = apply_rules(changed, before, after)
    assert d.rule == "R3"
    assert d.outcome is Outcome.FILE


def test_r1_takes_precedence_over_r2():
    """A record can trip both R1 and a real value change; R1 comes first."""
    before = {"maxbygnhjd": 2, "maxetager": 10, "anvendelsegenerel": "Boligområde"}
    after = {"maxbygnhjd": 10, "maxetager": 2, "anvendelsegenerel": "Erhvervsområde"}
    changed = {
        "maxbygnhjd": {"before": 2, "after": 10},
        "maxetager": {"before": 10, "after": 2},
        "anvendelsegenerel": {"before": "Boligområde", "after": "Erhvervsområde"},
    }
    d = apply_rules(changed, before, after)
    assert d.rule == "R1"
    assert d.outcome is Outcome.ESCALATE


# --- R2: any watched field value -> different value is filed -----------------


def test_r2_use_change_files():
    before = {"anvendelsegenerel": "Boligområde"}
    after = {"anvendelsegenerel": "Erhvervsområde"}
    changed = {"anvendelsegenerel": {"before": "Boligområde", "after": "Erhvervsområde"}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.FILE
    assert d.rule == "R2"


def test_r2_use_outranks_dimensions_when_both_change():
    before = {"anvendelsegenerel": "Boligområde", "maxbygnhjd": 8.5}
    after = {"anvendelsegenerel": "Erhvervsområde", "maxbygnhjd": 10}
    changed = {
        "anvendelsegenerel": {"before": "Boligområde", "after": "Erhvervsområde"},
        "maxbygnhjd": {"before": 8.5, "after": 10},
    }
    d = apply_rules(changed, before, after)
    assert d.rule == "R2"
    assert "Use decides" in d.reason


def test_r2_height_change_files_with_direction():
    before = {"maxbygnhjd": 8.5}
    after = {"maxbygnhjd": 12.5}
    changed = {"maxbygnhjd": {"before": 8.5, "after": 12.5}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.FILE
    assert d.rule == "R2"
    assert "increased" in d.reason


def test_r2_decrease_states_direction():
    before = {"bebygpct": 40}
    after = {"bebygpct": 30}
    changed = {"bebygpct": {"before": 40, "after": 30}}
    d = apply_rules(changed, before, after)
    assert d.rule == "R2"
    assert "decreased" in d.reason


def test_r2_zone_status_change_files():
    """v2: zone status is decidable like any other watched field."""
    before = {"zonestatus": "Byzone"}
    after = {"zonestatus": "Sommerhusområde"}
    changed = {"zonestatus": {"before": "Byzone", "after": "Sommerhusområde"}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.FILE
    assert d.rule == "R2"


# --- R3: field(s) gained a value, none lost, is filed -------------------------


def test_r3_pure_addition_filed():
    before = {"zonestatus": None}
    after = {"zonestatus": "Byzone"}
    changed = {"zonestatus": {"before": None, "after": "Byzone"}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.FILE
    assert d.rule == "R3"


def test_r3_multiple_additions_still_filed():
    before = {"zonestatus": None, "bebygpct": None}
    after = {"zonestatus": "Byzone", "bebygpct": 40}
    changed = {
        "zonestatus": {"before": None, "after": "Byzone"},
        "bebygpct": {"before": None, "after": 40},
    }
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.FILE
    assert d.rule == "R3"


# --- R4: any field lost its value (alone, or mixed with gains) ---------------


def test_r4_removal_escalated():
    before = {"maxbygnhjd": 8.5}
    after = {"maxbygnhjd": None}
    changed = {"maxbygnhjd": {"before": 8.5, "after": None}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.ESCALATE
    assert d.rule == "R4"
    assert "8.5" in d.reason


def test_r4_mixed_addition_and_removal():
    """v2 absorbs v1's R7: a mixed gain/loss revision is just R4 winning on
    precedence over R3."""
    before = {"bebygpct": None, "zonestatus": "Byzone"}
    after = {"bebygpct": 70, "zonestatus": None}
    changed = {
        "bebygpct": {"before": None, "after": 70},
        "zonestatus": {"before": "Byzone", "after": None},
    }
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.ESCALATE
    assert d.rule == "R4"


# --- No watched field changed: not_covered, handed to assess() ---------------


def test_no_watched_change_is_not_covered():
    before = {"maxbygnhjd": 8.5}
    after = {"maxbygnhjd": 8.5}
    d = apply_rules({}, before, after)
    assert d.outcome is Outcome.NOT_COVERED
    assert d.rule is None
    assert not d.is_covered

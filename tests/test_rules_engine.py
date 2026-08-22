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


GOLDEN_CASES = load_golden()


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


# --- R1: permitted use change is always filed -------------------------------


def test_r1_use_change_files():
    before = {"anvendelsegenerel": "Boligområde"}
    after = {"anvendelsegenerel": "Erhvervsområde"}
    changed = {"anvendelsegenerel": {"before": "Boligområde", "after": "Erhvervsområde"}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.FILE
    assert d.rule == "R1"


def test_r1_outranks_r2_when_both_change():
    before = {"anvendelsegenerel": "Boligområde", "maxbygnhjd": 8.5}
    after = {"anvendelsegenerel": "Erhvervsområde", "maxbygnhjd": 10}
    changed = {
        "anvendelsegenerel": {"before": "Boligområde", "after": "Erhvervsområde"},
        "maxbygnhjd": {"before": 8.5, "after": 10},
    }
    d = apply_rules(changed, before, after)
    assert d.rule == "R1"


# --- R2: dimensional limit change is filed -----------------------------------


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


# --- R3: field populated for the first time is ignored -----------------------


def test_r3_pure_addition_ignored():
    before = {"zonestatus": None}
    after = {"zonestatus": "Byzone"}
    changed = {"zonestatus": {"before": None, "after": "Byzone"}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.IGNORE
    assert d.rule == "R3"


def test_r3_multiple_additions_still_ignored():
    before = {"zonestatus": None, "bebygpct": None}
    after = {"zonestatus": "Byzone", "bebygpct": 40}
    changed = {
        "zonestatus": {"before": None, "after": "Byzone"},
        "bebygpct": {"before": None, "after": 40},
    }
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.IGNORE
    assert d.rule == "R3"


# --- R4: limit disappearing is escalated -------------------------------------


def test_r4_removal_escalated():
    before = {"maxbygnhjd": 8.5}
    after = {"maxbygnhjd": None}
    changed = {"maxbygnhjd": {"before": 8.5, "after": None}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.ESCALATE
    assert d.rule == "R4"
    assert "8.5" in d.reason


# --- R5: physically impossible records are escalated -------------------------


def test_r5_more_storeys_than_metres():
    before = {"maxbygnhjd": 2, "maxetager": 10}
    after = {"maxbygnhjd": 10, "maxetager": 2}
    changed = {
        "maxbygnhjd": {"before": 2, "after": 10},
        "maxetager": {"before": 10, "after": 2},
    }
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.ESCALATE
    assert d.rule == "R5"


def test_r5_zero_height():
    before = {"maxbygnhjd": 8.5, "maxetager": 2}
    after = {"maxbygnhjd": 0, "maxetager": 2}
    changed = {"maxbygnhjd": {"before": 8.5, "after": 0}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.ESCALATE
    assert d.rule == "R5"


def test_r5_takes_precedence_over_r6():
    """A record can trip both R5 and R6; R5 comes first in precedence."""
    before = {"maxbygnhjd": 2, "maxetager": 10, "bebygpct": 40}
    after = {"maxbygnhjd": 0, "maxetager": 10, "bebygpct": 0}
    changed = {
        "maxbygnhjd": {"before": 2, "after": 0},
        "bebygpct": {"before": 40, "after": 0},
    }
    d = apply_rules(changed, before, after)
    assert d.rule == "R5"


# --- R6: built percentage falling to zero is escalated ------------------------


def test_r6_bebygpct_to_zero():
    before = {"bebygpct": 40}
    after = {"bebygpct": 0}
    changed = {"bebygpct": {"before": 40, "after": 0}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.ESCALATE
    assert d.rule == "R6"


def test_r6_does_not_fire_when_no_prior_value():
    """bebygpct appearing as 0 for the first time is R3, not R6 - there was
    nothing to fall from."""
    before = {"bebygpct": None}
    after = {"bebygpct": 0}
    changed = {"bebygpct": {"before": None, "after": 0}}
    d = apply_rules(changed, before, after)
    assert d.rule == "R3"


# --- R7: mixed additions and removals are escalated ---------------------------


def test_r7_mixed_addition_and_removal():
    before = {"bebygpct": None, "zonestatus": "Byzone"}
    after = {"bebygpct": 70, "zonestatus": None}
    changed = {
        "bebygpct": {"before": None, "after": 70},
        "zonestatus": {"before": "Byzone", "after": None},
    }
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.ESCALATE
    assert d.rule == "R7"


# --- No watched field changed --------------------------------------------------


def test_no_change_is_ignored():
    before = {"maxbygnhjd": 8.5}
    after = {"maxbygnhjd": 8.5}
    d = apply_rules({}, before, after)
    assert d.outcome is Outcome.IGNORE
    assert d.rule is None


# --- Not covered: zone status changing alone ------------------------------------


def test_zonestatus_alone_is_not_covered():
    before = {"zonestatus": "Byzone"}
    after = {"zonestatus": "Sommerhusområde"}
    changed = {"zonestatus": {"before": "Byzone", "after": "Sommerhusområde"}}
    d = apply_rules(changed, before, after)
    assert d.outcome is Outcome.NOT_COVERED
    assert not d.is_covered

"""Offline tests for evals/cases.py's loading and routing logic."""

from evals.cases import (
    dataset_item_id,
    expected_route,
    iter_by_route,
    load_all_cases,
    load_golden_cases,
    load_not_covered_cases,
)
from tilsynsagent.rules.engine import Outcome, apply_rules


# The 34 hand-labelled transitions the dataset was founded on. That number is
# pinned deliberately: it is the evidence base every baseline is measured
# against, and it must not drift silently. What may grow is the set of cases
# the *running system* contributed - origin "escalation-derived", added one
# click at a time through the review screen - so those are counted separately
# rather than folded into the founding count.
FOUNDING_ORIGINS = {"hand-labelled", "rule-derived-v2"}


def _founding_cases():
    return [c for c in load_golden_cases() if c.get("origin") in FOUNDING_ORIGINS]


def _escalation_derived():
    return [c for c in load_golden_cases() if c.get("origin") == "escalation-derived"]


def test_founding_golden_cases_count_is_34():
    assert len(_founding_cases()) == 34


def test_every_golden_case_has_a_known_origin():
    """A case with an unrecognised origin would be silently excluded from
    both counts above, which is how a dataset quietly stops meaning what its
    documentation says."""
    allowed = FOUNDING_ORIGINS | {"escalation-derived"}
    unknown = {c["id"]: c.get("origin") for c in load_golden_cases() if c.get("origin") not in allowed}
    assert unknown == {}, f"unrecognised origins: {unknown}"


def test_escalation_derived_cases_carry_no_rule():
    """Their label came from a person, not a rule - see review/app.py's
    append helper and tests/test_rules_engine.py."""
    for case in _escalation_derived():
        assert case["rule"] is None, case["id"]


def test_golden_case_ids_are_unique():
    cases = load_golden_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_not_covered_cases_exist_and_are_synthetic():
    cases = load_not_covered_cases()
    assert 4 <= len(cases) <= 6
    for case in cases:
        assert case["origin"] == "synthetic-not-covered"
        assert case["label"] == "escalate"


def test_load_all_cases_is_concatenation():
    golden = load_golden_cases()
    not_covered = load_not_covered_cases()
    all_cases = load_all_cases()
    assert len(all_cases) == len(golden) + len(not_covered)


def test_expected_route_matches_label_for_founding_cases():
    for case in _founding_cases():
        assert expected_route(case) == case["label"]


def test_expected_route_of_an_escalation_derived_case_is_escalate():
    """Route and label part company here, on purpose. The *route* is what the
    system does with the case - it escalates, which is what put it in front of
    a person. The *label* is what the person then decided, which may well
    contradict the engine. Conflating them would score the system as wrong
    every time a human overrode it, turning the review loop into a permanent
    self-inflicted regression."""
    for case in _escalation_derived():
        assert expected_route(case) == "escalate"


def test_expected_route_is_not_covered_for_synthetic_cases():
    for case in load_not_covered_cases():
        assert expected_route(case) == "not_covered"


def test_dataset_item_id_is_case_id_verbatim():
    for case in load_all_cases():
        assert dataset_item_id(case) == case["id"]


def test_iter_by_route_file_matches_golden_composition():
    """The founding 34's composition, which the recorded baselines describe.
    Escalation-derived cases are excluded for the same reason as above: they
    change this count as the system runs, and a baseline's composition table
    must stay comparable to the run that produced it."""
    file_cases = list(iter_by_route(_founding_cases(), "file"))
    assert len(file_cases) == 22


def test_iter_by_route_escalate_matches_golden_composition():
    escalate_cases = list(iter_by_route(_founding_cases(), "escalate"))
    assert len(escalate_cases) == 12


def test_iter_by_route_not_covered_only_in_synthetic_set():
    assert list(iter_by_route(load_golden_cases(), "not_covered")) == []
    assert len(list(iter_by_route(load_all_cases(), "not_covered"))) == len(
        load_not_covered_cases()
    )


def test_not_covered_cases_actually_route_not_covered_in_the_real_engine():
    """The reason these cases exist: they must exercise apply_rules's
    NOT_COVERED branch for real, not just claim to by label."""
    for case in load_not_covered_cases():
        decision = apply_rules(case["changed_fields"], case["before"], case["after"])
        assert decision.outcome is Outcome.NOT_COVERED, (
            f"{case['id']} did not route to NOT_COVERED: {decision.reason}"
        )

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


def test_golden_cases_count_is_34():
    assert len(load_golden_cases()) == 34


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


def test_expected_route_matches_label_for_golden_cases():
    for case in load_golden_cases():
        assert expected_route(case) == case["label"]


def test_expected_route_is_not_covered_for_synthetic_cases():
    for case in load_not_covered_cases():
        assert expected_route(case) == "not_covered"


def test_dataset_item_id_is_case_id_verbatim():
    for case in load_all_cases():
        assert dataset_item_id(case) == case["id"]


def test_iter_by_route_file_matches_golden_composition():
    cases = load_golden_cases()
    file_cases = list(iter_by_route(cases, "file"))
    assert len(file_cases) == 22


def test_iter_by_route_escalate_matches_golden_composition():
    cases = load_golden_cases()
    escalate_cases = list(iter_by_route(cases, "escalate"))
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

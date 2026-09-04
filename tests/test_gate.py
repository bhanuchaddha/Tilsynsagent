"""The CI gate's decision logic, offline. No LLM, no network.

The gate is the thing standing between a behaviour regression and main, so
its own failure modes matter more than most: a gate that cannot fail is worse
than no gate, because it produces a green check that means nothing.
"""

from __future__ import annotations

import json

from evals.gate import evaluate, load_thresholds

PASSING = {
    "rule_engine_coverage": 1.0,
    "route_mismatches": [],
    "scorers": {
        "citation_fidelity": {"mean": 1.0, "n": 27, "failing_case_ids": []},
        "no_invented_numbers": {"mean": 1.0, "n": 27, "failing_case_ids": []},
        "states_both_values": {"mean": 1.0, "n": 27, "failing_case_ids": []},
        "did_not_read_the_document": {"mean": 1.0, "n": 27, "failing_case_ids": []},
        "valid_structured_output": {"mean": 1.0, "n": 27, "failing_case_ids": []},
    },
}


def _aggregate(**overrides) -> dict:
    agg = json.loads(json.dumps(PASSING))
    scorers = overrides.pop("scorers", {})
    agg.update(overrides)
    agg["scorers"].update(scorers)
    return agg


def test_committed_thresholds_pass_the_recorded_baseline():
    """The thresholds shipped in evals/thresholds.json must accept the
    behaviour recorded in docs/evals/baseline-2026-09-04.md. If this fails,
    the gate would block every pull request including ones that change
    nothing."""
    passed, _ = evaluate(_aggregate(), load_thresholds())
    assert passed


def test_the_repaired_regression_would_be_caught():
    """The exact numbers from docs/evals/baseline-2026-08-29.md's headline
    finding, replayed. This is the regression the gate was built for; if the
    thresholds ever drift high enough to let 0.704 through, the gate has
    stopped protecting the project's one rule."""
    aggregate = _aggregate(
        scorers={
            "citation_fidelity": {
                "mean": 0.704,
                "n": 27,
                "failing_case_ids": ["ZL-004", "ZL-023", "ZL-026"],
            }
        }
    )
    passed, lines = evaluate(aggregate, load_thresholds())
    assert not passed
    assert any("FAIL" in line and "citation_fidelity" in line for line in lines)
    assert any("ZL-004" in line for line in lines), "a failure must name its cases"


def test_rule_engine_coverage_has_no_wobble_allowance():
    """Deterministic code. One mismatched case means the engine and the
    golden labels have diverged, which is never a tolerable amount."""
    passed, _ = evaluate(_aggregate(rule_engine_coverage=33 / 34), load_thresholds())
    assert not passed


def test_a_single_route_mismatch_fails():
    passed, _ = evaluate(_aggregate(route_mismatches=["ZL-007"]), load_thresholds())
    assert not passed


def test_a_missing_scorer_fails_rather_than_passing_silently():
    """A scorer that stopped running must not read as a pass. Deleting a
    scorer is exactly how a gate gets quietly defeated."""
    aggregate = _aggregate()
    del aggregate["scorers"]["citation_fidelity"]
    passed, lines = evaluate(aggregate, load_thresholds())
    assert not passed
    assert any("not measured" in line for line in lines)


def test_one_flaky_case_in_27_does_not_fail_the_gate():
    """temperature=0 is not a determinism guarantee on a hosted MoE endpoint
    (baseline-2026-08-22.md's ZL-018 finding). A gate that fires on
    single-case wobble teaches people to ignore it, so the prose scorers sit
    at 0.96 rather than 1.000."""
    aggregate = _aggregate(
        scorers={
            "citation_fidelity": {"mean": 26 / 27, "n": 27, "failing_case_ids": ["ZL-018"]}
        }
    )
    passed, _ = evaluate(aggregate, load_thresholds())
    assert passed


def test_two_failing_cases_in_27_do_fail_the_gate():
    """The other side of the boundary above: the allowance is one case, not
    an open-ended tolerance."""
    aggregate = _aggregate(
        scorers={
            "citation_fidelity": {
                "mean": 25 / 27,
                "n": 27,
                "failing_case_ids": ["ZL-018", "ZL-004"],
            }
        }
    )
    passed, _ = evaluate(aggregate, load_thresholds())
    assert not passed


def test_documentation_keys_are_stripped_from_thresholds():
    """thresholds.json carries its reasoning in "_"-prefixed keys, because a
    bare number tells a future reader nothing about whether it may be moved.
    Those must never reach the comparison as if they were metrics."""
    thresholds = load_thresholds()
    assert not any(k.startswith("_") for k in thresholds)
    assert not any(k.startswith("_") for k in thresholds["scorers"])
    assert isinstance(thresholds["scorers"]["citation_fidelity"], float)


# --- infrastructure failures are never a behaviour verdict ---------------


def test_a_rate_limit_is_inconclusive_not_a_regression():
    """Groq's free tier has a daily token ceiling as well as a per-minute one.
    Exhausting it mid-run made two cases fail valid_structured_output on 429s,
    which read as a quality regression and was nothing of the sort. Reporting
    infrastructure as behaviour trains people to re-run the gate until it goes
    green, which destroys the gate."""
    from evals.gate import infrastructure_errors

    records = [
        {"case_id": "NC-004", "error": "Error code: 429 - rate limit reached ... (TPD)"},
        {"case_id": "NC-005", "error": "Error code: 429 - rate limit reached ... (TPD)"},
        {"case_id": "ZL-001", "error": None},
    ]
    assert infrastructure_errors(records) == ["NC-004", "NC-005"]


def test_a_genuine_failure_is_not_treated_as_infrastructure():
    """The other side of the boundary: a real error must not be excused as a
    transport problem, or the gate can be silenced by a well-worded exception."""
    from evals.gate import infrastructure_errors

    records = [{"case_id": "ZL-004", "error": "schema validation failed: missing field"}]
    assert infrastructure_errors(records) == []


def test_transport_failures_are_recognised():
    from evals.gate import infrastructure_errors

    for error in ("Connection error.", "Read timed out", "Error code: 503"):
        assert infrastructure_errors([{"case_id": "X", "error": error}]) == ["X"], error

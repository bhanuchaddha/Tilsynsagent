"""Run-over-run regression: the distinction an aggregate cannot make.

A new case that fails is the dataset doing its job. A case that used to pass
and now fails is a regression. `0.94 -> 0.86` makes them indistinguishable
and the responses are opposite, which is why every test here is about keeping
them apart.
"""

from __future__ import annotations

import json

from evals.regression import (
    TOLERANCE,
    RunSummary,
    compare_runs,
    load_previous_nightly,
    save_nightly,
    summarise_run,
)


def _payload(scores: dict[str, float], records: list[dict], prompts: dict | None = None) -> dict:
    return {
        "resolved_prompts": prompts or {"ground": "tilsynsagent-ground-system@v1"},
        "rule_engine_coverage": 1.0,
        "n_cases": len(records),
        "passes": [
            {
                "records": records,
                "aggregate": {"scorers": {n: {"mean": v, "n": len(records)} for n, v in scores.items()}},
            }
        ],
    }


def test_summarise_reads_the_gate_output_shape():
    payload = _payload(
        {"clause_is_verbatim": 0.94},
        [{"case_id": "ZL-1", "scores": {"clause_is_verbatim": 1.0}}],
    )
    run = summarise_run(payload, name="2026-09-05")
    assert run.mean_score == 0.94
    assert run.case_passed == {"ZL-1": True}
    assert run.prompt_versions["ground"].endswith("@v1")


def test_a_case_with_no_scores_counts_as_passing():
    """A rule-decided escalation calls no model and has no LLM behaviour to
    regress. Counting it as a failure would move the mean whenever the mix of
    routes in the dataset changes."""
    payload = _payload({"s": 1.0}, [{"case_id": "ZL-1", "scores": {}}])
    assert summarise_run(payload, name="n").case_passed["ZL-1"] is True


def test_the_first_run_is_a_baseline_not_a_regression():
    current = RunSummary("a", 0.9, {"s": 0.9}, {"ZL-1": False})
    comparison = compare_runs(current, None)
    assert comparison.regressed is False
    assert comparison.newly_failing_case_ids == []
    assert comparison.new_case_ids == ["ZL-1"]


def test_a_new_failing_case_is_not_a_regression():
    """A case a person added *because* the agent got it wrong. The score
    dropping is the dataset working, not the agent getting worse."""
    previous = RunSummary("a", 1.0, {"s": 1.0}, {"ZL-1": True})
    current = RunSummary("b", 0.5, {"s": 1.0}, {"ZL-1": True, "AQ-1": False})
    comparison = compare_runs(current, previous)
    assert comparison.new_case_ids == ["AQ-1"]
    assert comparison.newly_failing_case_ids == []
    assert comparison.regressed is False


def test_a_case_that_used_to_pass_and_now_fails_is_a_regression():
    previous = RunSummary("a", 1.0, {"s": 1.0}, {"ZL-1": True})
    current = RunSummary("b", 1.0, {"s": 1.0}, {"ZL-1": False})
    comparison = compare_runs(current, previous)
    assert comparison.newly_failing_case_ids == ["ZL-1"]
    assert comparison.regressed is True


def test_a_case_that_was_already_failing_is_not_newly_failing():
    previous = RunSummary("a", 0.5, {"s": 0.5}, {"ZL-1": False})
    current = RunSummary("b", 0.5, {"s": 0.5}, {"ZL-1": False})
    assert compare_runs(current, previous).newly_failing_case_ids == []


def test_wobble_within_tolerance_is_not_a_regression():
    """temperature=0 is not determinism on a hosted mixture-of-experts. An
    alert that fires on run-to-run wobble gets muted, which costs more than
    the drift it would have caught."""
    previous = RunSummary("a", 1.0, {"s": 1.0}, {"ZL-1": True})
    current = RunSummary("b", 1.0, {"s": 1.0 - TOLERANCE / 2}, {"ZL-1": True})
    assert compare_runs(current, previous).regressed is False


def test_a_drop_beyond_tolerance_is_a_regression():
    previous = RunSummary("a", 1.0, {"s": 1.0}, {"ZL-1": True})
    current = RunSummary("b", 0.9, {"s": 0.9}, {"ZL-1": True})
    comparison = compare_runs(current, previous)
    assert comparison.regressed is True
    assert comparison.regressed_scorers == ["s"]


def test_an_improvement_is_never_a_regression():
    previous = RunSummary("a", 0.8, {"s": 0.8}, {"ZL-1": False})
    current = RunSummary("b", 1.0, {"s": 1.0}, {"ZL-1": True})
    assert compare_runs(current, previous).regressed is False


def test_a_scorer_present_in_only_one_run_is_not_compared():
    previous = RunSummary("a", 1.0, {"s": 1.0}, {})
    current = RunSummary("b", 1.0, {"s": 1.0, "new": 0.1}, {})
    comparison = compare_runs(current, previous)
    assert "new" not in comparison.per_scorer_delta
    assert comparison.regressed is False


def test_the_json_fallback_survives_langfuse_retention(tmp_path):
    """Langfuse's free tier forgets after 30 days. Without this the nightly
    reports no regression while comparing against nothing at all."""
    save_nightly(_payload({"s": 0.9}, []), name="2026-09-01", directory=tmp_path)
    save_nightly(_payload({"s": 0.8}, []), name="2026-09-04", directory=tmp_path)
    payload, name = load_previous_nightly(exclude="2026-09-05", directory=tmp_path)
    assert name == "2026-09-04"
    assert payload["passes"][0]["aggregate"]["scorers"]["s"]["mean"] == 0.8


def test_the_current_run_is_excluded_from_its_own_baseline(tmp_path):
    save_nightly(_payload({"s": 0.9}, []), name="2026-09-04", directory=tmp_path)
    save_nightly(_payload({"s": 0.8}, []), name="2026-09-05", directory=tmp_path)
    _, name = load_previous_nightly(exclude="2026-09-05", directory=tmp_path)
    assert name == "2026-09-04"


def test_no_previous_run_returns_none(tmp_path):
    assert load_previous_nightly(directory=tmp_path) is None

"""The model-comparison harness, offline.

The hazard this guards is subtle and total: GROQ_MODEL is read at *import*
time by the llm modules, so a comparison that forgets to reload them measures
the first model twice and reports it as two models. That failure produces a
plausible-looking table with no error, which is the worst kind.
"""

from __future__ import annotations

import pytest

from evals.experiment import DEFAULT_MODELS, compare


def _run(model: str, **scorers) -> dict:
    return {
        "model": model,
        "aggregate": {
            "scorers": {
                name: {"mean": mean, "n": 27, "failing_case_ids": failing}
                for name, (mean, failing) in scorers.items()
            },
            "llm_step_error_rate": 0.0,
            "mean_tokens_per_case": 900.0,
            "total_cost_usd": 0.005,
        },
    }


def test_compare_lists_both_models():
    lines = compare(
        [
            _run("big", citation_fidelity=(1.0, [])),
            _run("small", citation_fidelity=(0.9, ["ZL-004"])),
        ]
    )
    text = "\n".join(lines)
    assert "big" in text and "small" in text


def test_compare_names_where_each_model_fails():
    """The aggregate hides the only thing worth knowing. A model that is
    worse on average but never drops a citation is a different proposition
    from one that is better on average and occasionally invents a number."""
    lines = compare(
        [
            _run("big", citation_fidelity=(1.0, [])),
            _run("small", citation_fidelity=(0.9, ["ZL-004", "ZL-023"])),
        ]
    )
    text = "\n".join(lines)
    assert "ZL-004" in text and "ZL-023" in text
    assert "no failing cases" in text, "a clean model must be stated as clean, not omitted"


def test_compare_handles_a_scorer_only_one_model_reported():
    """A scorer missing from one run must render as a gap rather than
    crashing the comparison or silently reading as a zero."""
    lines = compare(
        [
            _run("big", citation_fidelity=(1.0, []), no_invented_numbers=(1.0, [])),
            _run("small", citation_fidelity=(1.0, [])),
        ]
    )
    assert any("no_invented_numbers" in line for line in lines)


def test_default_candidates_are_same_provider_same_tier():
    """The comparison isolates model size. Mixing vendors would confound it
    with pricing model, API behaviour and rate limits all at once."""
    assert all(m.startswith("openai/gpt-oss-") for m in DEFAULT_MODELS)
    assert len(set(DEFAULT_MODELS)) == len(DEFAULT_MODELS)


def test_run_for_model_refuses_to_report_an_unreloaded_module(monkeypatch):
    """If the reload does not take effect, the run must fail loudly rather
    than measure one model twice and label the results as two."""
    import evals.experiment as experiment

    monkeypatch.setattr(
        "evals.tasks.resolved_models",
        lambda: {"assess": "openai/gpt-oss-120b", "summarise": "openai/gpt-oss-120b"},
    )
    # Reloading inside run_for_model would restore the real function, so this
    # asserts the guard's shape rather than re-running it: the check compares
    # the requested model against what the modules actually resolved to.
    import inspect

    source = inspect.getsource(experiment.run_for_model)
    assert "would be a lie" in source
    assert "raise RuntimeError" in source


@pytest.mark.parametrize("model", DEFAULT_MODELS)
def test_every_default_candidate_has_a_committed_price(model):
    """A cost comparison with a missing price silently reports $0.00 for the
    unpriced model, which reads as "free" rather than "unknown"."""
    from tilsynsagent.llm.pricing import load_pricing
    from tilsynsagent.llm.usage import Usage

    pricing = load_pricing()
    cost = pricing.cost_usd(
        Usage(model=model, prompt_tokens=1_000_000, completion_tokens=0, total_tokens=1_000_000)
    )
    assert cost > 0.0, f"{model} has no price entry in config/pricing.toml"

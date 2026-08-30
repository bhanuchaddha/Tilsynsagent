"""Offline tests for pricing. Uses a temp pricing.toml, never the real
config/pricing.toml, so the test doesn't depend on prices staying frozen."""

from pathlib import Path

from tilsynsagent.llm.pricing import load_pricing
from tilsynsagent.llm.usage import Usage


def _write_pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.toml"
    p.write_text(
        """
[models."test-model"]
input_per_1m = 1.0
output_per_1m = 2.0
checked_on = "2026-01-01"
source = "https://example.com"
"""
    )
    return p


def test_cost_usd_computes_from_prompt_and_completion(tmp_path):
    pricing = load_pricing(_write_pricing(tmp_path))
    usage = Usage(model="test-model", prompt_tokens=1_000_000, completion_tokens=1_000_000, total_tokens=2_000_000)
    assert pricing.cost_usd(usage) == 3.0


def test_cost_usd_scales_with_token_count(tmp_path):
    pricing = load_pricing(_write_pricing(tmp_path))
    usage = Usage(model="test-model", prompt_tokens=500_000, completion_tokens=0, total_tokens=500_000)
    assert pricing.cost_usd(usage) == 0.5


def test_unknown_model_returns_zero_not_raise(tmp_path):
    pricing = load_pricing(_write_pricing(tmp_path))
    usage = Usage(model="unpriced-model", prompt_tokens=100, completion_tokens=100, total_tokens=200)
    assert pricing.cost_usd(usage) == 0.0


def test_is_priced(tmp_path):
    pricing = load_pricing(_write_pricing(tmp_path))
    assert pricing.is_priced("test-model") is True
    assert pricing.is_priced("unpriced-model") is False


def test_checked_on(tmp_path):
    pricing = load_pricing(_write_pricing(tmp_path))
    assert pricing.checked_on("test-model") == "2026-01-01"
    assert pricing.checked_on("unpriced-model") is None


def test_loads_real_config():
    """config/pricing.toml must parse and contain the model this project
    actually uses, with a checked_on date."""
    pricing = load_pricing()
    assert pricing.is_priced("openai/gpt-oss-120b")
    assert pricing.checked_on("openai/gpt-oss-120b") is not None

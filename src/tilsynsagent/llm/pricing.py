"""Applies config/pricing.toml to a Usage. Prices are data, not code: adding a
model or reacting to a price change is a config edit, no deploy required.

Groq publishes no machine-readable price feed, so every price in the config
is a hand-checked number with a ``checked_on`` date - a cost figure whose
price basis is undocumented is not traceable, which is the project's one
rule applied to money. An unpriced model must never break a run, so
``cost_usd`` returns 0.0 and logs a warning rather than raising.
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path

from tilsynsagent.llm.usage import Usage

logger = logging.getLogger("tilsynsagent.llm.pricing")

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRICING_PATH = REPO_ROOT / "config" / "pricing.toml"


class Pricing:
    def __init__(self, models: dict[str, dict]):
        self._models = models

    def is_priced(self, model: str) -> bool:
        return model in self._models

    def checked_on(self, model: str) -> str | None:
        entry = self._models.get(model)
        return entry["checked_on"] if entry else None

    def cost_usd(self, usage: Usage) -> float:
        entry = self._models.get(usage.model)
        if entry is None:
            logger.warning(
                "no price entry for model %r in %s - reporting cost as 0.0",
                usage.model,
                DEFAULT_PRICING_PATH,
            )
            return 0.0
        input_cost = (usage.prompt_tokens / 1_000_000) * entry["input_per_1m"]
        output_cost = (usage.completion_tokens / 1_000_000) * entry["output_per_1m"]
        return input_cost + output_cost


def load_pricing(path: Path | None = None) -> Pricing:
    """Reads config/pricing.toml via stdlib tomllib (Python 3.12, no new
    dependency). Path overridable by PRICING_CONFIG_PATH, mainly for tests."""
    if path is None:
        env_path = os.environ.get("PRICING_CONFIG_PATH")
        path = Path(env_path) if env_path else DEFAULT_PRICING_PATH
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Pricing(data.get("models", {}))

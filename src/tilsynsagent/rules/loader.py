"""Reads docs/rules.md at runtime.

The rule set is a document, not a constant baked into code or a prompt. A
second rule set - a new version, a different register - is a config change:
point RULES_PATH elsewhere and restart. The rule *engine* (engine.py) still
needs a matching code change when the rules themselves change; what this
module keeps out of code is the rule set's prose, so the "what this rule set
does not cover" section and the precedence explanation reach the LLM assessment
step and docs verbatim, from one source of truth.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RULES_PATH = REPO_ROOT / "docs" / "rules.md"


def load_rules_text(path: Path | str = DEFAULT_RULES_PATH) -> str:
    """The full rule set document, as the LLM assessment step and docs see it."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Rule set not found at {path}. The agent must not guess a rule set - "
            "point it at a real document."
        )
    return path.read_text(encoding="utf-8")


def load_not_covered_section(path: Path | str = DEFAULT_RULES_PATH) -> str:
    """Just the '## What this rule set does not cover' section.

    Used to prime the assessment step with the exact boundary the rule set
    states for itself, rather than letting the model infer where its job
    starts.
    """
    text = load_rules_text(path)
    marker = "## What this rule set does not cover"
    start = text.find(marker)
    if start == -1:
        raise ValueError(f"'{marker}' section not found in {path}")
    end = text.find("\n## ", start + len(marker))
    section = text[start:end] if end != -1 else text[start:]
    return section.strip()

"""Reads the rule set document at runtime.

The rule set is a document, not a constant baked into code or a prompt.
Pointing RULES_PATH elsewhere and restarting switches rule sets with no code
change.
"""

from __future__ import annotations

from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent
DEFAULT_RULES_PATH = RULES_DIR / "rules.md"


def load_rules_text(path: Path | str = DEFAULT_RULES_PATH) -> str:
    """The full rule set document."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Rule set not found at {path}.")
    return path.read_text(encoding="utf-8")


def load_not_covered_section(path: Path | str = DEFAULT_RULES_PATH) -> str:
    """Just the '## What this rule set does not cover' section, used to
    prime the assessment step with the rule set's own stated boundary."""
    text = load_rules_text(path)
    marker = "## What this rule set does not cover"
    start = text.find(marker)
    if start == -1:
        raise ValueError(f"'{marker}' section not found in {path}")
    end = text.find("\n## ", start + len(marker))
    section = text[start:end] if end != -1 else text[start:]
    return section.strip()

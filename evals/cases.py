"""Loads golden-dataset cases for the eval runner.

Two files, never merged: evals/golden/cases.jsonl (the 34 real, hand-labelled
transitions - see evals/golden/README.md's "Provenance") and
evals/golden/not_covered_cases.jsonl (a small synthetic set that exists only
to give the assess() step any coverage at all - see that file's own header).
tests/test_rules_engine.py globs cases.jsonl only, so it stays blind to the
synthetic set; this module is the only place that reads both.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
CASES_PATH = GOLDEN_DIR / "cases.jsonl"
NOT_COVERED_CASES_PATH = GOLDEN_DIR / "not_covered_cases.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_golden_cases() -> list[dict]:
    """The 34 real, hand-labelled transitions."""
    return _load_jsonl(CASES_PATH)


def load_not_covered_cases() -> list[dict]:
    """The synthetic NOT_COVERED set - see that file's header for why it
    exists and why it is temporary."""
    return _load_jsonl(NOT_COVERED_CASES_PATH)


def load_all_cases() -> list[dict]:
    """Every case the eval runner exercises: the 34 real cases plus the
    synthetic NOT_COVERED set, concatenated only here - never on disk."""
    return load_golden_cases() + load_not_covered_cases()


def expected_route(case: dict) -> Literal["file", "escalate", "not_covered"]:
    """The route a case is expected to take through the graph. For the
    founding 34, this is the golden label directly. Synthetic NOT_COVERED cases carry
    label "escalate" (their real-world outcome, since NOT_COVERED always
    escalates - see rules/engine.py's Outcome.NOT_COVERED docstring) but are
    tagged with origin "synthetic-not-covered" so the eval task layer can
    route them through assess() instead of summarise()."""
    if case.get("origin") == "synthetic-not-covered":
        return "not_covered"
    if case.get("origin") == "escalation-derived":
        # The route the *graph* takes, which is not the same as the label.
        # These cases were escalated by the engine and then given a label by a
        # person - often one the engine disagrees with, which is the whole
        # reason they are worth keeping. Their expected route is therefore
        # still "escalate": that is what the system does with them, and what a
        # route check must compare against. The human's label lives in
        # case["label"] and is what the *resolution* was, not what the run did.
        return "escalate"
    return case["label"]


def dataset_item_id(case: dict) -> str:
    """The Langfuse dataset item id for a case. Returns case["id"] verbatim
    (e.g. "ZL-001") so create_dataset_item(id=...) upserts idempotently -
    running sync_dataset.py twice must not double the item count."""
    return case["id"]


def iter_by_route(
    cases: list[dict], route: Literal["file", "escalate", "not_covered"]
) -> Iterator[dict]:
    return (c for c in cases if expected_route(c) == route)

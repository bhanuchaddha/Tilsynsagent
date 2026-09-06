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

# The routes a case can take through the graph. "grounded_file" and
# "grounded_ignore" are the two the ground node reaches on its own; both are
# only reachable from a case the rule engine returned NOT_COVERED on.
Route = Literal["file", "escalate", "not_covered", "grounded_file", "grounded_ignore"]

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
CASES_PATH = GOLDEN_DIR / "cases.jsonl"
NOT_COVERED_CASES_PATH = GOLDEN_DIR / "not_covered_cases.jsonl"
# Golden set v2: the same five real records as the NOT_COVERED set, carrying
# the outcome the *ground* node is expected to reach once it has opened the
# document, and the clause that must justify it. The v1 set stops at "the
# rules correctly declined"; this set asserts what happens next.
GROUNDING_CASES_PATH = GOLDEN_DIR / "grounding_cases.jsonl"


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


def load_grounding_cases() -> list[dict]:
    """Golden set v2 - the grounded outcomes. Each case names the clause the
    ground node must cite, so a decision that reaches the right outcome for
    the wrong reason still fails."""
    return _load_jsonl(GROUNDING_CASES_PATH)


def load_all_cases() -> list[dict]:
    """Every case the eval runner exercises: the 34 real cases plus the
    synthetic NOT_COVERED set, concatenated only here - never on disk."""
    return load_golden_cases() + load_not_covered_cases()


def expected_route(case: dict) -> Route:
    """The route a case is expected to take through the graph. For the
    founding 34, this is the golden label directly. Synthetic NOT_COVERED cases carry
    label "escalate" (their real-world outcome, since NOT_COVERED always
    escalates - see rules/engine.py's Outcome.NOT_COVERED docstring) but are
    tagged with origin "synthetic-not-covered" so the eval task layer can
    route them through assess() instead of summarise()."""
    if case.get("origin") == "annotation-derived":
        # Promoted from a Langfuse annotation queue (evals/promote.py): a
        # production run a person looked at and labelled. The label *is* the
        # route, because these cases are only ever created from grounded runs
        # - the person was shown what the agent decided autonomously and said
        # whether it was right. An "escalate" label here means the person
        # judged that the agent should not have decided it at all.
        label = case["label"]
        if label in ("file", "ignore"):
            return f"grounded_{label}"  # type: ignore[return-value]
        return "escalate"
    if case.get("origin") == "grounding-v2":
        # Golden set v2. The label is the grounded outcome, so it maps onto
        # the grounded_* routes directly; "escalate" means the document could
        # settle nothing and a person must look.
        label = case["label"]
        if label in ("file", "ignore"):
            return f"grounded_{label}"  # type: ignore[return-value]
        return "escalate"
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
    if case["label"] == "ignore":
        # An ignore label on a hand-labelled case means the document settles
        # it, and only the ground node can reach that conclusion - the rule
        # engine has no path to 'ignore' at all (docs/rules.md, "The two
        # outcomes"). Before grounding existed this was unreachable and
        # review/app.py refused to write such a case; now it names a real
        # route.
        return "grounded_ignore"
    return case["label"]


def dataset_item_id(case: dict) -> str:
    """The Langfuse dataset item id for a case. Returns case["id"] verbatim
    (e.g. "ZL-001") so create_dataset_item(id=...) upserts idempotently -
    running sync_dataset.py twice must not double the item count."""
    return case["id"]


def iter_by_route(cases: list[dict], route: Route) -> Iterator[dict]:
    return (c for c in cases if expected_route(c) == route)

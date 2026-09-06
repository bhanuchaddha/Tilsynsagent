"""Data shaping for the review UI, with no Streamlit in it.

**Why this module exists.** Streamlit executes the whole script top to bottom
on every interaction, including every tab body whether or not it is visible,
which means two things constrain anything added to `review/app.py`:

1. Queries must stay narrow, because they all run on every rerun.
2. Logic in a `st.*`-shaped function cannot be tested - this repo has no way
   to render Streamlit components, so anything that matters must live where a
   plain unit test can reach it.

So every non-trivial decision the new tabs make - what counts as a
regression, which alerts are open, how a grounding rate reads in words - is a
pure function here, and `app.py` only arranges the results on screen. Same
pattern as `db/repo.py`'s `status_counts`, for the same reason.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
NIGHTLY_DIR = REPO_ROOT / "docs" / "evals" / "nightly"


def langfuse_trace_url(base_url: str | None, trace_id: str) -> str | None:
    """A deep link to one trace in the Langfuse UI.

    Returns None without a base URL rather than guessing at one: a link that
    404s in front of an audience is worse than a plain trace id they can paste
    into a search box.
    """
    if not base_url or not trace_id:
        return None
    return f"{base_url.rstrip('/')}/trace/{trace_id}"


def langfuse_queue_url(base_url: str | None, queue_url: str | None = None) -> str | None:
    """The annotation queue link shown to a business reviewer."""
    if queue_url:
        return queue_url
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/annotation-queues"


def grounding_rate_sentence(stats: dict) -> str:
    """The grounding rate, in words rather than as a bare fraction.

    A percentage on its own invites the wrong reading in both directions: a
    low number looks like failure when abstention is the safe behaviour, and a
    high one looks like success when it might mean the agent has stopped
    escalating things it should. The sentence names both halves.
    """
    total = stats.get("uncovered_total") or 0
    if not total:
        return (
            "No uncovered cases yet. Grounding only runs on changes the written "
            "rules do not cover, so this stays at zero until one arrives."
        )
    grounded = stats.get("grounded_total") or 0
    rate = stats.get("grounding_rate") or 0.0
    return (
        f"Of {total} change(s) the written rules did not cover, the agent settled "
        f"{grounded} ({rate:.0%}) by reading the plan document, and handed "
        f"{total - grounded} to a person. Both halves are correct behaviour: the "
        f"second is what it is supposed to do when the document does not answer."
    )


def load_nightly_runs(*, directory: Path | None = None, limit: int = 10) -> list[dict]:
    """The committed nightly run records, newest first.

    Filenames are ISO dates, so lexical ordering is chronological.
    """
    directory = directory or NIGHTLY_DIR
    if not directory.exists():
        return []
    runs = []
    for path in sorted(directory.glob("*.json"), reverse=True)[:limit]:
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        runs.append({"name": path.stem, "payload": payload, "path": str(path)})
    return runs


def latest_comparison(*, directory: Path | None = None):
    """The most recent nightly compared with the one before it.

    Returns a ``Comparison`` (evals/regression.py) or None when fewer than two
    runs exist. This is what the Developer tab renders: the drop, which case
    is new, and which case newly fails - the distinction an aggregate cannot
    make and the developer workflow depends on.
    """
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from evals.regression import compare_runs, summarise_run

    runs = load_nightly_runs(directory=directory, limit=2)
    if len(runs) < 2:
        return None
    current = summarise_run(runs[0]["payload"], name=runs[0]["name"])
    previous = summarise_run(runs[1]["payload"], name=runs[1]["name"])
    return compare_runs(current, previous)


def flagged_grounded_runs(groundings: list[dict]) -> list[dict]:
    """Grounded decisions whose evidence fails a code check, re-run here.

    The Business tab shows these as cards. Re-scored from the stored row
    rather than read back from Langfuse, so the tab works with no vendor
    reachable and renders in one query - see this module's docstring on
    Streamlit reruns.

    ``clause_is_verbatim`` is not among the checks run here: verifying a quote
    needs the document text, which means a fetch per card, and a tab body that
    executes on every rerun must not do that. Those failures reach this screen
    through the alert file and the queue instead.
    """
    from tilsynsagent.obs.grounded import (
        clause_id_exists,
        quote_mentions_the_changed_field,
    )

    flagged = []
    for row in groundings:
        retrieved = row.get("retrieved_clause_ids") or []
        if isinstance(retrieved, str):
            retrieved = json.loads(retrieved)
        scores = [
            clause_id_exists(clause_id=row["clause_id"], retrieved_clause_ids=retrieved),
            quote_mentions_the_changed_field(
                clause_quote=row["clause_quote"], changed_fields=row.get("changed_fields") or {}
            ),
        ]
        failing = [s for s in scores if s.value < 1.0]
        if failing:
            flagged.append({**row, "failing": [(s.name, s.comment) for s in failing]})
    return flagged


def alert_summary(alerts: list[dict]) -> dict:
    """Counts for the operator view: how many alerts, how many are demo."""
    return {
        "total": len(alerts),
        "real": sum(1 for a in alerts if not a["is_demo"]),
        "demo": sum(1 for a in alerts if a["is_demo"]),
        "recurring": sum(1 for a in alerts if a["recurrences"]),
    }


def document_cache_rate(groundings: list[dict]) -> str:
    """A sentence about what grounding costs to run.

    Reported as prose rather than a metric because the interesting fact is
    not the number, it is that a 30 MB document fetch per record is the real
    operating cost of grounding and caching is what makes it affordable.
    """
    if not groundings:
        return "No documents read yet."
    pages = [g["document_page_count"] for g in groundings if g.get("document_page_count")]
    if not pages:
        return f"{len(groundings)} document(s) read."
    return (
        f"{len(groundings)} document(s) read, averaging {sum(pages) / len(pages):.0f} pages. "
        f"Cached by sha256 of the document URL; since the register embeds a timestamp in "
        f"that URL, a revised document gets a new cache key with no invalidation logic."
    )

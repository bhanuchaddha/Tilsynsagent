"""Data shaping for the review UI, with no Streamlit in it.

Logic that needs to be unit-tested lives here as plain functions; `app.py`
only arranges the results on screen.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
NIGHTLY_DIR = REPO_ROOT / "var" / "evals" / "nightly"


def langfuse_trace_url(base_url: str | None, trace_id: str) -> str | None:
    """A deep link to one trace in the Langfuse UI. Returns None without a
    base URL rather than guessing at one."""
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


def flagged_grounded_runs(groundings: list[dict], *, document_text=None) -> list[dict]:
    """Grounded decisions whose evidence fails a code check, re-run here.

    The Business tab shows these as cards. Re-scored from the stored row
    rather than read back from Langfuse, so the tab works with no vendor
    reachable and renders in one query - see this module's docstring on
    Streamlit reruns.

    ``document_text`` is a callable taking a doklink and returning that
    document's text, or None to skip every check that needs one. It exists
    because stage 1 moved the clause checks from a retrieved subset to the
    full document, so ``clause_id_exists`` now needs the source. The caller
    supplies a cached reader (review/app.py memoises one per render) rather
    than this module fetching per card: a tab body executes on every
    Streamlit rerun and a 30 MB fetch per card would make the screen unusable.

    With no reader, clause-cited rows are still checked for structural
    completeness and for whether the quote is about the changed field, and
    field-cited rows are fully checked against the stored record - those need
    nothing but the row. What is lost without a reader is fabrication
    detection on clause citations, which is worth being explicit about rather
    than quietly dropping.
    """
    from tilsynsagent.obs.grounded import (
        abstained_when_ungrounded,
        clause_id_exists,
        clause_is_verbatim,
        field_citation_is_real,
        quote_mentions_the_changed_field,
    )

    flagged = []
    for row in groundings:
        citation_kind = row.get("citation_kind") or "clause"
        scores = [
            abstained_when_ungrounded(
                grounded=True,
                outcome=row.get("outcome") or "",
                citation_kind=citation_kind,
                clause_id=row.get("clause_id") or "",
                clause_quote=row.get("clause_quote") or "",
                field_name=row.get("field_name") or "",
                field_before=row.get("field_before") or "",
                field_after=row.get("field_after") or "",
            )
        ]
        if citation_kind == "clause":
            scores.append(
                quote_mentions_the_changed_field(
                    clause_quote=row.get("clause_quote") or "",
                    changed_fields=row.get("changed_fields") or {},
                )
            )
            if document_text is not None:
                text = document_text(row.get("doklink") or "")
                scores.extend(
                    [
                        clause_id_exists(
                            clause_id=row.get("clause_id") or "", document_text=text
                        ),
                        clause_is_verbatim(
                            clause_quote=row.get("clause_quote") or "", document_text=text
                        ),
                    ]
                )
        elif citation_kind == "field":
            scores.append(
                field_citation_is_real(
                    field_name=row.get("field_name") or "",
                    field_before=row.get("field_before") or "",
                    field_after=row.get("field_after") or "",
                    before=_as_dict(row.get("before")),
                    after=_as_dict(row.get("after")),
                )
            )
        failing = [s for s in scores if s.value < 1.0]
        if failing:
            flagged.append({**row, "failing": [(s.name, s.comment) for s in failing]})
    return flagged


def _as_dict(value) -> dict:
    """A JSONB column that psycopg may hand back as a dict or as a string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value or {}


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

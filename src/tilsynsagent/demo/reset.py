"""Deletes demo data created by demo/seed.py: every sub_area marked
is_test_data=True, and everything hanging off it (versions, diffs, filings,
escalations, escalation_resolutions), plus the LangGraph checkpoints for
every thread that seeding created.

Live rows are never touched - the WHERE clause only ever matches
sub_areas.is_test_data (migrations/003_demo_data.sql), which demo/seed.py is
the only writer of. Safe to run mid-demo and re-seed immediately after.
"""

from __future__ import annotations

import logging
import os

import psycopg
from dotenv import load_dotenv

logger = logging.getLogger("tilsynsagent.demo.reset")

# Matches demo/seed.py's thread id scheme (f"demo-{case_id}-v{n}") exactly.
# Deliberately not derived from the escalations table: seed.py opens a
# checkpoint thread for *every* case it runs (the "before" version's
# first-sighting skip, and a filed case's "after" run), not only the ones
# that end up escalated - a join through escalations would leave those
# orphaned after reset.
_CHECKPOINT_THREAD_PATTERN = "demo-%"


def reset_demo(database_url: str | None = None) -> dict:
    """Deletes all is_test_data=True rows and their LangGraph checkpoints.
    Returns counts of what was removed."""
    database_url = database_url or os.environ["DATABASE_URL"]
    counts = {
        "sub_areas": 0,
        "checkpoint_threads": 0,
        "demo_documents": 0,
        "cached_documents": 0,
        "demo_alerts": 0,
    }

    with psycopg.connect(database_url) as conn, conn.transaction():
        thread_rows = conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE %s",
            (_CHECKPOINT_THREAD_PATTERN,),
        ).fetchall()
        thread_ids = [r[0] for r in thread_rows]

        conn.execute(
            """
            DELETE FROM escalation_resolutions WHERE escalation_id IN (
                SELECT e.id FROM escalations e
                JOIN diffs d ON d.id = e.diff_id
                JOIN sub_areas s ON s.id = d.sub_area_id
                WHERE s.is_test_data
            )
            """
        )
        conn.execute(
            """
            DELETE FROM escalations WHERE diff_id IN (
                SELECT d.id FROM diffs d
                JOIN sub_areas s ON s.id = d.sub_area_id
                WHERE s.is_test_data
            )
            """
        )
        # groundings before diffs, and before filings only because they are
        # independent - the FK is groundings.diff_id, so every grounding must
        # go before the diff it hangs off.
        conn.execute(
            """
            DELETE FROM groundings WHERE diff_id IN (
                SELECT d.id FROM diffs d
                JOIN sub_areas s ON s.id = d.sub_area_id
                WHERE s.is_test_data
            )
            """
        )
        conn.execute(
            """
            DELETE FROM filings WHERE diff_id IN (
                SELECT d.id FROM diffs d
                JOIN sub_areas s ON s.id = d.sub_area_id
                WHERE s.is_test_data
            )
            """
        )
        conn.execute(
            """
            DELETE FROM diffs WHERE sub_area_id IN (
                SELECT id FROM sub_areas WHERE is_test_data
            )
            """
        )
        conn.execute(
            """
            DELETE FROM sub_area_versions WHERE sub_area_id IN (
                SELECT id FROM sub_areas WHERE is_test_data
            )
            """
        )
        result = conn.execute("DELETE FROM sub_areas WHERE is_test_data")
        counts["sub_areas"] = result.rowcount

        # LangGraph's PostgresSaver tables (checkpoints, checkpoint_writes,
        # checkpoint_blobs) key on thread_id, not on this schema's tables -
        # cleared separately so a reset also frees a paused run's thread,
        # not just the escalation row that pointed at it.
        for thread_id in thread_ids:
            conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (thread_id,))
            conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (thread_id,))
            conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s", (thread_id,))
        counts["checkpoint_threads"] = len(thread_ids)

    # Everything below is on disk rather than in the database, and is cleared
    # after the transaction commits: a failure here leaves stale files, which
    # a re-run fixes, whereas rolling back the database delete to protect a
    # file cleanup would leave demo rows in the register.
    counts.update(_reset_files())

    logger.info("reset complete: %s", counts)
    return counts


def _reset_files() -> dict:
    """Clears the on-disk demo artefacts: generated documents, their cache
    entries, and demo alert files.

    Demo alerts are deleted by *filename*, not by reading their contents:
    obs/alerts.py names a demo alert with a `demo-` prefix precisely so this
    function can tell them apart from real alerts without parsing. The
    committed alert record in docs/alerts/ is a permanent artefact of
    this project and a reset must never be able to touch it - which means
    "delete the alerts" has to be a decision made by a naming scheme agreed
    when the alert is written, not a judgement made at deletion time.
    """
    from tilsynsagent.demo.documents import clear_demo_documents, demo_document_dir
    from tilsynsagent.documents.cache import clear_cache

    documents = list(demo_document_dir().glob("*.pdf"))
    doklinks = [p.resolve().as_uri() for p in documents]
    counts = {
        "cached_documents": clear_cache(prefix_urls=doklinks),
        "demo_documents": clear_demo_documents(),
    }

    from tilsynsagent.obs.alerts import clear_demo_alerts

    counts["demo_alerts"] = clear_demo_alerts()
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    reset_demo()


if __name__ == "__main__":
    main()

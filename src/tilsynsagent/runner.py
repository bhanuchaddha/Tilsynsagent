"""Drives one unattended run: fetch everything new since the watermark, run
the graph once per changed sub-area version, advance the watermark.

The watermark is read from and written to the database (repo.get_watermark /
save_watermark), not the local WatermarkStore file, so a scheduled run picks
up where the last one left off regardless of which machine ran it - the
in-memory WatermarkStore class in sources/plandata.py is used to do the
comparison, just seeded from and flushed back to Postgres instead of disk.

One graph thread per sub-area version: thread_id is deterministic
(f"{lokplan_id}-{delnr}-{versionsnr}") so a crashed or interrupted run can be
safely re-invoked for the same record without creating a duplicate thread.
"""

from __future__ import annotations

import logging
import os

import psycopg
from dotenv import load_dotenv
from langgraph.types import Interrupt

from tilsynsagent.db import repo
from tilsynsagent.db.migrate import run_migrations
from tilsynsagent.graph import make_graph, run_input
from tilsynsagent.sources.plandata import PlandataClient, WatermarkStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tilsynsagent.runner")


def _thread_id_for(record) -> str:
    return f"{record.lokplan_id}-{record.delnr}-{record.versionsnr}"


def run_once(database_url: str | None = None) -> dict:
    """One unattended pass: fetch new records since the watermark, run the
    graph on each, advance and persist the watermark. Returns counts."""
    database_url = database_url or os.environ["DATABASE_URL"]
    run_migrations(database_url)

    with psycopg.connect(database_url) as conn:
        last_datoopdt, seen_at_watermark = repo.get_watermark(conn)

    store = WatermarkStore(last_datoopdt=last_datoopdt, seen_at_watermark=set(seen_at_watermark))

    counts = {"fetched": 0, "filed": 0, "escalated": 0, "ignored": 0, "errors": 0}
    graph, saver_cm = make_graph(database_url)
    try:
        graph.checkpointer.setup()
        with PlandataClient() as client:
            for record in store.fetch_new(client):
                counts["fetched"] += 1
                thread_id = _thread_id_for(record)
                config = {"configurable": {"thread_id": thread_id}}
                try:
                    result = graph.invoke(run_input(record), config=config)
                except Exception:
                    logger.exception(
                        "run failed for %s-%s-v%s", record.lokplan_id, record.delnr, record.versionsnr
                    )
                    counts["errors"] += 1
                    continue

                interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
                if interrupts:
                    counts["escalated"] += 1
                    logger.info(
                        "escalated %s-%s-v%s: %s",
                        record.lokplan_id,
                        record.delnr,
                        record.versionsnr,
                        interrupts[0].value.get("what_is_unclear")
                        if isinstance(interrupts[0], Interrupt)
                        else interrupts,
                    )
                    continue

                outcome_result = result.get("result", {})
                if "filing_id" in outcome_result:
                    counts["filed"] += 1
                    logger.info(
                        "filed %s-%s-v%s -> diff %s",
                        record.lokplan_id,
                        record.delnr,
                        record.versionsnr,
                        outcome_result["diff_id"],
                    )
                elif "escalation_id" in outcome_result:
                    counts["escalated"] += 1
                    logger.info(
                        "escalated %s-%s-v%s -> diff %s",
                        record.lokplan_id,
                        record.delnr,
                        record.versionsnr,
                        outcome_result["diff_id"],
                    )
                else:
                    counts["ignored"] += 1

        with psycopg.connect(database_url) as conn, conn.transaction():
            repo.save_watermark(conn, store.last_datoopdt, sorted(store.seen_at_watermark))
    finally:
        saver_cm.__exit__(None, None, None)

    logger.info("run complete: %s", counts)
    return counts


def main() -> None:
    load_dotenv()
    run_once()


if __name__ == "__main__":
    main()

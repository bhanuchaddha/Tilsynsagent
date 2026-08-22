"""Runs the agent unattended on a generous, fixed interval.

The register changes slowly and there is no prize for polling frequently -
POLL_INTERVAL_HOURS defaults to 24. This is a plain sleep loop, not a cron
daemon or task queue: the whole point of Phase 1 is that the agent runs
end-to-end without a person, and a loop that calls runner.run_once() on a
timer is the simplest thing that satisfies that. Deploying it (systemd timer,
cron entry, container with a restart policy) is an operational choice outside
this phase's scope - "no UI" extends to "no scheduler infrastructure" too.
"""

from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

from tilsynsagent.runner import run_once

logger = logging.getLogger("tilsynsagent.schedule")


def run_forever() -> None:
    load_dotenv()
    interval_hours = float(os.environ.get("POLL_INTERVAL_HOURS", "24"))
    interval_seconds = interval_hours * 3600
    logger.info("starting scheduled loop: every %.1f hour(s)", interval_hours)
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("scheduled run failed; will retry next interval")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_forever()

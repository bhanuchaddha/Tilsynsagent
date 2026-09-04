"""Command-line entrypoint: `tilsynsagent run-once`, `schedule`, `seed-demo`,
`reset-demo`, or `review`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser(prog="tilsynsagent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-once", help="Fetch new changes since the watermark, process them, exit.")
    subparsers.add_parser("schedule", help="Run unattended on POLL_INTERVAL_HOURS, forever.")
    subparsers.add_parser(
        "seed-demo", help="Feed hand-labelled golden cases through the real agent as demo data."
    )
    subparsers.add_parser("reset-demo", help="Delete all demo/test data. Live rows untouched.")
    subparsers.add_parser("review", help="Launch the Streamlit review queue.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    if args.command == "run-once":
        from tilsynsagent.runner import run_once

        run_once()
    elif args.command == "schedule":
        from tilsynsagent.schedule import run_forever

        run_forever()
    elif args.command == "seed-demo":
        import os

        from tilsynsagent import obs
        from tilsynsagent.db.migrate import run_migrations
        from tilsynsagent.demo.seed import seed_demo

        run_migrations(os.environ["DATABASE_URL"])
        try:
            seed_demo()
        finally:
            # seed_demo() itself only flushes - see its docstring on why a
            # mid-process shutdown() is unsafe. This CLI invocation is the
            # short-lived process shutdown() exists for.
            obs.shutdown()
    elif args.command == "reset-demo":
        from tilsynsagent.demo.reset import reset_demo

        reset_demo()
    elif args.command == "review":
        import streamlit.web.cli as stcli

        app_path = str(Path(__file__).parent / "review" / "app.py")
        sys.argv = ["streamlit", "run", app_path]
        stcli.main()


if __name__ == "__main__":
    main()

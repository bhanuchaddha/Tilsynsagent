"""Command-line entrypoint: `tilsynsagent run-once` or `tilsynsagent schedule`."""

from __future__ import annotations

import argparse
import logging

from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser(prog="tilsynsagent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-once", help="Fetch new changes since the watermark, process them, exit.")
    subparsers.add_parser("schedule", help="Run unattended on POLL_INTERVAL_HOURS, forever.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    if args.command == "run-once":
        from tilsynsagent.runner import run_once

        run_once()
    elif args.command == "schedule":
        from tilsynsagent.schedule import run_forever

        run_forever()


if __name__ == "__main__":
    main()

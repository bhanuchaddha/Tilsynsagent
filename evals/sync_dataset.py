"""Syncs the golden dataset (34 real + N synthetic NOT_COVERED cases) into a
Langfuse dataset, one item per case.

Idempotent for free: create_dataset_item(id=...) upserts, and
cases.dataset_item_id(case) returns case["id"] verbatim - "ZL-001" in the
JSONL is "ZL-001" in Langfuse. Running this twice must produce 34+N items,
not 68+2N - there is no local mapping table because none is needed.
"""

from __future__ import annotations

import argparse
import sys

DATASET_NAME = "tilsynsagent-golden"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync the golden dataset into Langfuse.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be synced, touch nothing."
    )
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    from evals.cases import dataset_item_id, expected_route, load_all_cases

    cases = load_all_cases()
    print(f"{len(cases)} cases to sync into dataset {DATASET_NAME!r}", file=sys.stderr)

    if args.dry_run:
        for case in cases:
            print(f"  {dataset_item_id(case)}  expected_route={expected_route(case)}",
                  file=sys.stderr)
        return

    from tilsynsagent import obs

    if not obs.observability_enabled():
        print("LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set - nothing to sync to.",
              file=sys.stderr)
        raise SystemExit(1)

    from langfuse import get_client

    client = get_client()
    client.create_dataset(
        name=DATASET_NAME,
        description="Tilsynsagent golden dataset: 34 hand-labelled real transitions "
        "plus a synthetic NOT_COVERED set. See evals/golden/README.md.",
    )

    for case in cases:
        client.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=dataset_item_id(case),
            input={
                "changed_fields": case["changed_fields"],
                "before": case["before"],
                "after": case["after"],
                "source": case["source"],
            },
            expected_output={
                "route": expected_route(case),
                "label": case["label"],
                "rule": case["rule"],
            },
            metadata={"origin": case["origin"], "rule_set": case["rule_set"]},
        )
    client.flush()
    print(f"synced {len(cases)} items", file=sys.stderr)


if __name__ == "__main__":
    main()

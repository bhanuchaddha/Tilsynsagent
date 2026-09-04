"""The CI gate: fails a pull request whose behaviour is worse than the
committed thresholds.

**Why a separate entry point from run_baseline.py.** A baseline *records* what
the system does, however bad, and is committed unedited (rule 9). A gate
*decides* whether a change may merge. Mixing them would mean either a baseline
that refuses to record a bad number, or a gate whose pass condition drifts
every time someone records a new baseline. They share the task and scorer code
and nothing else.

**Why thresholds live in a committed file, not in this script.** `evals/
thresholds.json` is the agreed definition of "good enough", and changing it is
a reviewable diff that shows up in the pull request that changes it. A
contributor who lowers a threshold to make their change pass has to do so
visibly, which is the entire point - the number cannot be quietly relaxed in
the same commit that breaks it.

**Why it calls the real model.** Recorded fixtures would make this fast and
deterministic, and would also make it measure nothing: the failure mode this
gate exists to catch is a prompt edit changing what the model produces, which
a fixture by definition cannot see. The run costs free-tier tokens and takes a
few minutes, and that is the correct trade.

Exit codes: 0 = every threshold met, 1 = at least one regression, 2 = the run
could not be completed (rate limits, network) - which is deliberately *not*
the same as a regression, because a CI outage must not read as a behaviour
change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds.json"

# A run that could not gather enough evidence is inconclusive, not a pass and
# not a regression. Below this fraction of cases completing, the gate exits 2.
MIN_COMPLETION_RATE = 0.9


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict:
    """Reads the committed thresholds, dropping documentation keys.

    JSON has no comments, and the reasoning behind each number is the most
    important thing in that file - a bare number tells a future reader
    nothing about whether it may be moved. Keys starting with "_" are prose
    and are stripped here so they never reach the comparison.
    """
    raw = json.loads(path.read_text())
    return {
        key: ({k: v for k, v in value.items() if not k.startswith("_")}
              if isinstance(value, dict) else value)
        for key, value in raw.items()
        if not key.startswith("_")
    }


def evaluate(aggregate: dict, thresholds: dict) -> tuple[bool, list[str]]:
    """Compares one pass's aggregate against the thresholds.

    Returns (passed, lines) where lines is the human-readable verdict per
    metric - printed whether or not the gate passes, so a green run still
    shows what it measured rather than only saying "ok".
    """
    lines: list[str] = []
    failures: list[str] = []

    coverage = aggregate.get("rule_engine_coverage")
    min_coverage = thresholds["rule_engine_coverage"]
    if coverage is not None:
        ok = coverage >= min_coverage
        lines.append(
            f"{'PASS' if ok else 'FAIL'}  rule_engine_coverage  "
            f"{coverage:.3f} (min {min_coverage:.3f})"
        )
        if not ok:
            failures.append("rule_engine_coverage")

    scorer_thresholds = thresholds["scorers"]
    for name in sorted(scorer_thresholds):
        minimum = scorer_thresholds[name]
        observed = aggregate.get("scorers", {}).get(name)
        if observed is None:
            lines.append(f"FAIL  {name}  not measured in this run")
            failures.append(name)
            continue
        mean = observed["mean"]
        ok = mean >= minimum
        failing = observed.get("failing_case_ids") or []
        detail = f" failing: {', '.join(failing)}" if failing else ""
        lines.append(
            f"{'PASS' if ok else 'FAIL'}  {name}  {mean:.3f} (min {minimum:.3f}) n={observed['n']}{detail}"
        )
        if not ok:
            failures.append(name)

    max_route_mismatches = thresholds["max_route_mismatches"]
    mismatches = aggregate.get("route_mismatches") or []
    ok = len(mismatches) <= max_route_mismatches
    lines.append(
        f"{'PASS' if ok else 'FAIL'}  route_mismatches  {len(mismatches)} "
        f"(max {max_route_mismatches})"
        + (f" -> {', '.join(mismatches)}" if mismatches else "")
    )
    if not ok:
        failures.append("route_mismatches")

    return (not failures), lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval gate for CI.")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N cases.")
    parser.add_argument(
        "--from-json",
        type=str,
        default=None,
        help="Evaluate an existing run_baseline --out file instead of calling the model.",
    )
    parser.add_argument("--out", type=str, default=None, help="Write the run's JSON here.")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    thresholds = load_thresholds()

    if args.from_json:
        payload = json.loads(Path(args.from_json).read_text())
        aggregate = dict(payload["passes"][0]["aggregate"])
        aggregate["rule_engine_coverage"] = payload["rule_engine_coverage"]
        n_cases = payload["n_cases"]
        resolved = payload.get("resolved_models"), payload.get("resolved_prompts")
    else:
        from evals.cases import load_all_cases
        from evals.run_baseline import _aggregate, _rule_engine_coverage, run_pass
        from evals.tasks import resolved_models, resolved_prompts

        cases = load_all_cases()
        if args.limit:
            cases = cases[: args.limit]
        coverage, _ = _rule_engine_coverage()
        records = run_pass(cases)
        aggregate = _aggregate(records)
        aggregate["rule_engine_coverage"] = coverage
        n_cases = len(cases)
        resolved = resolved_models(), resolved_prompts()
        if args.out:
            Path(args.out).write_text(
                json.dumps(
                    {
                        "resolved_models": resolved[0],
                        "resolved_prompts": resolved[1],
                        "rule_engine_coverage": coverage,
                        "n_cases": n_cases,
                        "passes": [{"records": records, "aggregate": aggregate}],
                    },
                    indent=2,
                )
            )

    print(f"models:  {resolved[0]}")
    print(f"prompts: {resolved[1]}")
    print(f"cases:   {n_cases}")
    print()

    # An inconclusive run must not read as either outcome - see the module
    # docstring. The error rate is over the cases that called the model at
    # all, which is what "could the run gather its evidence" means here.
    error_rate = aggregate.get("llm_step_error_rate", 0.0)
    if (1.0 - error_rate) < MIN_COMPLETION_RATE:
        print(
            f"INCONCLUSIVE: llm_step_error_rate {error_rate:.3f} - too many calls failed "
            f"to judge behaviour. This is not a regression verdict.",
            file=sys.stderr,
        )
        return 2

    passed, lines = evaluate(aggregate, thresholds)
    for line in lines:
        print(line)
    print()

    if passed:
        print("GATE PASSED - every threshold met.")
        return 0

    print(
        "GATE FAILED - this change lowers a behaviour the eval suite protects.\n"
        "Either fix the regression, or, if the new behaviour is genuinely correct,\n"
        "change evals/thresholds.json in this pull request and say why in the diff.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

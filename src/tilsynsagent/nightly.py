"""The nightly run: the vendor-facing half of the feedback loop.

**The deliberate asymmetry with CI.** `.github/workflows/evals.yml` runs the
gate with no Langfuse keys at all, on purpose: a merge verdict must never
depend on an observability vendor being reachable, and the number CI enforces
is text committed in this repository. This job is the opposite. It *is* the
vendor loop - it reads annotations a person left in Langfuse, promotes them,
runs the dataset, compares against the previous run, and writes an alert. It
gets keys because it has nothing to do without them.

Saying that out loud matters more than it looks: it is the difference between
"we use Langfuse" and "we depend on Langfuse", and a system that cannot
articulate which of its checks are vendor-independent has not thought about
what happens when the vendor is down.

**Order matters.** Promotion runs *before* the eval, so cases a person
labelled today are measured tonight rather than tomorrow. That is what makes
"a business person labels a run" and "a developer gets an alert" the same
failure one dataset apart rather than two unrelated events a day apart.

Invoked by `tilsynsagent nightly` and by `.github/workflows/nightly.yml` -
both, with no logic duplicated into the workflow file, so what CI runs is
what a person can run by hand.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger("tilsynsagent.nightly")


def run_nightly(
    *,
    limit: int | None = None,
    promote: bool = True,
    judge: bool = True,
    on: date | None = None,
) -> int:
    """One full nightly pass. Returns a process exit code.

    Exit codes mirror evals/gate.py: 0 = nothing regressed, 1 = a regression
    was found and an alert was written, 2 = the run could not be completed.
    A cron can act on the code alone, and an infrastructure failure is never
    reported as a behaviour change.
    """
    from evals.regression import (
        compare_runs,
        load_previous_nightly,
        save_nightly,
        summarise_run,
    )

    on = on or date.today()
    run_name = on.isoformat()

    if promote:
        _promote_annotations()

    payload = _run_eval(limit=limit)
    if payload is None:
        print("INCONCLUSIVE: the eval run could not be completed.")
        return 2

    current = summarise_run(payload, name=run_name)

    previous = _previous_run(exclude=run_name)
    comparison = compare_runs(current, previous)
    for line in comparison.lines:
        print(line)

    saved = save_nightly(payload, name=run_name)
    print(f"\nrun saved: {saved}")

    if judge:
        _judge_agreement(on=on)

    if comparison.regressed:
        from tilsynsagent.obs.alerts import developer_alert

        path = developer_alert(
            current_score=current.mean_score,
            previous_score=previous.mean_score if previous else current.mean_score,
            regressed_scorers=comparison.regressed_scorers,
            new_case_ids=comparison.new_case_ids,
            newly_failing_case_ids=comparison.newly_failing_case_ids,
            prompt_versions=current.prompt_versions,
            previous_prompt_versions=previous.prompt_versions if previous else {},
        )
        print(f"alert written: {path}")
        return 1

    print("\nno regression against the previous run.")
    return 0


def _promote_annotations() -> None:
    """Promotes anything a person answered since the last run.

    Failure here is logged and the nightly continues: an unreachable
    annotation queue must not stop the regression check, which is the part
    that works with no vendor at all.
    """
    try:
        from evals.promote import promote_completed

        written = promote_completed()
        if written:
            print(f"promoted {len(written)} annotated run(s) into the dataset: {', '.join(written)}")
        else:
            print("nothing to promote from the annotation queue.")
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("promotion step failed, continuing: %s", exc)


def _run_eval(*, limit: int | None) -> dict | None:
    """Runs the full dataset and returns the payload gate.py --out writes.

    Reuses run_baseline's own pass and aggregate functions rather than
    reimplementing them, so the nightly measures the same thing the gate and
    the committed baselines do - three entry points, one definition of a run.
    """
    try:
        from evals.cases import load_all_cases
        from evals.run_baseline import _aggregate, _rule_engine_coverage, run_pass
        from evals.tasks import resolved_models, resolved_prompts

        cases = load_all_cases()
        if limit:
            cases = cases[:limit]
        coverage, _ = _rule_engine_coverage()
        records = run_pass(cases)
        aggregate = _aggregate(records)
        aggregate["rule_engine_coverage"] = coverage
        return {
            "resolved_models": resolved_models(),
            "resolved_prompts": resolved_prompts(),
            "rule_engine_coverage": coverage,
            "n_cases": len(cases),
            "passes": [{"records": records, "aggregate": aggregate}],
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("nightly eval run failed: %s", exc)
        return None


def _previous_run(*, exclude: str):
    """The run to compare against: Langfuse first, committed JSON second.

    Langfuse first because it sees runs this checkout never did. JSON second
    because Langfuse's free tier forgets after 30 days, and a comparison with
    no baseline is a green run that measured nothing - see evals/regression.py.
    """
    from evals.regression import load_previous_nightly, previous_run_from_langfuse, summarise_run

    from_langfuse = previous_run_from_langfuse()
    if from_langfuse is not None and from_langfuse.per_scorer:
        print(f"baseline: Langfuse dataset run {from_langfuse.name!r}")
        return from_langfuse

    fallback = load_previous_nightly(exclude=exclude)
    if fallback is None:
        print("baseline: none - this run becomes the baseline")
        return None
    payload, name = fallback
    print(f"baseline: committed nightly {name}")
    return summarise_run(payload, name=name)


def _judge_agreement(*, on: date) -> None:
    """Measures the judge against human labels and commits the result.

    In the nightly rather than inline because human labels arrive days after
    the judge scores they are about - measuring agreement at decision time
    would measure an empty set. See obs/judge.py.
    """
    try:
        from tilsynsagent.obs.judge import write_agreement_report

        path = write_agreement_report(on=on)
        if path is not None:
            print(f"judge agreement written: {path}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("judge agreement could not be computed: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="The nightly eval and regression run.")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N cases.")
    parser.add_argument(
        "--no-promote", action="store_true", help="Skip promoting annotated runs."
    )
    parser.add_argument("--no-judge", action="store_true", help="Skip judge agreement.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from dotenv import load_dotenv

    load_dotenv()

    code = run_nightly(promote=not args.no_promote, judge=not args.no_judge, limit=args.limit)

    from tilsynsagent import obs

    obs.shutdown()
    return code


if __name__ == "__main__":
    raise SystemExit(main())

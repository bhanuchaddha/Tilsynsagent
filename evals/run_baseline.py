"""Runs the golden dataset (34 real + N synthetic NOT_COVERED cases) through
the LLM steps and computes the aggregate scores that become the committed
baseline in docs/evals/baseline-<date>.md.

`--no-langfuse` is a requirement, not a convenience: it runs identical
task+scorer code locally and produces the same aggregate a Langfuse
experiment run would, so the committed number is reproducible by code that
does not depend on a vendor whose free tier deletes data after 30 days.

GROQ_MODEL is read at import time by the llm modules - load_dotenv() must
run, and evals.tasks must be imported, only after argument parsing, inside
main(). Importing evals.tasks at module level here would read the wrong
model if a caller sets GROQ_MODEL after importing this file but before
calling main() (e.g. programmatically) - keeping the import inside main()
means "run the baseline" and "resolve the model" always happen together.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MAX_CONCURRENCY = 2


def _rule_engine_coverage() -> tuple[float, list[str]]:
    """The precondition gate: apply_rules must reproduce the golden label
    for every one of the 34 real cases (not the synthetic set, which exists
    to exercise NOT_COVERED and is expected to route there, not to a
    file/escalate/ignore label). Always checked against the full 34,
    independent of --limit. Reported as a precondition, never as the
    baseline's achievement - see docs/evals/baseline-<date>.md."""
    from evals.cases import load_golden_cases
    from tilsynsagent.rules.engine import apply_rules

    # Escalation-derived cases record what a *person* decided about a case the
    # engine escalated, and the interesting ones are exactly those where the
    # person disagreed with the engine. Asking the engine to reproduce a human
    # override it has never been taught is not a coverage measurement - see
    # evals/golden/README.md and tests/test_rules_engine.py, which draw the
    # same line.
    golden = [c for c in load_golden_cases() if c.get("origin") != "escalation-derived"]
    mismatches = []
    for case in golden:
        decision = apply_rules(case["changed_fields"], case["before"], case["after"])
        ok = decision.outcome.value == case["label"] and (
            case["rule"] is None or decision.rule == case["rule"]
        )
        if not ok:
            mismatches.append(case["id"])
    return (len(golden) - len(mismatches)) / len(golden), mismatches


def run_pass(cases: list[dict]) -> list[dict]:
    """Runs every case once, scores each result, returns per-case records.
    Concurrent up to MAX_CONCURRENCY - the free-tier budget note in the plan
    (34 tasks per pass, tripled at --stability 3) is what bounds this, not a
    generic throughput target."""
    from evals import scorers
    from evals.cases import expected_route
    from evals.tasks import run_case

    records = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        futures = {pool.submit(run_case, case): case for case in cases}
        for future in as_completed(futures):
            case = futures[future]
            result = future.result()
            record = {
                "case_id": result.case_id,
                "expected_route": expected_route(case),
                "actual_route": result.route,
                "route_match": expected_route(case) == result.route,
                "error": result.error,
                "usage": result.usage.to_dict() if result.usage else None,
                "latency_ms": result.latency_ms,
                "scores": {},
            }
            if result.error is not None:
                record["scores"]["valid_structured_output"] = 0.0
            elif result.output_text is not None:
                record["scores"]["valid_structured_output"] = 1.0
                for score in scorers.score_all(
                    case, result.output_text, route=result.route
                ):
                    record["scores"][score.name] = score.value
            records.append(record)
    return records


def _aggregate(records: list[dict]) -> dict:
    llm_records = [r for r in records if r["actual_route"] in ("file", "not_covered")]
    error_records = [r for r in records if r["error"] is not None]

    score_names = sorted({name for r in records for name in r["scores"]})
    per_scorer = {}
    for name in score_names:
        values = [r["scores"][name] for r in records if name in r["scores"]]
        failing = [r["case_id"] for r in records if r["scores"].get(name) == 0.0]
        per_scorer[name] = {
            "mean": sum(values) / len(values) if values else None,
            "n": len(values),
            "failing_case_ids": failing,
        }

    total_tokens = sum(r["usage"]["total_tokens"] for r in records if r["usage"])
    total_cost = 0.0
    if llm_records:
        from tilsynsagent.llm.pricing import load_pricing
        from tilsynsagent.llm.usage import Usage

        pricing = load_pricing()
        for r in records:
            if r["usage"]:
                usage = Usage(**{k: v for k, v in r["usage"].items() if k != "latency_ms"})
                total_cost += pricing.cost_usd(usage)

    return {
        "n_cases": len(records),
        "n_llm_calls": len(llm_records),
        "llm_step_error_rate": len(error_records) / len(llm_records) if llm_records else 0.0,
        "mean_tokens_per_case": total_tokens / len(llm_records) if llm_records else 0.0,
        "total_cost_usd": round(total_cost, 6),
        "scorers": per_scorer,
        "route_mismatches": [r["case_id"] for r in records if not r["route_match"]],
    }


def _run_langfuse_experiment(cases: list[dict], *, run_name: str) -> None:
    """Runs the same task+scorer code as run_pass, but through Langfuse's
    own DatasetClient.run_experiment against the dataset evals/sync_dataset.py
    populated - not a second hand-rolled execution path, a thin adapter
    (_task, _evaluator below) over evals.tasks.run_case and
    evals.scorers.ALL_SCORERS so the two modes can never silently diverge in
    what they measure. Uses the case id (not the item's dataset id lookup)
    to find the matching local case dict, since a DatasetItem's .input is a
    copy of what sync_dataset.py wrote, not the case dict itself."""
    from langfuse import Evaluation, get_client

    from evals import scorers
    from evals.cases import dataset_item_id
    from evals.sync_dataset import DATASET_NAME
    from evals.tasks import run_case

    client = get_client()
    dataset = client.get_dataset(DATASET_NAME)
    cases_by_id = {dataset_item_id(c): c for c in cases}
    # dataset.run_experiment() always runs every item in the dataset (no
    # subset parameter); client.run_experiment(data=...) is the lower-level
    # call that accepts an explicit item list, which is what --limit needs.
    dataset_items = [item for item in dataset.items if item.id in cases_by_id]
    if not dataset_items:
        print(f"none of these case ids are in dataset {DATASET_NAME!r} - "
              f"run `python -m evals.sync_dataset` first", file=sys.stderr)
        raise SystemExit(1)

    def _task(*, item, **_kwargs):
        case = cases_by_id[item.id]
        return run_case(case)

    from evals.cases import expected_route

    def _evaluator(*, input, output, expected_output, metadata, **_kwargs):
        del input, expected_output, metadata  # unused; case looked up via output.case_id
        case = cases_by_id[output.case_id]
        evals = [
            Evaluation(
                name="route_match",
                value=1.0 if output.route == expected_route(case) else 0.0,
            )
        ]
        if output.error is not None:
            evals.append(Evaluation(name="valid_structured_output", value=0.0,
                                     comment=output.error))
            return evals
        if output.output_text is not None:
            evals.append(Evaluation(name="valid_structured_output", value=1.0))
            for score in scorers.score_all(case, output.output_text, route=output.route):
                evals.append(Evaluation(name=score.name, value=score.value, comment=score.comment))
        return evals

    result = client.run_experiment(
        name=run_name,
        data=dataset_items,
        task=_task,
        evaluators=[_evaluator],
        max_concurrency=MAX_CONCURRENCY,
    )
    print(f"Langfuse experiment run: {run_name} ({len(dataset_items)} items)", file=sys.stderr)
    if hasattr(result, "format"):
        print(result.format(), file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the golden-dataset eval baseline.")
    parser.add_argument("--no-langfuse", action="store_true", help="Skip Langfuse entirely.")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N cases.")
    parser.add_argument("--stability", type=int, default=1, help="Run the full pass N times.")
    parser.add_argument("--out", type=str, default=None, help="Write JSON results to this path.")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    if args.no_langfuse:
        # Force-disable, even if .env set real keys: --no-langfuse must mean
        # this run has zero Langfuse involvement, not "Langfuse happens to
        # be off right now" - obs.observability_enabled() is a bool of both
        # keys, so clearing either is enough.
        import os

        os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
        os.environ.pop("LANGFUSE_SECRET_KEY", None)

    from evals.cases import load_all_cases
    from evals.tasks import resolved_models, resolved_prompts

    cases = load_all_cases()
    if args.limit:
        cases = cases[: args.limit]

    coverage, mismatches = _rule_engine_coverage()

    print(f"resolved models: {resolved_models()}", file=sys.stderr)
    print(f"resolved prompts: {resolved_prompts()}", file=sys.stderr)
    print(f"rule_engine_coverage (GATE): {coverage:.3f}", file=sys.stderr)
    if mismatches:
        print(f"  mismatches: {mismatches}", file=sys.stderr)

    from tilsynsagent import obs

    use_langfuse_experiment = not args.no_langfuse and obs.observability_enabled()

    passes = []
    for i in range(args.stability):
        print(f"pass {i + 1}/{args.stability}: {len(cases)} cases", file=sys.stderr)
        if use_langfuse_experiment:
            # Full mode: the Langfuse-native DatasetClient.run_experiment
            # path (see _run_langfuse_experiment) is the run of record - it
            # calls the same run_case/scorers code as run_pass below, so
            # running both here would just double LLM calls against a
            # rate-limited free tier for no new information. --no-langfuse
            # is what runs run_pass instead, as the vendor-independent,
            # reproducible-without-Langfuse path.
            import datetime

            today = datetime.datetime.now(tz=datetime.UTC).date()
            run_name = f"baseline-{today.isoformat()}-pass{i + 1}"
            _run_langfuse_experiment(cases, run_name=run_name)
            continue

        records = run_pass(cases)
        agg = _aggregate(records)
        passes.append({"records": records, "aggregate": agg})
        print(f"  aggregate: {json.dumps(agg, indent=2)}", file=sys.stderr)

    if use_langfuse_experiment:
        obs.flush()
        print("full run complete - results are in the Langfuse UI, not printed here "
              "(the local aggregate above is what --no-langfuse also produces).",
              file=sys.stderr)
        return

    output = {
        "resolved_models": resolved_models(),
        "resolved_prompts": resolved_prompts(),
        "rule_engine_coverage": coverage,
        "rule_engine_mismatches": mismatches,
        "n_cases": len(cases),
        "passes": passes,
    }

    if args.out:
        Path(args.out).write_text(json.dumps(output, indent=2))
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()

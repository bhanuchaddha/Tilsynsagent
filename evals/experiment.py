"""Model comparison as an experiment, not an opinion.

**The question this answers.** "Which model should this run on?" is normally
settled by reputation, benchmark tables, or whichever one was in the tutorial.
None of those say anything about *this* task on *this* data. The only honest
answer is: run the golden dataset against both, record both results with their
configuration attached, and publish where the cheaper one fails.

**Comparison is experiment-versus-experiment, never a score in isolation.** A
number like "0.96" means nothing on its own - not without the model, the
prompt version, the dataset revision and the date beside it. Every run here
records all four, and the output is a comparison table rather than a verdict,
because the decision of whether a gap matters belongs to a person.

**Where the small model fails is the deliverable**, not the headline average.
An aggregate that says "0.96 versus 0.91" hides the only thing worth knowing:
*which* cases the cheap model got wrong, and whether they are the cases that
matter. A model that is worse on average but never drops a citation is a
different proposition from one that is better on average and occasionally
invents a number.

Candidates are same-provider, same-tier on purpose (`openai/gpt-oss-120b` vs
`openai/gpt-oss-20b`, both verified live on this key on 2026-09-04). That
isolates model size as the variable rather than confounding it with vendor,
pricing model, and API behaviour all at once.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

# Same family, same provider, same free tier - so the comparison is about
# size and nothing else. llama-3.1-8b-instant was considered and is
# decommissioned on Groq (verified live 2026-09-04: 404 on this key).
DEFAULT_MODELS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b")


def run_for_model(model: str, cases: list[dict]) -> dict:
    """Runs every case against one model and returns its aggregate.

    GROQ_MODEL is read at *import* time by llm/assess.py and llm/summarise.py
    (their DEFAULT_MODEL constants), so switching models requires setting the
    environment variable and then reloading those modules - importing them
    once and mutating os.environ afterwards would silently measure the first
    model twice. This is the same hazard evals/tasks.py documents for
    run_baseline.py, in a sharper form: here it would not just mislabel a
    run, it would invalidate the entire comparison.
    """
    import importlib

    os.environ["GROQ_MODEL"] = model

    import tilsynsagent.llm.assess
    import tilsynsagent.llm.summarise

    importlib.reload(tilsynsagent.llm.assess)
    importlib.reload(tilsynsagent.llm.summarise)

    import evals.tasks

    importlib.reload(evals.tasks)

    import evals.run_baseline

    importlib.reload(evals.run_baseline)

    resolved = evals.tasks.resolved_models()
    if set(resolved.values()) != {model}:
        raise RuntimeError(
            f"asked for {model!r} but the LLM modules resolved to {resolved} - "
            "the reload above did not take effect, and this comparison would be a lie"
        )

    records = evals.run_baseline.run_pass(cases)
    aggregate = evals.run_baseline._aggregate(records)
    return {
        "model": model,
        "resolved_models": resolved,
        "resolved_prompts": evals.tasks.resolved_prompts(),
        "aggregate": aggregate,
        "records": records,
    }


def compare(runs: list[dict]) -> list[str]:
    """The comparison table, plus the per-case divergences underneath it."""
    lines: list[str] = []
    scorer_names = sorted(
        {name for run in runs for name in run["aggregate"].get("scorers", {})}
    )

    header = f"{'scorer':<28}" + "".join(f"{run['model']:>26}" for run in runs)
    lines.append(header)
    lines.append("-" * len(header))

    for name in scorer_names:
        row = f"{name:<28}"
        for run in runs:
            observed = run["aggregate"].get("scorers", {}).get(name)
            row += f"{observed['mean']:>26.3f}" if observed else f"{'-':>26}"
        lines.append(row)

    for key, label, fmt in (
        ("llm_step_error_rate", "llm_step_error_rate", "{:>26.3f}"),
        ("mean_tokens_per_case", "mean_tokens_per_case", "{:>26.1f}"),
        ("total_cost_usd", "total_cost_usd", "{:>26.6f}"),
    ):
        row = f"{label:<28}"
        for run in runs:
            value = run["aggregate"].get(key)
            row += fmt.format(value) if value is not None else f"{'-':>26}"
        lines.append(row)

    lines.append("")
    lines.append("Where each model fails (the point of this run):")
    for run in runs:
        failures: dict[str, list[str]] = {}
        for name, observed in run["aggregate"].get("scorers", {}).items():
            if observed.get("failing_case_ids"):
                failures[name] = observed["failing_case_ids"]
        if not failures:
            lines.append(f"  {run['model']}: no failing cases")
            continue
        lines.append(f"  {run['model']}:")
        for name in sorted(failures):
            lines.append(f"    {name}: {', '.join(failures[name])}")

    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare models on the golden dataset.")
    parser.add_argument(
        "--models", nargs="+", default=list(DEFAULT_MODELS), help="Models to compare."
    )
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N cases.")
    parser.add_argument("--out", type=str, default=None, help="Write the full JSON here.")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    # This is a measurement, not a production run: keep it entirely local so
    # a comparison never depends on a vendor being reachable, exactly as
    # run_baseline --no-langfuse does.
    os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
    os.environ.pop("LANGFUSE_SECRET_KEY", None)

    from evals.cases import load_all_cases

    cases = load_all_cases()
    if args.limit:
        cases = cases[: args.limit]

    runs = []
    for model in args.models:
        print(f"running {len(cases)} cases against {model} ...", file=sys.stderr)
        runs.append(run_for_model(model, cases))

    for line in compare(runs):
        print(line)

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "run_at": datetime.datetime.now(tz=datetime.UTC).isoformat(),
                    "n_cases": len(cases),
                    "runs": runs,
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

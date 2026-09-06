"""Comparing one nightly eval run against the previous one.

**Why run-over-run and not run-against-threshold.** `evals/gate.py` already
answers "is this good enough?" against committed thresholds, and that is the
right question for a pull request. It is the wrong question for a nightly:
thresholds are set with slack in them (0.96, not 1.000, because temperature=0
is not determinism on a hosted MoE), so a system can degrade from 1.000 to
0.97 - a real and worrying change - without the gate noticing anything. The
nightly's question is different: *did anything change since yesterday?*

**Why `new_case_ids` and `newly_failing_case_ids` are reported separately.**
Because telling them apart *is* the developer workflow, and no aggregate can:

- A **new case that fails** is the dataset doing its job. Somebody labelled a
  production run precisely because the agent got it wrong, and the mean score
  dropping is the expected, correct consequence. Nothing regressed.
- A **case that used to pass and now fails** is a regression. The dataset did
  not change; the behaviour did.

A single "0.94 -> 0.86" makes those indistinguishable, and the two demand
opposite responses. An alert that cannot tell them apart gets muted within a
fortnight, which is worse than no alert.

**Why a tolerance, and why 0.02.** Same reason the thresholds sit at 0.96:
the model is a hosted mixture-of-experts and temperature=0 does not make it
deterministic (see docs/evals/baseline-2026-08-22.md's ZL-018 finding). A
nightly that fires on single-case run-to-run wobble teaches people to ignore
it, which costs more than the drift it would have caught.

**Why the baseline has a file fallback.** Langfuse's free tier retains data
for 30 days. Reading the previous run only from `datasets.get_runs` means the
comparison silently loses its baseline after a month of quiet - the run
succeeds, reports no regression, and is comparing against nothing. The
committed `docs/evals/nightly/*.json` files are not an optimisation; without
them the nightly stops meaning anything exactly when nobody is watching.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
NIGHTLY_DIR = REPO_ROOT / "docs" / "evals" / "nightly"

# Run-to-run wobble below this is not a regression - see the module docstring.
TOLERANCE = 0.02


@dataclass(frozen=True)
class RunSummary:
    """One eval run, reduced to what a comparison needs."""

    name: str
    mean_score: float
    per_scorer: dict[str, float] = field(default_factory=dict)
    # case_id -> whether every scorer passed for it.
    case_passed: dict[str, bool] = field(default_factory=dict)
    prompt_versions: dict[str, str] = field(default_factory=dict)

    @property
    def case_ids(self) -> set[str]:
        return set(self.case_passed)


@dataclass(frozen=True)
class Comparison:
    """The verdict, with the two case sets kept apart."""

    current: RunSummary
    previous: RunSummary | None
    per_scorer_delta: dict[str, float]
    regressed: bool
    regressed_scorers: list[str]
    new_case_ids: list[str]
    newly_failing_case_ids: list[str]
    lines: list[str]

    @property
    def mean_delta(self) -> float:
        if self.previous is None:
            return 0.0
        return self.current.mean_score - self.previous.mean_score


def summarise_run(payload: dict, *, name: str) -> RunSummary:
    """Reduces a gate/baseline JSON payload to a RunSummary.

    Reads the same file shape `evals/gate.py --out` writes, so the nightly
    and the CI gate cannot diverge in what they consider a run.
    """
    pass_ = payload["passes"][0]
    aggregate = pass_["aggregate"]
    per_scorer = {
        name_: observed["mean"] for name_, observed in (aggregate.get("scorers") or {}).items()
    }
    case_passed = {}
    for record in pass_.get("records", []):
        scores = record.get("scores") or {}
        # A case with no scores at all (a rule-decided escalation, which calls
        # no model) counts as passing: it has no LLM behaviour to regress, and
        # counting it as a failure would make the mean move whenever the mix
        # of routes in the dataset changes.
        case_passed[record["case_id"]] = all(v >= 1.0 for v in scores.values()) if scores else True

    means = list(per_scorer.values())
    return RunSummary(
        name=name,
        mean_score=sum(means) / len(means) if means else 0.0,
        per_scorer=per_scorer,
        case_passed=case_passed,
        prompt_versions=payload.get("resolved_prompts") or {},
    )


def compare_runs(
    current: RunSummary, previous: RunSummary | None, *, tolerance: float = TOLERANCE
) -> Comparison:
    """Compares two runs, keeping new cases and newly failing cases apart."""
    lines: list[str] = []

    if previous is None:
        lines.append("no previous run to compare against - recording this one as the baseline")
        return Comparison(
            current=current,
            previous=None,
            per_scorer_delta={},
            regressed=False,
            regressed_scorers=[],
            new_case_ids=sorted(current.case_ids),
            newly_failing_case_ids=[],
            lines=lines,
        )

    per_scorer_delta: dict[str, float] = {}
    regressed_scorers: list[str] = []
    for name in sorted(set(current.per_scorer) | set(previous.per_scorer)):
        now = current.per_scorer.get(name)
        before = previous.per_scorer.get(name)
        if now is None or before is None:
            lines.append(f"?     {name}  only present in one run - not compared")
            continue
        delta = now - before
        per_scorer_delta[name] = delta
        if delta < -tolerance:
            regressed_scorers.append(name)
        lines.append(
            f"{'DROP' if delta < -tolerance else 'ok  '}  {name}  "
            f"{before:.3f} -> {now:.3f} ({delta:+.3f})"
        )

    new_case_ids = sorted(current.case_ids - previous.case_ids)
    newly_failing = sorted(
        cid
        for cid, passed in current.case_passed.items()
        # Only cases present in BOTH runs can "newly fail" - a case that did
        # not exist yesterday cannot have regressed, however badly it scores.
        # This is the line the whole module is about.
        if not passed and previous.case_passed.get(cid) is True
    )

    mean_delta = current.mean_score - previous.mean_score
    lines.append(
        f"mean {previous.mean_score:.3f} -> {current.mean_score:.3f} ({mean_delta:+.3f})"
    )
    if new_case_ids:
        lines.append(f"new cases ({len(new_case_ids)}): {', '.join(new_case_ids)}")
    if newly_failing:
        lines.append(f"newly failing ({len(newly_failing)}): {', '.join(newly_failing)}")

    regressed = bool(regressed_scorers) or bool(newly_failing)
    return Comparison(
        current=current,
        previous=previous,
        per_scorer_delta=per_scorer_delta,
        regressed=regressed,
        regressed_scorers=regressed_scorers,
        new_case_ids=new_case_ids,
        newly_failing_case_ids=newly_failing,
        lines=lines,
    )


def save_nightly(payload: dict, *, name: str, directory: Path | None = None) -> Path:
    """Writes a run's JSON where the next run can find it without Langfuse."""
    directory = directory or NIGHTLY_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_previous_nightly(
    *, exclude: str | None = None, directory: Path | None = None
) -> tuple[dict, str] | None:
    """The most recent committed nightly payload, newest first by filename.

    Filenames are dates, so lexical ordering is chronological ordering.
    """
    directory = directory or NIGHTLY_DIR
    if not directory.exists():
        return None
    for path in sorted(directory.glob("*.json"), reverse=True):
        if exclude and path.stem == exclude:
            continue
        try:
            return json.loads(path.read_text()), path.stem
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("nightly payload %s unreadable: %s", path, exc)
    return None


def previous_run_from_langfuse(*, dataset_name: str = "tilsynsagent-golden") -> RunSummary | None:
    """The previous dataset run, read from Langfuse.

    Tried first because it carries runs this checkout may never have seen -
    another machine's nightly, or a manual experiment. Falls back to the
    committed JSON, which is what survives the 30-day retention window.
    """
    from tilsynsagent.obs.langfuse_setup import observability_enabled

    if not observability_enabled():
        return None
    try:
        from langfuse import get_client

        runs = get_client().api.datasets.get_runs(dataset_name=dataset_name, limit=2)
        data = getattr(runs, "data", []) or []
        if len(data) < 2:
            return None
        # data[0] is the run just completed; data[1] is the one before it.
        previous = data[1]
        return RunSummary(
            name=getattr(previous, "name", "previous"),
            mean_score=_mean_of(previous),
            per_scorer=_per_scorer_of(previous),
            case_passed={},
            prompt_versions=(getattr(previous, "metadata", None) or {}).get("prompts", {}),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("previous dataset run could not be read from Langfuse: %s", exc)
        return None


def _mean_of(run) -> float:
    per_scorer = _per_scorer_of(run)
    return sum(per_scorer.values()) / len(per_scorer) if per_scorer else 0.0


def _per_scorer_of(run) -> dict[str, float]:
    """Per-scorer means off a Langfuse dataset run object.

    Defensive about shape: this is the one place the nightly depends on a
    vendor response layout, and a shape change must degrade to "no baseline"
    rather than crash the run that carries the actual finding.
    """
    scores = getattr(run, "scores", None) or {}
    result = {}
    if isinstance(scores, dict):
        for name, value in scores.items():
            if isinstance(value, (int, float)):
                result[name] = float(value)
            elif isinstance(value, dict) and isinstance(value.get("average"), (int, float)):
                result[name] = float(value["average"])
    return result

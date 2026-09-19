"""Drift detection: deciding, in advance, what "degraded" means.

**The point of writing this down before it happens.** After a failure,
every threshold is negotiable — there is always a reason the number that just
fired was too strict. A definition agreed while nothing is wrong is the only
kind that survives the first time it is inconvenient. So the thresholds in
``DEGRADED`` are committed here, in code, reviewable in a diff, and the
project's position is that changing one is a decision to be argued for rather
than a knob to be turned during an outage.

**Why a rolling window and not a single failing run.** One bad decision is a
case, not a trend, and paging a human for it teaches them to ignore the
signal. What matters is a *rate* over recent decisions: the register keeps
producing new shapes of transition, and the failure mode this watches for is
the agent getting quietly worse at them, not one anomalous record.

**Why counts as well as rates.** A rate over three decisions is noise. The
window must hold at least ``MIN_WINDOW`` scored decisions before any verdict
is issued at all — below that the honest answer is "not enough evidence",
which is deliberately a different answer from "healthy".

The scores this reads are the ones ``obs/online.py`` writes, so drift is
measured on live traffic with no golden case involved.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Fewer scored decisions than this and no verdict is issued. Three failures
# out of four is not a trend; it is a Tuesday with very little traffic.
MIN_WINDOW = 20

# How many recent scored decisions the verdict is computed over.
WINDOW_SIZE = 100


# The definition of degraded, agreed before it fires. Each entry is the
# minimum acceptable pass rate for that scorer over the window.
DEGRADED: dict[str, float] = {
    # The one rule, on live traffic. A filed decision that cites nothing, or
    # cites something other than the record's own document, is untraceable -
    # CLAUDE.md calls that a regression regardless of any other number, so
    # this is the strictest threshold here and is deliberately not a round
    # 1.0 only because a doklink can be malformed upstream.
    "cited_source_present": 0.98,
    # Structural, not probabilistic: an uncovered case escalating is decided
    # by code, not by a model. Any failure at all means the decision
    # architecture has leaked and the rate should be 1.0.
    "escalated_when_uncovered": 1.0,
    # Also structural. A run that wrote both a filing and an escalation, or
    # neither, is a bug rather than a quality drop.
    "stayed_in_tool_surface": 1.0,

    # --- Grounded decisions: the only ones no person reviews. ---
    #
    # These thresholds are written here *before the first grounded run ever
    # executes*, which is this module's entire discipline. Agreeing what
    # "degraded" means while nothing is wrong is the only kind of agreement
    # that survives the first time it is inconvenient - and grounding is
    # exactly where that temptation will arrive, because a threshold that
    # fires is a threshold standing between a shipped decision and a reader.

    # A cited clause the agent was never shown is a fabrication, full stop -
    # not a quality gradient. Code decides this against a list, so anything
    # below 1.0 means the model invented a clause number and the system
    # recorded it as a decision.
    "clause_id_exists": 1.0,

    # The quote must be findable in the document. Not 1.0, and the gap is
    # deliberate: PDF text extraction is genuinely lossy at column and page
    # boundaries, so a real quote can occasionally fail to match through no
    # fault of the model. 0.95 tolerates that; it does not tolerate a model
    # that has started paraphrasing, which shows up as a rate in the 0.7s.
    # This is the scorer demo 1 breaks on purpose.
    "clause_is_verbatim": 0.95,

    # The weakest of the grounded scorers and the loosest threshold, honestly
    # so: it is a keyword floor, and Danish plan clauses phrase the same
    # restriction many ways. A rate below 0.85 means quotes have stopped
    # being about the field that changed, which is a retrieval problem rather
    # than a generation one.
    "quote_mentions_the_changed_field": 0.85,

    # Structural, like escalated_when_uncovered: a run that could not ground
    # a case and acted anyway has leaked past the one code path that leads to
    # a person. There is no acceptable rate for that other than 1.0.
    "abstained_when_ungrounded": 1.0,

    # The field-citation counterpart to clause_id_exists, added with stage 1's
    # second kind of citation, and 1.0 for the same reason: a decision resting
    # on "status: F -> V" when the record says no such thing is a fabrication,
    # not a quality gradient. Code compares it against the stored record, so
    # there is nothing here to be lossy about - unlike clause_is_verbatim,
    # which tolerates PDF extraction noise. Written before the first
    # field-cited decision ever executed, which is this module's discipline.
    "field_citation_is_real": 1.0,
}


@dataclass(frozen=True)
class ScorerWindow:
    """One scorer's results over the recent window."""

    name: str
    values: list[float] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def pass_rate(self) -> float:
        if not self.values:
            return 1.0
        return sum(self.values) / len(self.values)


@dataclass(frozen=True)
class DriftVerdict:
    """The answer, with its reasoning attached.

    ``status`` is one of "healthy", "degraded", or "insufficient_evidence" -
    three states, not two, because "we do not have enough decisions to say"
    must never be reported as healthy.
    """

    status: str
    lines: list[str]
    failing_scorers: list[str]

    @property
    def is_degraded(self) -> bool:
        return self.status == "degraded"


def evaluate_window(
    windows: dict[str, ScorerWindow],
    *,
    thresholds: dict[str, float] | None = None,
    min_window: int = MIN_WINDOW,
) -> DriftVerdict:
    """Judges the current window against the committed definition."""
    thresholds = thresholds if thresholds is not None else DEGRADED
    lines: list[str] = []
    failing: list[str] = []

    scored = max((w.n for w in windows.values()), default=0)
    if scored < min_window:
        return DriftVerdict(
            "insufficient_evidence",
            [f"only {scored} scored decisions in the window; {min_window} needed for a verdict"],
            [],
        )

    for name in sorted(thresholds):
        minimum = thresholds[name]
        window = windows.get(name)
        if window is None or window.n == 0:
            lines.append(f"?     {name}  not scored in this window")
            continue
        rate = window.pass_rate
        ok = rate >= minimum
        lines.append(
            f"{'ok  ' if ok else 'DRIFT'}  {name}  {rate:.3f} (min {minimum:.3f}) n={window.n}"
        )
        if not ok:
            failing.append(name)

    return DriftVerdict("degraded" if failing else "healthy", lines, failing)


def fetch_windows(*, window_size: int = WINDOW_SIZE) -> dict[str, ScorerWindow]:
    """Reads the most recent online scores back out of Langfuse.

    Returns an empty mapping rather than raising when Langfuse is
    unconfigured or unreachable: an alerting path that itself crashes is
    worse than one that reports no evidence, and evaluate_window turns an
    empty window into "insufficient_evidence" rather than a false all-clear.
    """
    from tilsynsagent.obs.langfuse_setup import observability_enabled

    if not observability_enabled():
        return {}

    try:
        from langfuse import get_client

        client = get_client()
        windows: dict[str, ScorerWindow] = {}
        for name in DEGRADED:
            # scores_v3.get_many_v3 is the current list endpoint on the
            # Langfuse SDK - verified live 2026-09-04 against this exact
            # client version; the older score_v_2.get does not exist here.
            response = client.api.scores_v3.get_many_v3(name=name, limit=window_size)
            values = [
                float(item.value)
                for item in response.data
                if isinstance(getattr(item, "value", None), (int, float))
            ]
            windows[name] = ScorerWindow(name, values)
        return windows
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("drift windows could not be fetched: %s", exc)
        return {}


def _push_worthy(verdict: DriftVerdict) -> set[str]:
    """Which failing scorers are worth putting in front of a person.

    All of them, currently - but expressed as a function rather than inlined
    because the annotation queue's value depends entirely on not containing
    everything (obs/annotation.py), and the day a scorer starts firing on
    noise this is where it gets excluded, visibly.
    """
    return set(verdict.failing_scorers)


def _push_failing_traces(scorer: str) -> None:
    """Puts the traces that failed this scorer into the annotation queue.

    Never raises, and a no-op without Langfuse keys - see obs/annotation.py.
    A drift verdict must still be reported even if the queue is unreachable;
    the alert file is the durable artefact and the queue is a convenience on
    top of it.
    """
    try:
        from tilsynsagent.obs.annotation import push_failing_traces

        pushed = push_failing_traces(scorer=scorer)
        if pushed:
            print(f"queued {len(pushed)} failing trace(s) for annotation")
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("failing traces for %s could not be queued: %s", scorer, exc)


def main() -> int:
    """`python -m tilsynsagent.obs.drift` prints the current verdict.

    Exit codes mirror evals/gate.py deliberately: 0 healthy, 1 degraded,
    2 inconclusive. An alerting cron can act on the code alone.
    """
    from dotenv import load_dotenv

    load_dotenv()

    windows = fetch_windows()
    verdict = evaluate_window(windows)
    for line in verdict.lines:
        print(line)
    print()
    print(f"status: {verdict.status}")

    if verdict.is_degraded:
        # The alert is written *here*, at the moment the verdict is reached,
        # rather than by whatever cron invoked this. A degraded verdict that
        # only exists as an exit code is a verdict nobody can read next month
        # - see obs/alerts.py on why the record has to outlive Langfuse's
        # 30-day retention.
        from tilsynsagent.obs.alerts import business_alert

        for name in verdict.failing_scorers:
            window = windows.get(name)
            path = business_alert(
                scorer=name,
                rate=window.pass_rate if window else 0.0,
                minimum=DEGRADED[name],
                window_size=window.n if window else 0,
                queue_url=os.environ.get("LANGFUSE_QUEUE_URL"),
            )
            print(f"alert written: {path}")
            if window and name in _push_worthy(verdict):
                _push_failing_traces(name)

    from tilsynsagent import obs

    obs.shutdown()

    if verdict.status == "degraded":
        return 1
    if verdict.status == "insufficient_evidence":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

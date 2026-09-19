"""Measures agreement between the LLM judge and human review verdicts.

The judge itself is a Langfuse-configured evaluator that runs on traces
tagged ``grounded`` and emits a score called
``judge_clause_supports_conclusion``. This module pairs that score with the
human verdict on the same trace and reports the two directions of
disagreement separately:

- ``judge_false_flag`` — the judge flagged a decision the human said was fine.
- ``judge_false_pass`` — the judge passed a decision the human said was wrong.

The judge never decides an outcome; it only flags for human attention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = REPO_ROOT / "var" / "evals"

JUDGE_SCORE_NAME = "judge_clause_supports_conclusion"

# Below this many paired observations, no agreement figure is published.
MIN_PAIRS = 5


@dataclass(frozen=True)
class Agreement:
    """How often the judge and the humans said the same thing."""

    n: int
    agreed: int
    judge_false_flag: int
    judge_false_pass: int

    @property
    def rate(self) -> float:
        return self.agreed / self.n if self.n else 0.0

    @property
    def false_pass_rate(self) -> float:
        """Of the decisions a human called wrong, how many the judge passed."""
        wrong = self.judge_false_pass + (self.agreed_on_wrong)
        return self.judge_false_pass / wrong if wrong else 0.0

    agreed_on_wrong: int = 0


def compute_agreement(pairs: list[tuple[bool, bool]]) -> Agreement:
    """Agreement over (judge_flagged, human_said_wrong) pairs.

    ``judge_flagged`` is True when the judge scored the decision as
    unsupported; ``human_said_wrong`` is True when a reviewer picked any
    verdict other than "correct".
    """
    agreed = sum(1 for j, h in pairs if j == h)
    false_flag = sum(1 for j, h in pairs if j and not h)
    false_pass = sum(1 for j, h in pairs if not j and h)
    agreed_on_wrong = sum(1 for j, h in pairs if j and h)
    return Agreement(
        n=len(pairs),
        agreed=agreed,
        judge_false_flag=false_flag,
        judge_false_pass=false_pass,
        agreed_on_wrong=agreed_on_wrong,
    )


def fetch_pairs() -> list[tuple[bool, bool]]:
    """Pairs a human verdict with the judge's score on the same trace.

    Returns [] when either side is unavailable.
    """
    from tilsynsagent.obs.annotation import CORRECT_VERDICT_VALUE, fetch_completed
    from tilsynsagent.obs.langfuse_setup import observability_enabled

    if not observability_enabled():
        return []

    completed = fetch_completed()
    if not completed:
        return []

    try:
        from langfuse import get_client

        client = get_client()
    except Exception as exc:  # noqa: BLE001
        logger.warning("langfuse client unavailable for judge agreement: %s", exc)
        return []

    pairs: list[tuple[bool, bool]] = []
    for item in completed:
        verdict = item.get("verdict")
        if verdict is None:
            continue
        human_wrong = str(verdict).lower() != "correct" and verdict != CORRECT_VERDICT_VALUE
        judge_flagged = _judge_flagged(client, item["trace_id"])
        if judge_flagged is None:
            continue
        pairs.append((judge_flagged, human_wrong))
    return pairs


def _judge_flagged(client, trace_id: str) -> bool | None:
    """Whether the judge scored this trace as unsupported. None if unscored."""
    try:
        response = client.api.scores_v3.get_many_v3(
            name=JUDGE_SCORE_NAME, trace_id=trace_id, limit=5
        )
        for item in getattr(response, "data", []) or []:
            value = getattr(item, "value", None)
            if isinstance(value, (int, float)):
                return value < 1.0
    except Exception as exc:  # noqa: BLE001
        logger.warning("judge score for trace %s could not be read: %s", trace_id, exc)
    return None


def render_report(agreement: Agreement, *, on: date) -> str:
    return f"""\
# Judge agreement — {on.isoformat()}

How often the LLM judge (`{JUDGE_SCORE_NAME}`, configured in Langfuse) agreed
with the person who reviewed the same decision.

| | |
|---|---|
| Paired observations | {agreement.n} |
| Agreed | {agreement.agreed} ({agreement.rate:.0%}) |
| **Judge passed, human said wrong** (`judge_false_pass`) | **{agreement.judge_false_pass}** |
| Judge flagged, human said fine (`judge_false_flag`) | {agreement.judge_false_flag} |

## Reading this

The two disagreement columns are not equivalent and must never be averaged
into one number.

`judge_false_flag` costs a person a few minutes: the judge raised something
that turned out to be fine. That is the cheap direction, and a judge tuned to
be slightly noisy there is a judge doing its job.

**`judge_false_pass` is the number that matters.** Each one is a decision the
agent got wrong, that the judge waved through, and that reached a reader
because a human happened to look. At {agreement.judge_false_pass} of
{agreement.judge_false_pass + agreement.agreed_on_wrong} human-identified
errors, the judge's miss rate on real errors is
{agreement.false_pass_rate:.0%}.

## What this does not license

The judge does not decide anything. It flags grounded decisions for human
attention, and this report is why it is *evaluated rather than trusted*. A
judge is a model, and a model's verdict is not a citation — measuring it is
the only thing that distinguishes using one from believing one.
"""


def write_agreement_report(*, on: date | None = None, directory: Path | None = None) -> Path | None:
    """Computes agreement and writes a report. Returns the path, or None if
    there are too few paired observations to publish a figure."""
    on = on or date.today()
    pairs = fetch_pairs()
    if len(pairs) < MIN_PAIRS:
        logger.info(
            "judge agreement not published: %d paired observation(s), %d needed",
            len(pairs),
            MIN_PAIRS,
        )
        return None

    agreement = compute_agreement(pairs)
    directory = directory or REPORTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"judge-agreement-{on.isoformat()}.md"
    path.write_text(render_report(agreement, on=on))
    return path

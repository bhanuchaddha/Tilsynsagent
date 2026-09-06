"""The LLM judge: measuring it, not trusting it.

**What the judge is, and where it lives.** The judge itself is *not in this
file*. It is a Langfuse-configured evaluator, set up in the Langfuse UI, that
runs on traces tagged ``grounded`` and emits a score called
``judge_clause_supports_conclusion``. It answers the one question the code
scorers structurally cannot: the agent quoted a real clause, verbatim, about
roughly the right subject - but does that clause actually support the
conclusion drawn from it?

**Its configuration is documented verbatim in `docs/judge-configuration.md`,
and that is not bureaucracy.** It is config, not code: it cannot be reviewed
in a pull request, cannot be unit-tested, and disappears entirely if this
project is recreated in a fresh Langfuse workspace. A component that shapes
production decisions and exists only as settings in a vendor UI is the single
most fragile thing in this system, and writing it down is the only mitigation
available.

**What this module owns is agreement, not judgement.** The judge produces
scores; humans produce verdicts in the annotation queue. This module measures
how often they say the same thing, and reports the two directions of
disagreement separately:

- ``judge_false_flag`` - the judge said a decision was unsupported; the human
  said it was fine. Annoying. It costs a person a few minutes of review.
- ``judge_false_pass`` - the judge said a decision was fine; the human said it
  was wrong. **This is the number that matters.** A judge that over-flags
  wastes attention; a judge that under-flags is the thing standing between a
  bad decision and a reader, not doing its job.

Reporting a single "agreement: 0.86" would average those together and hide
exactly the asymmetry that decides whether the judge is trustworthy.

**Why agreement is computed in the nightly and not inline.** Human labels
arrive days after the judge scores they are about - someone has to open the
queue. Computing agreement at decision time would measure an empty set and
report a confident 1.000 about nothing.

**The surviving rule: a judge may never decide an outcome, nor be the only
thing between a decision and a reader.** Nothing in this module writes a
decision, and the judge's score never routes anything. It flags for human
attention, and its own accuracy at doing that is what is measured here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DIR = REPO_ROOT / "docs" / "evals"

JUDGE_SCORE_NAME = "judge_clause_supports_conclusion"

# Below this many paired observations, no agreement figure is published. Same
# reasoning as drift.py's MIN_WINDOW: a rate over four labels is not a
# measurement, and publishing one invites a decision to be made on it.
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
        """Of the decisions a human called wrong, how many the judge passed.

        The denominator is human-wrong cases, not all cases: a judge that
        never flags anything on a healthy population scores well on overall
        agreement and terribly here, which is the correct reading of it.
        """
        wrong = self.judge_false_pass + (self.agreed_on_wrong)
        return self.judge_false_pass / wrong if wrong else 0.0

    # Set by compute_agreement; kept as a plain attribute rather than derived
    # because it needs the pairing that produced the counts.
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

    Returns [] when either side is unavailable - no keys, no completed
    annotations, or no judge configured. An empty list produces no report,
    which is the honest outcome: "the judge has not been measured" must not
    render as "the judge agrees".
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
            # The judge did not score this trace at all. Excluded rather than
            # counted as a pass: an unscored trace is missing evidence, and
            # treating missing evidence as agreement is how a judge comes to
            # look better than it is.
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

How often the LLM judge (`{JUDGE_SCORE_NAME}`, configured in Langfuse — see
`docs/judge-configuration.md`) agreed with the person who reviewed the same
decision.

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
    """Computes agreement and commits it to docs/. Returns the path, or None.

    Returns None when there are too few pairs to say anything - which is a
    normal state for a system whose queue nobody has opened this week, and is
    reported as silence rather than as a figure.
    """
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
    directory = directory or DOCS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"judge-agreement-{on.isoformat()}.md"
    path.write_text(render_report(agreement, on=on))
    return path

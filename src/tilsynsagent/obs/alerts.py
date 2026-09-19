"""Writes alert files recording that something got worse, committed to the
repo so they outlive any observability vendor's retention window.

A repeat alert on the same day, for the same kind and subject, appends a
timestamped occurrence to the existing file instead of creating a new one.

``business_alert`` is fired on a production quality drop and points at the
annotation queue. ``developer_alert`` is fired by the nightly regression
run and names cases and prompt versions.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ALERTS_DIR = Path(__file__).resolve().parents[3] / "var" / "alerts"

# demo/reset.py deletes files by this prefix alone.
DEMO_PREFIX = "demo-"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "alert"


def alert_path(*, kind: str, subject: str, on: date | None = None, demo: bool = False) -> Path:
    """``var/alerts/<date>-<kind>-<slug>.md``."""
    on = on or date.today()
    prefix = DEMO_PREFIX if demo else ""
    return ALERTS_DIR / f"{prefix}{on.isoformat()}-{_slug(kind)}-{_slug(subject)}.md"


def write_alert(
    *,
    kind: str,
    subject: str,
    title: str,
    body: str,
    on: date | None = None,
    demo: bool = False,
    directory: Path | None = None,
) -> Path:
    """Writes an alert, or appends an occurrence to today's existing one.

    Returns the path either way. Never raises on a write failure.
    """
    path = alert_path(kind=kind, subject=subject, on=on, demo=demo)
    if directory is not None:
        path = directory / path.name
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with open(path, "a") as f:
                f.write(f"\n## Recurrence — {stamp}\n\n{body.strip()}\n")
        else:
            path.write_text(
                f"# {title}\n\n"
                f"*Opened {stamp} by `tilsynsagent` — {kind}.*\n\n"
                f"{body.strip()}\n"
            )
    except OSError as exc:  # pragma: no cover
        logger.warning("alert %s could not be written: %s", path, exc)
    return path


def business_alert(
    *,
    scorer: str,
    rate: float,
    minimum: float,
    window_size: int,
    queue_url: str | None = None,
    demo: bool = False,
    directory: Path | None = None,
) -> Path:
    """Production quality dropped on live decisions. Written for someone who
    will never open the code; says nothing about prompts, versions or cases.
    """
    body = f"""\
## What happened

The agent's production quality dropped on a check called **`{scorer}`**.

Over the last **{window_size}** decisions it made without a person in the
path, it passed that check **{rate:.0%}** of the time. The agreed minimum is
**{minimum:.0%}**, written down before this happened.

## What that check means

{_scorer_in_plain_language(scorer)}

## What is needed from a person

The decisions that failed have been put in an annotation queue. Each one
shows the change the agent saw, what it decided, and the clause from the plan
document it used to decide.

For each, the question is only: **was the agent right?**

That judgement is the thing no amount of code can supply, and the labels it
produces become test cases, so the same failure cannot come back unnoticed.

{f"Queue: {queue_url}" if queue_url else "Queue: Langfuse -> Annotation Queues -> grounded-decisions"}

## What happens next without a person

Nothing breaks. The agent keeps running. But it keeps making this class of
decision at this quality until someone says which of them were wrong.
"""
    return write_alert(
        kind="quality-drop",
        subject=scorer,
        title=f"Production quality dropped: {scorer} at {rate:.2f}",
        body=body,
        demo=demo,
        directory=directory,
    )


def developer_alert(
    *,
    current_score: float,
    previous_score: float,
    regressed_scorers: list[str],
    new_case_ids: list[str],
    newly_failing_case_ids: list[str],
    prompt_versions: dict[str, str] | None = None,
    previous_prompt_versions: dict[str, str] | None = None,
    demo: bool = False,
    directory: Path | None = None,
) -> Path:
    """The nightly eval regressed against the previous run.

    Separates new cases (a failure the dataset gained on purpose) from
    newly failing cases (a real regression) since they call for opposite
    responses.
    """
    prompt_versions = prompt_versions or {}
    previous_prompt_versions = previous_prompt_versions or {}

    changed_prompts = [
        f"- `{name}`: `{previous_prompt_versions.get(name, 'unknown')}` -> `{version}`"
        for name, version in sorted(prompt_versions.items())
        if previous_prompt_versions.get(name) != version
    ]

    body = f"""\
## The regression

Mean score **{previous_score:.3f} -> {current_score:.3f}** against the previous
nightly run.

Scorers below tolerance: {", ".join(f"`{s}`" for s in regressed_scorers) or "none individually"}

## New cases in this run ({len(new_case_ids)})

{_bullets(new_case_ids) or "None — the dataset did not change."}

Cases the dataset gained since the last run. A new case that fails is not a
regression: it is a case a person added *because* the agent got it wrong.
The score dropping here is the dataset doing its job.

## Cases that used to pass and now fail ({len(newly_failing_case_ids)})

{_bullets(newly_failing_case_ids) or "None."}

These are regressions. The dataset did not change; the behaviour did.

## Prompt versions

{chr(10).join(changed_prompts) if changed_prompts else "Unchanged since the previous run."}

{"A prompt version changed between these two runs, which is the first thing to look at: a prompt edit changes behaviour exactly as much as a code change and does not appear in any code diff." if changed_prompts else "No prompt changed, so a behaviour change here is a model-side or data-side one."}

## How to fix it without a deploy

If a prompt is at fault, the fix is a new version in Langfuse with the
`production` label moved to it. That takes effect within the prompt cache TTL
and requires no code change and no deploy.
"""
    return write_alert(
        kind="eval-regression",
        subject=f"{previous_score:.2f}-to-{current_score:.2f}",
        title=f"Nightly eval regression: {previous_score:.3f} -> {current_score:.3f}",
        body=body,
        demo=demo,
        directory=directory,
    )


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- `{i}`" for i in items)


def _scorer_in_plain_language(scorer: str) -> str:
    """What a scorer means, for a reader who has never seen the code."""
    return {
        "clause_is_verbatim": (
            "When the agent decides something by reading the plan document, it must "
            "quote the sentence it relied on **exactly as written**. This check looks "
            "for that sentence in the actual document. A failure means the agent's "
            "quote cannot be found — it reworded it, or changed a number — so a reader "
            "cannot check the decision against the source."
        ),
        "clause_id_exists": (
            "The agent must name which numbered clause of the plan it used. This check "
            "confirms that clause was actually one it was shown. A failure means it "
            "named a clause it never read, which makes the decision impossible to trace."
        ),
        "quote_mentions_the_changed_field": (
            "The clause the agent quoted should be about the thing that changed. A "
            "failure means it quoted a real clause about something else — a height "
            "restriction used to justify a decision about permitted use, for example."
        ),
        "abstained_when_ungrounded": (
            "When the plan document does not settle a case, the agent must hand it to a "
            "person rather than decide anyway. A failure here means it decided something "
            "it had no basis for, which is the one thing this system is built to prevent."
        ),
        "cited_source_present": (
            "Every decision must link to the document that justified it. A failure means "
            "a decision was recorded with no way back to its source."
        ),
        "escalated_when_uncovered": (
            "A case the written rules do not cover must go to a person. A failure means "
            "the agent acted on something nobody wrote a rule for."
        ),
        "stayed_in_tool_surface": (
            "Each decision should record exactly one kind of outcome. A failure means the "
            "system recorded a contradictory pair, which is a bug rather than a judgement."
        ),
    }.get(scorer, f"The automated check `{scorer}` is failing more often than agreed.")


def open_alerts(*, directory: Path | None = None) -> list[dict]:
    """Every alert file on disk, newest first."""
    directory = directory or ALERTS_DIR
    if not directory.exists():
        return []
    alerts = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        if path.name == "README.md":
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        first_line = next((line for line in text.splitlines() if line.startswith("# ")), "")
        alerts.append(
            {
                "path": str(path),
                "name": path.name,
                "title": first_line.lstrip("# ").strip() or path.stem,
                "is_demo": path.name.startswith(DEMO_PREFIX),
                "recurrences": text.count("## Recurrence"),
            }
        )
    return alerts


def clear_demo_alerts(*, directory: Path | None = None) -> int:
    """Deletes demo alert files only - see DEMO_PREFIX."""
    directory = directory or ALERTS_DIR
    if not directory.exists():
        return 0
    removed = 0
    for path in directory.glob(f"{DEMO_PREFIX}*.md"):
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("could not remove demo alert %s: %s", path, exc)
    return removed

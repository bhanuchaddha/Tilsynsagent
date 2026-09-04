"""Runs one golden case through the LLM step its expected route exercises.

Deliberately does not go through graph.py's compiled graph: the graph writes
filings/escalations to Postgres as a side effect, and an eval run replaying
34+N cases must not create 34+N rows of fake data in the real register.
Instead this calls assess()/summarise() directly - the same functions the
graph nodes call - with inputs built straight from the case record, so the
prompt each case receives is identical to what a live run would send.

GROQ_MODEL is read at import time by llm/assess.py and llm/summarise.py (see
those modules' DEFAULT_MODEL). run_baseline.py must load_dotenv() and import
this module *inside* main(), after the environment is set, or a baseline
silently measures the wrong model - see the plan's stated risk on this exact
point.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from groq import RateLimitError

from tilsynsagent.llm.assess import DEFAULT_MODEL as ASSESS_DEFAULT_MODEL
from tilsynsagent.llm.assess import Assessment, assess
from tilsynsagent.llm.summarise import DEFAULT_MODEL as SUMMARISE_DEFAULT_MODEL
from tilsynsagent.llm.summarise import summarise
from tilsynsagent.llm.usage import Usage, take_last_usage
from tilsynsagent.rules.engine import apply_rules


@dataclass(frozen=True)
class TaskResult:
    case_id: str
    route: str  # "file" | "escalate" | "not_covered"
    output_text: str | None  # the summary or a rendering of the assessment; None for escalate
    error: str | None
    usage: Usage | None
    latency_ms: float | None


def _with_rate_limit_retry(fn, *, max_attempts: int = 3):
    """Groq's free tier caps at 8000 TPM per docs/sources.md-adjacent
    verification (observed live during baseline runs); a burst of
    concurrent eval calls trips it. One retry with the delay Groq's own
    error message states is a real operating property of this tier, not a
    hand-rolled backoff policy - the 429 body names the exact wait."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except RateLimitError as exc:
            last_exc = exc
            wait_s = 2.0 * (attempt + 1)
            time.sleep(wait_s)
    raise last_exc


def _sub_area_description(case: dict) -> str:
    source = case.get("source", {})
    return (
        f"{source.get('municipality', '?')}, plan {source.get('plan_id', '?')}, "
        f"sub-area {source.get('sub_area', '?')}"
    )


def run_case(case: dict) -> TaskResult:
    """Reproduces the exact decision the live graph would make for this
    case: apply_rules first (deterministic, no model), then the matching LLM
    step only when the route calls for one. A "file"-labelled golden case
    still runs through the real rule engine rather than trusting the label,
    the same as tests/test_rules_engine.py's golden-dataset check - if the
    engine disagrees with the label here, that is itself a finding, not a
    bug to route around."""
    doklink = case.get("source", {}).get("document", "")
    decision = apply_rules(case["changed_fields"], case["before"], case["after"])
    take_last_usage()  # clear any stale value from a previous case

    if decision.outcome.value == "file":
        try:
            start = time.monotonic()
            summary = _with_rate_limit_retry(
                lambda: summarise(
                    sub_area_description=_sub_area_description(case),
                    changed_fields=case["changed_fields"],
                    rule=decision.rule,
                    rule_reason=decision.reason,
                    doklink=doklink,
                )
            )
            latency_ms = (time.monotonic() - start) * 1000
            usage = take_last_usage()
            return TaskResult(
                case_id=case["id"],
                route="file",
                output_text=summary,
                error=None,
                usage=usage,
                latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001 - captured as a scoreable error, not raised
            return TaskResult(
                case_id=case["id"], route="file", output_text=None,
                error=str(exc), usage=None, latency_ms=None,
            )

    if decision.outcome.value == "not_covered":
        try:
            start = time.monotonic()
            result: Assessment = _with_rate_limit_retry(
                lambda: assess(
                    sub_area_description=_sub_area_description(case),
                    changed_fields=case["changed_fields"],
                    doklink=doklink,
                )
            )
            latency_ms = (time.monotonic() - start) * 1000
            usage = take_last_usage()
            output_text = (
                f"{result.what_is_unclear} {result.what_a_person_must_decide} "
                f"{result.citation}"
            )
            return TaskResult(
                case_id=case["id"],
                route="not_covered",
                output_text=output_text,
                error=None,
                usage=usage,
                latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001
            return TaskResult(
                case_id=case["id"], route="not_covered", output_text=None,
                error=str(exc), usage=None, latency_ms=None,
            )

    # "escalate" (rule-decided) never calls the model.
    return TaskResult(
        case_id=case["id"],
        route=decision.outcome.value,
        output_text=None,
        error=None,
        usage=None,
        latency_ms=None,
    )


def resolved_prompts() -> dict[str, str]:
    """The prompt version each LLM step resolves to right now, for the
    baseline header. A score is only comparable against another score whose
    prompt version is also known - see llm/prompts.py on why the registry
    exists. Recorded the same way and for the same reason as
    resolved_models(): measured, never assumed."""
    from tilsynsagent.llm.prompts import (
        ASSESS_PROMPT_NAME,
        SUMMARISE_PROMPT_NAME,
        get_prompt,
    )

    return {
        "assess": get_prompt(ASSESS_PROMPT_NAME).label,
        "summarise": get_prompt(SUMMARISE_PROMPT_NAME).label,
    }


def resolved_models() -> dict[str, str]:
    """The GROQ_MODEL each LLM step actually resolved to, for the baseline
    header - see this module's docstring on why this must be recorded, not
    assumed."""
    return {"assess": ASSESS_DEFAULT_MODEL, "summarise": SUMMARISE_DEFAULT_MODEL}

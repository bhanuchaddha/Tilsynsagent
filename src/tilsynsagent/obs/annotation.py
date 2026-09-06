"""The annotation queue: putting a production decision in front of a person
who will never open the code.

**Why this is the missing link and not a nice-to-have.** Before this module,
a golden case could only be born two ways: someone hand-labelled a historical
transition, or someone answered an escalation in the review screen. Both
require the system to have *asked*. Grounded decisions never ask - that is
what makes them able to degrade quietly - so without a path from "a
production run scored badly" to "a person looked at it", the feedback loop
has a detector and no way to act on what it detects.

**Only failures go in the queue, and this is the design decision the module
lives or dies by.** A queue containing every grounded decision is a queue
nobody opens; the second week it has 400 items in it, it becomes a backlog,
and a backlog is indistinguishable from an empty queue in terms of what it
actually causes to happen. So ``push_failing_traces`` pushes a trace only
when it failed a code scorer, or when the judge disagreed with the code
scorers. Everything else is left out on purpose, and the cost of that choice
is stated plainly: a decision that is wrong in a way no scorer noticed will
not reach a person through this path. It reaches them through the register
being wrong, later, which is exactly why the judge exists.

**Never raises, no-ops without keys.** Same contract as everything else in
``obs/``: an observability failure must never fail a decision that has
already been made and written.

Verified live 2026-09-05 against this SDK version: ``score_configs.create``
-> ``create_queue`` -> ``create_queue_item(object_id=<trace id>,
object_type='TRACE')`` returns a PENDING item. That full round trip is the
reason this module exists in this shape rather than a guessed one.
"""

from __future__ import annotations

import logging
import os

from tilsynsagent.obs.langfuse_setup import observability_enabled

logger = logging.getLogger(__name__)

# Overridable because Langfuse's free (Hobby) tier caps a project at **one**
# annotation queue, and that cap is enforced on create with a 405, not on
# list - verified live 2026-09-05. So an existing queue under a different
# name cannot be replaced, only adopted; see ensure_queue.
QUEUE_NAME = os.environ.get("TILSYNSAGENT_ANNOTATION_QUEUE", "grounded-decisions")

# The score a person assigns in the queue. Categorical rather than numeric:
# the question put to a business reviewer is "was the agent right?", and the
# honest answer set includes "the document and the register disagree" - the
# third failure class, which is neither a right answer nor a wrong one but a
# statement that the truth itself is contested. A 0-1 slider would force that
# case into a number and lose the only information it carries.
SCORE_CONFIG_NAME = "human_verdict"
# Values must be distinct - Langfuse rejects a categorical config with a
# duplicate value ("Duplicate category value: 0", verified live 2026-09-05),
# so the three ways of being wrong cannot all be 0. That turns out to be the
# better shape anyway: WRONG_VERDICT_VALUES below is what makes "wrong"
# checkable, and the distinct values preserve *how* it was wrong, which is
# what tells a developer whether to fix retrieval, the prompt, or the rules.
VERDICT_OPTIONS = [
    {"label": "correct", "value": 1},
    {"label": "wrong - should have escalated", "value": 0},
    {"label": "wrong - decided the opposite", "value": -1},
    {"label": "register and document disagree", "value": -2},
]

# Everything that is not "correct". Named rather than written as `!= 1` at
# each call site, because "register and document disagree" is not the agent
# being wrong in the ordinary sense - it is the third failure class, where
# the truth itself is contested - and a reader should see that it is
# deliberately grouped here rather than assume nobody thought about it.
CORRECT_VERDICT_VALUE = 1
WRONG_VERDICT_VALUES = frozenset({0, -1, -2})

# How many failing traces one drift verdict may enqueue. A degraded window is
# up to 100 decisions; queueing all of them would recreate the backlog this
# module exists to avoid.
MAX_PUSH_PER_RUN = 10


def _client():
    if not observability_enabled():
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        logger.warning("langfuse client unavailable: %s", exc)
        return None


def ensure_score_config(*, name: str = SCORE_CONFIG_NAME) -> str | None:
    """Creates the categorical score a reviewer picks from, if absent.

    Returns its id, or None when Langfuse is unconfigured. Idempotent by
    lookup-then-create rather than by upsert, because the API has no upsert
    and a duplicate config would split one question's answers across two
    score names - which looks like missing data rather than an error.
    """
    client = _client()
    if client is None:
        return None
    try:
        existing = client.api.score_configs.get(limit=100)
        for config in getattr(existing, "data", []) or []:
            if getattr(config, "name", None) == name:
                return config.id
        created = client.api.score_configs.create(
            name=name,
            data_type="CATEGORICAL",
            categories=VERDICT_OPTIONS,
            description=(
                "Was the agent right about this decision? Answered by a person "
                "reviewing a production run the automated checks flagged."
            ),
        )
        return created.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("score config %r could not be ensured: %s", name, exc)
        return None


def ensure_queue(*, name: str = QUEUE_NAME) -> str | None:
    """Creates the annotation queue, if absent. Returns its id or None."""
    client = _client()
    if client is None:
        return None
    try:
        queues = list(getattr(client.api.annotation_queues.list_queues(limit=100), "data", []) or [])
        for queue in queues:
            if getattr(queue, "name", None) == name:
                return queue.id

        config_id = ensure_score_config()
        if config_id is None:
            # create_queue rejects an empty score_config_ids array outright
            # (400, "expected array to have >=1 items"), so a queue with no
            # score config is not a degraded queue - it is not a queue. Fail
            # here rather than send a request that cannot succeed.
            logger.warning("annotation queue %r needs a score config, which is unavailable", name)
            return None
        try:
            created = client.api.annotation_queues.create_queue(
                name=name,
                description=(
                    "Production decisions the agent made with no person in the path, "
                    "which an automated check flagged. The question is only: was it right?"
                ),
                score_config_ids=[config_id],
            )
            return created.id
        except Exception as exc:  # noqa: BLE001
            # The free tier allows exactly one annotation queue per project,
            # and refuses creation past that with a 405 rather than returning
            # the existing one. Adopting whatever queue is already there is
            # strictly better than doing nothing: the items still reach a
            # person, under a name that is not the one this code would have
            # chosen. Logged loudly, because "your queue has an unexpected
            # name" is a confusing thing to discover silently.
            if "Maximum number of annotation queues" not in str(exc) or not queues:
                logger.warning("annotation queue %r could not be created: %s", name, exc)
                return None
            adopted = queues[0]
            logger.warning(
                "annotation queue limit reached on this Langfuse plan; using the existing "
                "queue %r instead of creating %r. Set TILSYNSAGENT_ANNOTATION_QUEUE to that "
                "name to silence this.",
                getattr(adopted, "name", "?"),
                name,
            )
            return adopted.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("annotation queue %r could not be ensured: %s", name, exc)
        return None


def push_trace(trace_id: str, *, queue_id: str | None = None) -> bool:
    """Adds one trace to the queue. Returns whether it was added.

    Deduplicated by trace id: the same decision must not appear twice, or a
    persistent failure would fill the queue with copies of itself over
    successive nightly runs - the same failure mode obs/alerts.py's
    idempotency prevents in the alert directory.
    """
    client = _client()
    if client is None:
        return False
    queue_id = queue_id or ensure_queue()
    if queue_id is None:
        return False
    try:
        existing = client.api.annotation_queues.list_queue_items(queue_id=queue_id, limit=100)
        for item in getattr(existing, "data", []) or []:
            if getattr(item, "object_id", None) == trace_id:
                return False
        client.api.annotation_queues.create_queue_item(
            queue_id=queue_id, object_id=trace_id, object_type="TRACE"
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("trace %s could not be queued: %s", trace_id, exc)
        return False


def failing_trace_ids(*, scorer: str, limit: int = MAX_PUSH_PER_RUN) -> list[str]:
    """Trace ids whose score for ``scorer`` was a failure.

    **Read from traces, not from the score list, and that is not a style
    choice.** ``scores_v3.get_many_v3`` is a summary endpoint: verified live
    2026-09-05, it returns a score's name, value and timestamp but leaves
    ``comment`` and the trace reference empty, even for a score written with
    an explicit ``trace_id``. Building the queue from it would silently
    enqueue nothing - the alert would fire, the queue would stay empty, and
    the loop would appear to work.

    ``trace.get()`` returns each trace's scores in full, with comment and
    trace id intact, so the queue is built by walking recent traces instead.
    obs/drift.py still uses the summary endpoint, correctly: it needs only
    values to compute a rate.
    """
    client = _client()
    if client is None:
        return []
    try:
        listing = client.api.trace.list(limit=100, tags=["grounded"])
        traces = getattr(listing, "data", []) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("traces could not be listed: %s", exc)
        return []

    ids: list[str] = []
    for summary in traces:
        trace_id = getattr(summary, "id", None)
        if not trace_id:
            continue
        if _trace_failed(client, trace_id, scorer):
            ids.append(trace_id)
        if len(ids) >= limit:
            break
    return ids


def _trace_failed(client, trace_id: str, scorer: str) -> bool:
    """Whether one trace failed a named scorer."""
    try:
        trace = client.api.trace.get(trace_id)
        for score in getattr(trace, "scores", []) or []:
            data = score if isinstance(score, dict) else score.model_dump()
            if data.get("name") != scorer:
                continue
            value = data.get("value")
            if isinstance(value, (int, float)) and value < 1.0:
                return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("trace %s could not be read: %s", trace_id, exc)
    return False


def push_failing_traces(*, scorer: str, limit: int = MAX_PUSH_PER_RUN) -> list[str]:
    """Queues the traces that failed one scorer. Returns what was queued."""
    queue_id = ensure_queue()
    if queue_id is None:
        return []
    pushed = []
    for trace_id in failing_trace_ids(scorer=scorer, limit=limit):
        if push_trace(trace_id, queue_id=queue_id):
            pushed.append(trace_id)
    return pushed


def fetch_completed(*, queue_name: str = QUEUE_NAME) -> list[dict]:
    """Queue items a person has answered, with their verdict.

    Returns ``[{"trace_id":..., "verdict":..., "comment":...}]``. This is what
    evals/promote.py turns into golden cases, and what obs/judge.py measures
    the judge's agreement against.

    Human labels arrive days after the scores they are about, which is why
    agreement is computed in the nightly rather than inline - see
    obs/judge.py.
    """
    client = _client()
    if client is None:
        return []
    queue_id = ensure_queue(name=queue_name)
    if queue_id is None:
        return []
    completed = []
    try:
        items = client.api.annotation_queues.list_queue_items(queue_id=queue_id, limit=100)
        for item in getattr(items, "data", []) or []:
            if getattr(item, "status", None) != "COMPLETED":
                continue
            trace_id = getattr(item, "object_id", None)
            if not trace_id:
                continue
            verdict, comment = _verdict_for(client, trace_id)
            completed.append({"trace_id": trace_id, "verdict": verdict, "comment": comment})
    except Exception as exc:  # noqa: BLE001
        logger.warning("completed queue items could not be fetched: %s", exc)
    return completed


def _verdict_for(client, trace_id: str) -> tuple[str | None, str | None]:
    """The human_verdict score a reviewer left on one trace.

    Read off the trace for the same reason failing_trace_ids is: the score
    list endpoint drops the comment, and a reviewer's comment is the sentence
    that becomes the golden case's ``reason``. Losing it would turn every
    promoted case's justification into a generated placeholder.
    """
    try:
        trace = client.api.trace.get(trace_id)
        for score in getattr(trace, "scores", []) or []:
            data = score if isinstance(score, dict) else score.model_dump()
            if data.get("name") != SCORE_CONFIG_NAME:
                continue
            label = data.get("stringValue") or data.get("string_value") or data.get("value")
            if label is not None:
                return str(label), data.get("comment")
    except Exception as exc:  # noqa: BLE001
        logger.warning("verdict for trace %s could not be read: %s", trace_id, exc)
    return None, None

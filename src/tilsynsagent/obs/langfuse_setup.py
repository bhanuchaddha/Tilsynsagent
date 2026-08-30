"""Langfuse wiring for runner.py, using Langfuse's own LangGraph/LangChain
integration rather than hand-rolled tracing.

This project runs LangGraph directly, not through the `langchain` agent
framework, but Langfuse's ``CallbackHandler`` (langfuse.langchain) is built
on ``langchain_core`` - the same primitives LangGraph itself is built on -
and LangGraph accepts any LangChain-compatible callback via
``config={"callbacks": [...]}}``. That is the supported integration path:
https://langfuse.com/integrations/frameworks/langgraph. The one thing it
needs that this project didn't otherwise pull in is the top-level
``langchain`` package (a version-check import inside the handler, not a
functional dependency - see pyproject.toml).

Every function here is a no-op when LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY
are absent, and ``import langfuse`` happens inside the functions, so the
agent runs with Langfuse uninstalled or unconfigured with zero special
handling anywhere else in the codebase.
"""

from __future__ import annotations

import contextlib
import os


def observability_enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY")) and bool(
        os.environ.get("LANGFUSE_SECRET_KEY")
    )


def get_callback_handler():
    """A langfuse.langchain.CallbackHandler, or None when keys are absent.

    Pass the result in config={"callbacks": [handler]} to graph.invoke(); a
    None entry must be filtered out by the caller (trace_config below does
    this), never passed through as a bare None callback.
    """
    if not observability_enabled():
        return None
    from langfuse.langchain import CallbackHandler

    return CallbackHandler()


def trace_config(base: dict, *, thread_id: str, tags: list[str] | None = None,
                  metadata: dict | None = None) -> dict:
    """Wraps a LangGraph invoke config with Langfuse tracing, when enabled.

    thread_id doubles as the Langfuse session id (via the
    "langfuse_session_id" metadata key the CallbackHandler looks for) so a
    trace is joinable to its LangGraph checkpoint by one string - what makes
    an interrupted (escalated) run's trace explicable after the fact. A
    no-op merge when observability is off: base comes back unchanged.
    """
    handler = get_callback_handler()
    if handler is None:
        return base

    merged_metadata = {"langfuse_session_id": thread_id}
    if metadata:
        merged_metadata.update(metadata)

    config = dict(base)
    config["callbacks"] = [*config.get("callbacks", []), handler]
    config["metadata"] = {**config.get("metadata", {}), **merged_metadata}
    if tags:
        config["tags"] = [*config.get("tags", []), *tags]
    return config


@contextlib.contextmanager
def record_generation(name: str, *, model: str):
    """Wraps one raw Groq call as a Langfuse generation observation, nested
    under whatever span is current (the LangGraph node span the
    CallbackHandler already opened for assess/summarise).

    The CallbackHandler instruments LangChain-shaped calls; assess.py and
    summarise.py call groq.Groq directly, which the handler cannot see. This
    is Langfuse's own generation-observation primitive
    (start_as_current_observation(as_type="generation")), used here instead
    of hand-rolled span bookkeeping, to attach token usage where the
    CallbackHandler has no visibility. A no-op contextmanager yielding None
    when observability is off.

    Usage: with record_generation("assess", model=model) as gen:
               resp = client.chat.completions.create(...)
               if gen is not None:
                   gen.update(usage_details={...})
    """
    if not observability_enabled():
        yield None
        return
    from langfuse import get_client

    client = get_client()
    with client.start_as_current_observation(
        name=name, as_type="generation", model=model
    ) as gen:
        yield gen


def flush() -> None:
    if not observability_enabled():
        return
    from langfuse import get_client

    get_client().flush()


def shutdown() -> None:
    """Flushes and clears the cached Langfuse client. Mandatory before a
    short-lived CLI process exits - Langfuse batches spans, and a process
    that exits without shutdown() can drop the tail of a run's trace."""
    if not observability_enabled():
        return
    from langfuse import get_client

    get_client().shutdown()

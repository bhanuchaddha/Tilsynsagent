"""The LangGraph state graph wiring fetch -> detect -> classify -> (file |
escalate) together, one thread per sub-area diff.

Flow (docs/plan, Phase 1):

    fetch -> detect -> classify -> covered     -> decide (code) -> file -> summarise
                                 -> not covered -> assess (LLM)  -> escalate -> interrupt()

fetch, detect, classify and decide are deterministic Python. assess and
summarise are the model. Escalation calls interrupt() inside the escalate
node, which pauses the graph and persists its state to Postgres via
PostgresSaver until a person resolves it with Command(resume=...).

One graph run processes one sub-area version transition (one diff). The
run-over-many-changes loop lives in runner.py, which invokes this graph once
per new record from the watermark fetch - not as a loop inside the graph
itself, so each diff gets its own checkpoint thread and can be
interrupted/resumed independently of the others.
"""

from __future__ import annotations

import os
from typing import Literal, TypedDict

import psycopg
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import get_config
from langgraph.types import Command, interrupt

from tilsynsagent.actions.escalate import escalate_decision
from tilsynsagent.actions.file import file_decision
from tilsynsagent.actions.permissions import RunBudget
from tilsynsagent.db import repo
from tilsynsagent.detect import diff_versions
from tilsynsagent.llm.assess import assess
from tilsynsagent.llm.summarise import summarise
from tilsynsagent.llm.usage import take_last_usage
from tilsynsagent.rules.engine import RULE_SET_ID, Outcome, apply_rules
from tilsynsagent.sources.plandata import SubAreaRecord


class GraphState(TypedDict, total=False):
    # --- input: set before the graph runs ---
    # A dict (SubAreaRecord._asdict()-shaped), not the dataclass itself: the
    # Postgres checkpointer serializes state via msgpack, which only allows
    # registered types. Keeping the checkpointed state to plain dicts avoids
    # depending on SubAreaRecord's msgpack registration remaining supported.
    record: dict

    # True only for records fed in by demo/seed.py. Marks the sub_area row
    # so demo/reset.py can wipe it without touching live run history - see
    # migrations/003_demo_data.sql. Defaults to False for every real run.
    is_test_data: bool

    # --- detect ---
    sub_area_id: int
    before_version_id: int | None
    after_version_id: int
    before: dict | None
    after: dict
    changed_fields: dict
    # True when the fetched record's feature_id matches the sub-area's last
    # stored version - the same source row re-served, not a new revision.
    # See _route_after_detect: this routes to skip, same as before_version_id
    # being None.
    same_feature: bool

    # --- classify / decide ---
    outcome: Literal["file", "escalate", "not_covered"]
    rule: str | None
    rule_reason: str

    # --- assess (only when the rule engine did not cover the case) ---
    assessment_what_is_unclear: str
    assessment_what_a_person_must_decide: str

    # --- LLM usage, one entry per model call made this run (plain dicts -
    # see the msgpack note on `record` above; Usage.to_dict() produces this
    # shape). assess and summarise each add at most one entry; _escalate_node
    # never adds one, since it makes no LLM call itself and re-executes on
    # resume - see graph.py's docstring on interrupt() re-entry. ---
    usage: list[dict]

    # --- result ---
    result: dict  # {"diff_id":..., "filing_id"|"escalation_id":...} or {"skipped": True}


def _sub_area_description(record: SubAreaRecord) -> str:
    return f"{record.kommunenavn or record.komnr}, plan {record.lokplan_id}, sub-area {record.delnr}"


def run_input(record: SubAreaRecord, *, is_test_data: bool = False) -> dict:
    """The initial state dict for graph.invoke({...}, config=...) - converts
    the fetched record to the plain-dict form GraphState.record requires.

    is_test_data is only ever True from demo/seed.py - see GraphState's
    docstring on the field."""
    return {"record": record.to_dict(), "is_test_data": is_test_data}


def resume_run(graph, thread_id: str) -> dict:
    """Resumes a paused run against its stored thread id (review/app.py, once
    a person has answered the escalation that paused it).

    _escalate_node has already written the escalation row and its resolution
    is written separately by review/app.py before this is called - this call
    exists only to let interrupt() return so the graph reaches END. The value
    passed to Command(resume=...) is unused by _escalate_node (see its
    docstring: nothing further happens after the interrupt() call returns).
    It cannot be None, though: LangGraph's loop only sets its internal
    resume_is_map flag inside the `resume is not None` branch and reads it
    unconditionally right after (pregel/_loop.py), so resume=None raises
    UnboundLocalError before the graph resumes at all. True is passed as an
    arbitrary non-None placeholder.
    """
    return graph.invoke(
        Command(resume=True), config={"configurable": {"thread_id": thread_id}}
    )


def make_graph(database_url: str | None = None):
    """Builds the compiled graph, checkpointed to Postgres.

    Call ``.setup()`` once (see db/migrate.py or runner.py's bootstrap) before
    running - PostgresSaver needs its own checkpoint tables created first.
    Returns (graph, saver_cm) where saver_cm is the context manager owning the
    underlying connection; callers must keep it open for the graph's lifetime.
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    database_url = database_url or os.environ["DATABASE_URL"]
    saver_cm = PostgresSaver.from_conn_string(database_url)
    checkpointer = saver_cm.__enter__()

    builder = StateGraph(GraphState)
    builder.add_node("detect", _detect_node)
    builder.add_node("decide", _decide_node)
    builder.add_node("assess", _assess_node)
    builder.add_node("file", _file_node)
    builder.add_node("escalate", _escalate_node)
    builder.add_node("skip", _skip_node)

    builder.add_edge(START, "detect")
    builder.add_conditional_edges(
        "detect",
        _route_after_detect,
        {"decide": "decide", "skip": "skip"},
    )
    builder.add_conditional_edges(
        "decide",
        _route_after_decide,
        {"file": "file", "escalate": "escalate", "assess": "assess"},
    )
    builder.add_edge("assess", "escalate")
    builder.add_edge("file", END)
    builder.add_edge("escalate", END)
    builder.add_edge("skip", END)

    graph = builder.compile(checkpointer=checkpointer)
    return graph, saver_cm


# --- nodes -------------------------------------------------------------------


def _detect_node(state: GraphState) -> dict:
    """Looks up the sub-area's stored last version, inserts the new version,
    and produces the normalised diff. If this is the sub-area's first
    sighting, there is nothing to compare against - it is skipped silently
    (deduplication, not a rule outcome: nothing "changed" from a version
    that didn't exist). A seen-before sub-area with no watched-field change
    still goes to `decide`, where the engine's NOT_COVERED branch hands it
    to assess() - see _route_after_detect."""
    record = SubAreaRecord.from_dict(state["record"])
    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            sub_area_id = repo.get_or_create_sub_area(
                conn, record, is_test_data=state.get("is_test_data", False)
            )
            previous = repo.latest_version(conn, sub_area_id)
            after_version_id = repo.insert_version(conn, sub_area_id, record)

        if previous is None:
            return {
                "sub_area_id": sub_area_id,
                "before_version_id": None,
                "after_version_id": after_version_id,
                "before": None,
                "after": _to_version_dict(record),
                "changed_fields": {},
                "same_feature": False,
            }

        if previous["feature_id"] == record.feature_id:
            # The exact same source row, re-served by the WFS query (most
            # often: a re-run before the watermark advanced past it). Not a
            # new revision - insert_version()'s ON CONFLICT already proved
            # that by returning the existing row id instead of a new one.
            # Nothing to diff, so this is deduplication, not a decision -
            # see _route_after_detect.
            return {
                "sub_area_id": sub_area_id,
                "before_version_id": previous["id"],
                "after_version_id": after_version_id,
                "before": _to_stored_version_dict(previous),
                "after": _to_stored_version_dict(previous),
                "changed_fields": {},
                "same_feature": True,
            }

        previous_record = SubAreaRecord(
            feature_id=previous["feature_id"],
            lokplan_id=record.lokplan_id,
            delnr=record.delnr,
            komnr=record.komnr,
            kommunenavn=record.kommunenavn,
            versionsnr=previous["versionsnr"],
            status=previous["status"],
            datoopdt=previous["datoopdt"].isoformat(),
            maxbygnhjd=previous["maxbygnhjd"],
            maxetager=previous["maxetager"],
            bebygpct=previous["bebygpct"],
            zonestatus=previous["zonestatus"],
            anvendelsegenerel=previous["anvendelsegenerel"],
            doklink=previous["doklink"],
        )
        diff = diff_versions(previous_record, record)

    return {
        "sub_area_id": sub_area_id,
        "before_version_id": previous["id"],
        "after_version_id": after_version_id,
        "before": diff.before,
        "after": diff.after,
        "changed_fields": diff.changed_fields,
        "same_feature": False,
    }


def _route_after_detect(state: GraphState) -> str:
    """Two shapes of "nothing to decide", both deduplication rather than a
    rule outcome:

    - First-ever sighting (before_version_id is None) - no prior version to
      have changed from.
    - Same feature_id as the last stored version (same_feature is True) -
      the exact same source row, re-served by the WFS query rather than a
      genuine new revision. Comparing it to itself would always report zero
      watched-field differences and fall through to NOT_COVERED, which is
      not an honest "we cannot see what changed" - there is nothing new to
      not-see.

    A seen-before sub-area with a genuinely new feature_id always goes to
    `decide`, even with an empty changed_fields - that is the no-visible-
    change case the engine's NOT_COVERED branch exists for."""
    if state["before_version_id"] is None or state.get("same_feature"):
        return "skip"
    return "decide"


def _decide_node(state: GraphState) -> dict:
    """Applies R1-R7 in precedence order. Deterministic code - this is the
    layer that never asks the model anything."""
    decision = apply_rules(state["changed_fields"], state["before"] or {}, state["after"])
    return {
        "outcome": decision.outcome.value,
        "rule": decision.rule,
        "rule_reason": decision.reason,
    }


def _route_after_decide(state: GraphState) -> str:
    outcome = state["outcome"]
    if outcome == Outcome.NOT_COVERED.value:
        return "assess"
    return outcome  # "file" | "escalate"


def _assess_node(state: GraphState) -> dict:
    """The rule engine did not cover this case. The model decides what is
    worth a person's attention and why - not whether it is compliant."""
    record = SubAreaRecord.from_dict(state["record"])
    result = assess(
        sub_area_description=_sub_area_description(record),
        changed_fields=state["changed_fields"],
        doklink=record.doklink or "",
    )
    usage = take_last_usage()
    return {
        "outcome": "escalate",
        "rule": None,
        "assessment_what_is_unclear": result.what_is_unclear,
        "assessment_what_a_person_must_decide": result.what_a_person_must_decide,
        "usage": [usage.to_dict()] if usage else [],
    }


def _file_node(state: GraphState) -> dict:
    record = SubAreaRecord.from_dict(state["record"])
    summary = summarise(
        sub_area_description=_sub_area_description(record),
        changed_fields=state["changed_fields"],
        rule=state["rule"],
        rule_reason=state["rule_reason"],
        doklink=record.doklink or "",
    )
    usage = take_last_usage()
    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url) as conn, conn.transaction():
        result = file_decision(
            conn,
            RunBudget(),
            sub_area_id=state["sub_area_id"],
            before_version_id=state["before_version_id"],
            after_version_id=state["after_version_id"],
            changed_fields=state["changed_fields"],
            rule=state["rule"],
            rule_set=RULE_SET_ID,
            summary=summary,
            citation=record.doklink or "",
        )
    return {
        "result": {"diff_id": result.diff_id, "filing_id": result.filing_id},
        "usage": [usage.to_dict()] if usage else [],
    }


def _escalate_node(state: GraphState) -> dict:
    """Writes the escalation, then calls interrupt() to pause the graph.

    interrupt() must run *after* the write so the escalation row already
    exists (with a thread_id) by the time a person is asked to look at it -
    an interrupted-but-unrecorded escalation would be invisible to anyone
    querying the escalations table.
    """
    record = SubAreaRecord.from_dict(state["record"])
    # The thread id LangGraph itself is running this graph under - the same
    # id a caller passes as config={"configurable": {"thread_id": ...}} to
    # graph.invoke(), and the same id Command(resume=...) must be issued
    # against later to resume this exact paused run.
    thread_id = get_config()["configurable"]["thread_id"]

    what_is_unclear = state.get("assessment_what_is_unclear") or state["rule_reason"]
    what_a_person_must_decide = state.get("assessment_what_a_person_must_decide")

    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url) as conn, conn.transaction():
        result = escalate_decision(
            conn,
            RunBudget(),
            sub_area_id=state["sub_area_id"],
            before_version_id=state["before_version_id"],
            after_version_id=state["after_version_id"],
            changed_fields=state["changed_fields"],
            rule=state["rule"],
            rule_set=RULE_SET_ID,
            thread_id=thread_id,
            what_is_unclear=what_is_unclear,
            what_a_person_must_decide=what_a_person_must_decide,
            citation=record.doklink or "",
        )

    interrupt(
        {
            "escalation_id": result.escalation_id,
            "diff_id": result.diff_id,
            "sub_area": _sub_area_description(record),
            "changed_fields": state["changed_fields"],
            "what_is_unclear": what_is_unclear,
            "what_a_person_must_decide": what_a_person_must_decide,
            "citation": record.doklink,
        }
    )
    # Re-entered on resume; interrupt() re-raises until a Command(resume=...)
    # is supplied, at which point this line is reached with nothing further
    # to do - the write already happened before the pause.
    return {"result": {"diff_id": result.diff_id, "escalation_id": result.escalation_id}}


def _skip_node(state: GraphState) -> dict:
    """A sub-area's first-ever sighting, or the same feature_id re-served by
    the WFS query. Either way this is deduplication, not a rule outcome -
    there is nothing new to have changed from, so nothing is written as a
    diff. The version row inserted in detect already records that this
    version was seen (or was already stored, for the same-feature case)."""
    return {"result": {"skipped": True}}


def _to_stored_version_dict(row: dict) -> dict:
    """Same shape as _to_version_dict, but read from a sub_area_versions row
    already in the database rather than a freshly fetched SubAreaRecord -
    used only for the same-feature short-circuit in _detect_node, where
    before and after are the same stored row."""
    return {
        "status": row["status"],
        "updated": row["datoopdt"].isoformat(),
        "version": row["versionsnr"],
        "maxbygnhjd": row["maxbygnhjd"],
        "maxetager": row["maxetager"],
        "bebygpct": row["bebygpct"],
        "zonestatus": row["zonestatus"],
        "anvendelsegenerel": row["anvendelsegenerel"],
    }


def _to_version_dict(record: SubAreaRecord) -> dict:
    return {
        "status": record.status,
        "updated": record.datoopdt,
        "version": record.versionsnr,
        "maxbygnhjd": record.maxbygnhjd,
        "maxetager": record.maxetager,
        "bebygpct": record.bebygpct,
        "zonestatus": record.zonestatus,
        "anvendelsegenerel": record.anvendelsegenerel,
    }



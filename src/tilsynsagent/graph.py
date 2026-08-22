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
from langgraph.types import interrupt

from tilsynsagent.actions.escalate import escalate_decision
from tilsynsagent.actions.file import file_decision
from tilsynsagent.actions.permissions import RunBudget
from tilsynsagent.db import repo
from tilsynsagent.detect import diff_versions
from tilsynsagent.llm.assess import assess
from tilsynsagent.llm.summarise import summarise
from tilsynsagent.rules.engine import RULE_SET_ID, Outcome, apply_rules
from tilsynsagent.sources.plandata import SubAreaRecord


class GraphState(TypedDict, total=False):
    # --- input: set before the graph runs ---
    # A dict (SubAreaRecord._asdict()-shaped), not the dataclass itself: the
    # Postgres checkpointer serializes state via msgpack, which only allows
    # registered types. Keeping the checkpointed state to plain dicts avoids
    # depending on SubAreaRecord's msgpack registration remaining supported.
    record: dict

    # --- detect ---
    sub_area_id: int
    before_version_id: int | None
    after_version_id: int
    before: dict | None
    after: dict
    changed_fields: dict

    # --- classify / decide ---
    outcome: Literal["file", "escalate", "ignore"]
    rule: str | None
    rule_reason: str

    # --- assess (only when the rule engine did not cover the case) ---
    assessment_what_is_unclear: str
    assessment_what_a_person_must_decide: str

    # --- result ---
    result: dict  # {"diff_id":..., "filing_id"|"escalation_id":...} or {"ignored": True}


def _sub_area_description(record: SubAreaRecord) -> str:
    return f"{record.kommunenavn or record.komnr}, plan {record.lokplan_id}, sub-area {record.delnr}"


def run_input(record: SubAreaRecord) -> dict:
    """The initial state dict for graph.invoke({...}, config=...) - converts
    the fetched record to the plain-dict form GraphState.record requires."""
    return {"record": record.to_dict()}


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
    builder.add_node("ignore", _ignore_node)

    builder.add_edge(START, "detect")
    builder.add_conditional_edges(
        "detect",
        _route_after_detect,
        {"decide": "decide", "ignore": "ignore"},
    )
    builder.add_conditional_edges(
        "decide",
        _route_after_decide,
        {"file": "file", "escalate": "escalate", "assess": "assess", "ignore": "ignore"},
    )
    builder.add_edge("assess", "escalate")
    builder.add_edge("file", END)
    builder.add_edge("escalate", END)
    builder.add_edge("ignore", END)

    graph = builder.compile(checkpointer=checkpointer)
    return graph, saver_cm


# --- nodes -------------------------------------------------------------------


def _detect_node(state: GraphState) -> dict:
    """Looks up the sub-area's stored last version, inserts the new version,
    and produces the normalised diff. If this is the sub-area's first
    sighting, there is nothing to compare against - it goes straight to
    ignore (nothing "changed" from a version that didn't exist)."""
    record = SubAreaRecord.from_dict(state["record"])
    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            sub_area_id = repo.get_or_create_sub_area(conn, record)
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
    }


def _route_after_detect(state: GraphState) -> str:
    return "decide" if state["changed_fields"] else "ignore"


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
    return outcome  # "file" | "escalate" | "ignore"


def _assess_node(state: GraphState) -> dict:
    """The rule engine did not cover this case. The model decides what is
    worth a person's attention and why - not whether it is compliant."""
    record = SubAreaRecord.from_dict(state["record"])
    result = assess(
        sub_area_description=_sub_area_description(record),
        changed_fields=state["changed_fields"],
        doklink=record.doklink or "",
    )
    return {
        "outcome": "escalate",
        "rule": None,
        "assessment_what_is_unclear": result.what_is_unclear,
        "assessment_what_a_person_must_decide": result.what_a_person_must_decide,
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
    return {"result": {"diff_id": result.diff_id, "filing_id": result.filing_id}}


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


def _ignore_node(state: GraphState) -> dict:
    """Ignored changes are not written as diffs. Per docs/rules.md, ignore
    means nothing about what may be built has changed - there is nothing to
    file or escalate, and the version row inserted in detect already records
    that this version was seen."""
    return {"result": {"ignored": True}}


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



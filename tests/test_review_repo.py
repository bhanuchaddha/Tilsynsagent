"""Integration tests for the review queue's read queries
(db/repo.py's list_open_escalations and get_escalation_detail) and the
resume path (graph.resume_run), against the real Neon database.

Skipped automatically if DATABASE_URL is not set.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()

from tilsynsagent.actions.escalate import escalate_decision
from tilsynsagent.actions.permissions import RunBudget
from tilsynsagent.db import repo
from tilsynsagent.graph import make_graph, resume_run
from tilsynsagent.sources.plandata import SubAreaRecord

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="requires a real DATABASE_URL"
)

TEST_LOKPLAN_ID = 777222


def _wipe_test_data(c: psycopg.Connection) -> None:
    with c.transaction():
        c.execute(
            "DELETE FROM escalation_resolutions WHERE escalation_id IN "
            "(SELECT e.id FROM escalations e JOIN diffs d ON e.diff_id = d.id "
            "JOIN sub_areas s ON d.sub_area_id = s.id WHERE s.lokplan_id = %s)",
            (TEST_LOKPLAN_ID,),
        )
        c.execute(
            "DELETE FROM escalations WHERE diff_id IN "
            "(SELECT d.id FROM diffs d JOIN sub_areas s ON d.sub_area_id = s.id "
            "WHERE s.lokplan_id = %s)",
            (TEST_LOKPLAN_ID,),
        )
        c.execute(
            "DELETE FROM diffs WHERE sub_area_id IN "
            "(SELECT id FROM sub_areas WHERE lokplan_id = %s)",
            (TEST_LOKPLAN_ID,),
        )
        c.execute(
            "DELETE FROM sub_area_versions WHERE sub_area_id IN "
            "(SELECT id FROM sub_areas WHERE lokplan_id = %s)",
            (TEST_LOKPLAN_ID,),
        )
        c.execute("DELETE FROM sub_areas WHERE lokplan_id = %s", (TEST_LOKPLAN_ID,))
        c.execute("DELETE FROM checkpoints WHERE thread_id LIKE 'review-repo-test-%'")
        c.execute("DELETE FROM checkpoint_writes WHERE thread_id LIKE 'review-repo-test-%'")
        c.execute("DELETE FROM checkpoint_blobs WHERE thread_id LIKE 'review-repo-test-%'")


@pytest.fixture
def conn():
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        _wipe_test_data(c)
        yield c
        _wipe_test_data(c)


def _make_version(conn, delnr="A") -> tuple[int, int]:
    rec = SubAreaRecord(
        feature_id=f"review-repo-test.{delnr}",
        lokplan_id=TEST_LOKPLAN_ID,
        delnr=delnr,
        komnr=999,
        kommunenavn="Testkommune",
        versionsnr=1,
        status="V",
        datoopdt="2026-01-01T00:00:00.000Z",
        maxbygnhjd=8.5,
        maxetager=2,
        bebygpct=40,
        zonestatus=None,
        anvendelsegenerel="Boligområde",
        doklink="https://dokument.plandata.dk/review-repo-test.pdf",
    )
    sub_area_id = repo.get_or_create_sub_area(conn, rec, is_test_data=True)
    version_id = repo.insert_version(conn, sub_area_id, rec)
    return sub_area_id, version_id


def test_list_open_escalations_excludes_resolved(conn):
    sub_area_id, version_id = _make_version(conn, delnr="A")
    with conn.transaction():
        result = escalate_decision(
            conn,
            RunBudget(),
            sub_area_id=sub_area_id,
            before_version_id=None,
            after_version_id=version_id,
            changed_fields={"zonestatus": {"before": "Byzone", "after": None}},
            rule="R4",
            rule_set="zealand-local-plans-v2",
            thread_id="review-repo-test-open",
            what_is_unclear="test escalation",
            what_a_person_must_decide="nothing, this is a test",
            citation="https://dokument.plandata.dk/review-repo-test.pdf",
        )

    open_before = [e for e in repo.list_open_escalations(conn) if e["escalation_id"] == result.escalation_id]
    assert len(open_before) == 1

    with conn.transaction():
        repo.insert_escalation_resolution(
            conn,
            escalation_id=result.escalation_id,
            label="file",
            reason="test resolution",
            rule="R4",
            resolved_by="test",
        )

    open_after = [e for e in repo.list_open_escalations(conn) if e["escalation_id"] == result.escalation_id]
    assert open_after == []


def test_get_escalation_detail_includes_before_after_versions(conn):
    sub_area_id, before_id = _make_version(conn, delnr="B")
    after_rec = SubAreaRecord(
        feature_id="review-repo-test.B.v2",
        lokplan_id=TEST_LOKPLAN_ID,
        delnr="B",
        komnr=999,
        kommunenavn="Testkommune",
        versionsnr=2,
        status="V",
        datoopdt="2026-02-01T00:00:00.000Z",
        maxbygnhjd=8.5,
        maxetager=2,
        bebygpct=None,
        zonestatus=None,
        anvendelsegenerel="Boligområde",
        doklink="https://dokument.plandata.dk/review-repo-test.pdf",
    )
    after_id = repo.insert_version(conn, sub_area_id, after_rec)

    with conn.transaction():
        result = escalate_decision(
            conn,
            RunBudget(),
            sub_area_id=sub_area_id,
            before_version_id=before_id,
            after_version_id=after_id,
            changed_fields={"bebygpct": {"before": 40, "after": None}},
            rule="R4",
            rule_set="zealand-local-plans-v2",
            thread_id="review-repo-test-detail",
            what_is_unclear="a value went blank",
            what_a_person_must_decide="confirm against the plan",
            citation="https://dokument.plandata.dk/review-repo-test.pdf",
        )

    detail = repo.get_escalation_detail(conn, result.escalation_id)

    assert detail is not None
    assert detail["before"]["id"] == before_id
    assert detail["after"]["id"] == after_id
    assert detail["rule"] == "R4"
    assert detail["resolution_id"] is None
    assert detail["thread_id"] == "review-repo-test-detail"


def test_get_escalation_detail_returns_none_for_unknown_id(conn):
    assert repo.get_escalation_detail(conn, -1) is None


def test_resume_run_completes_a_paused_run_and_is_idempotent(conn):
    """The core new mechanism this phase adds: resuming a paused escalation
    against its stored thread id must let the graph reach END, and calling
    resume_run a second time on the same (now-completed) thread must not
    raise or duplicate the escalation_resolutions row - PLAN.md's stated
    risk ('If it fires twice, it could write duplicates')."""
    # Committed explicitly, not left open on the fixture's connection: the
    # graph run below opens its own separate connection (_detect_node
    # connects independently - see graph.py), and an uncommitted insert here
    # would hold a row lock that connection blocks on indefinitely.
    with conn.transaction():
        _make_version(conn, delnr="C")
    thread_id = "review-repo-test-resume"

    database_url = os.environ["DATABASE_URL"]
    graph, saver_cm = make_graph(database_url)
    try:
        graph.checkpointer.setup()
        from tilsynsagent.graph import run_input
        from tilsynsagent.sources.plandata import SubAreaRecord as SAR

        # Drive a real escalation through the graph so it actually pauses at
        # interrupt() - this is the exact mechanism resume_run resumes.
        after_rec = SAR(
            feature_id="review-repo-test.C.v2",
            lokplan_id=TEST_LOKPLAN_ID,
            delnr="C",
            komnr=999,
            kommunenavn="Testkommune",
            versionsnr=2,
            status="V",
            datoopdt="2026-02-01T00:00:00.000Z",
            maxbygnhjd=8.5,
            maxetager=2,
            bebygpct=None,
            zonestatus=None,
            anvendelsegenerel="Boligområde",
            doklink="https://dokument.plandata.dk/review-repo-test.pdf",
        )
        result = graph.invoke(
            run_input(after_rec, is_test_data=True),
            config={"configurable": {"thread_id": thread_id}},
        )
        assert result.get("__interrupt__")

        escalation_id = result["__interrupt__"][0].value["escalation_id"]

        with conn.transaction():
            repo.insert_escalation_resolution(
                conn,
                escalation_id=escalation_id,
                label="file",
                reason="test resolution for resume",
                rule="R4",
                resolved_by="test",
            )

        first = resume_run(graph, thread_id)
        assert not first.get("__interrupt__")

        # Second resume of an already-completed thread must not raise.
        second = resume_run(graph, thread_id)
        assert not second.get("__interrupt__")

        resolutions = conn.execute(
            "SELECT COUNT(*) FROM escalation_resolutions WHERE escalation_id = %s",
            (escalation_id,),
        ).fetchone()
        assert resolutions[0] == 1
    finally:
        saver_cm.__exit__(None, None, None)
        with psycopg.connect(database_url) as cleanup_conn, cleanup_conn.transaction():
            cleanup_conn.execute("DELETE FROM checkpoints WHERE thread_id = %s", (thread_id,))
            cleanup_conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (thread_id,))
            cleanup_conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = %s", (thread_id,))

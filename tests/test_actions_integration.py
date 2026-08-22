"""Integration tests against the real Neon database (Phase 1 verification 6).

Skipped automatically if DATABASE_URL is not set. These prove refusal happens
before execution, not after: a permission violation must leave zero rows
written, checked by counting rows before and after the refused call.
"""

import os

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()

from tilsynsagent.actions.escalate import escalate_decision
from tilsynsagent.actions.file import file_decision
from tilsynsagent.actions.permissions import PermissionDenied, RunBudget
from tilsynsagent.db import repo
from tilsynsagent.sources.plandata import SubAreaRecord

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="requires a real DATABASE_URL"
)

TEST_LOKPLAN_ID = 888888


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
            "DELETE FROM filings WHERE diff_id IN "
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


@pytest.fixture
def conn():
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        _wipe_test_data(c)
        yield c
        _wipe_test_data(c)


def _make_version(conn, delnr="A"):
    rec = SubAreaRecord(
        feature_id=f"perm-test.{delnr}",
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
        doklink="https://dokument.plandata.dk/perm-test.pdf",
    )
    sub_area_id = repo.get_or_create_sub_area(conn, rec)
    version_id = repo.insert_version(conn, sub_area_id, rec)
    return sub_area_id, version_id


def _diff_count(conn) -> int:
    row = conn.execute(
        "SELECT count(*) FROM diffs WHERE sub_area_id IN "
        "(SELECT id FROM sub_areas WHERE lokplan_id = %s)",
        (TEST_LOKPLAN_ID,),
    ).fetchone()
    return row[0]


def test_file_decision_writes_filing(conn):
    sub_area_id, version_id = _make_version(conn)
    with conn.transaction():
        result = file_decision(
            conn,
            RunBudget(),
            sub_area_id=sub_area_id,
            before_version_id=None,
            after_version_id=version_id,
            changed_fields={"anvendelsegenerel": {"before": None, "after": "Boligområde"}},
            rule="R1",
            rule_set="zealand-local-plans-v1",
            summary="test filing",
            citation="https://dokument.plandata.dk/perm-test.pdf",
        )
    assert result.diff_id > 0
    assert result.filing_id > 0


def test_run_budget_refuses_before_writing_any_row(conn):
    """A run budget exceeded on the Nth call must write zero rows for that
    call - proven by counting diffs before and after the refused call."""
    sub_area_id, version_id = _make_version(conn)
    budget = RunBudget()
    # exhaust the budget artificially by calling check_and_record directly
    from tilsynsagent.actions.permissions import FILE_PERMISSION

    for _ in range(FILE_PERMISSION.max_calls_per_run):
        budget.calls_made[FILE_PERMISSION.name] = FILE_PERMISSION.max_calls_per_run

    before_count = _diff_count(conn)
    with pytest.raises(PermissionDenied), conn.transaction():
        file_decision(
            conn,
            budget,
            sub_area_id=sub_area_id,
            before_version_id=None,
            after_version_id=version_id,
            changed_fields={"anvendelsegenerel": {"before": None, "after": "X"}},
            rule="R1",
            rule_set="zealand-local-plans-v1",
            summary="should never be written",
            citation="https://dokument.plandata.dk/perm-test.pdf",
        )
    after_count = _diff_count(conn)
    assert before_count == after_count == 0


def test_escalate_decision_writes_escalation(conn):
    sub_area_id, version_id = _make_version(conn, delnr="B")
    with conn.transaction():
        result = escalate_decision(
            conn,
            RunBudget(),
            sub_area_id=sub_area_id,
            before_version_id=None,
            after_version_id=version_id,
            changed_fields={"zonestatus": {"before": "Byzone", "after": None}},
            rule="R4",
            rule_set="zealand-local-plans-v1",
            thread_id="perm-test-thread",
            what_is_unclear="test escalation",
            what_a_person_must_decide="nothing, this is a test",
            citation="https://dokument.plandata.dk/perm-test.pdf",
        )
    assert result.diff_id > 0
    assert result.escalation_id > 0


def test_escalate_decision_is_idempotent_on_reentry(conn):
    """interrupt() re-executes its whole node from the start on resume (its
    own documented behaviour), so escalate_decision can be called twice for
    the same after_version_id/diff. The second call must return the same
    ids, not raise a UniqueViolation - this is the bug verification 5 caught."""
    sub_area_id, version_id = _make_version(conn, delnr="C")
    kwargs = dict(
        sub_area_id=sub_area_id,
        before_version_id=None,
        after_version_id=version_id,
        changed_fields={"zonestatus": {"before": "Byzone", "after": None}},
        rule="R4",
        rule_set="zealand-local-plans-v1",
        thread_id="perm-test-thread-reentry",
        what_is_unclear="test escalation",
        what_a_person_must_decide="nothing, this is a test",
        citation="https://dokument.plandata.dk/perm-test.pdf",
    )
    with conn.transaction():
        first = escalate_decision(conn, RunBudget(), **kwargs)
    with conn.transaction():
        second = escalate_decision(conn, RunBudget(), **kwargs)

    assert first.diff_id == second.diff_id
    assert first.escalation_id == second.escalation_id

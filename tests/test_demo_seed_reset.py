"""Integration tests for demo/seed.py and demo/reset.py against the real
Neon database.

Skipped automatically if DATABASE_URL or GROQ_API_KEY is not set - seed_demo
makes real Groq calls for the NOT_COVERED cases (assess()), so this needs
both.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()

from tilsynsagent.db import repo
from tilsynsagent.db.migrate import run_migrations
from tilsynsagent.demo.reset import reset_demo
from tilsynsagent.demo.seed import seed_demo

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ or "GROQ_API_KEY" not in os.environ,
    reason="requires a real DATABASE_URL and GROQ_API_KEY",
)

# A small mix, one of each outcome shape - enough to prove the pipeline
# without a full seed run in the test suite.
SMALL_CASE_IDS = ["ZL-004", "ZL-001", "NC-001"]


@pytest.fixture(autouse=True)
def _clean_demo_data():
    run_migrations(os.environ["DATABASE_URL"])
    reset_demo()
    yield
    reset_demo()


def test_seed_creates_test_data_through_the_real_graph():
    """Every case fed in reaches a real outcome (filed or escalated), and
    every sub_area it touches is marked is_test_data - the flag
    demo/reset.py's WHERE clause keys off (migrations/003_demo_data.sql)."""
    counts = seed_demo(case_ids=SMALL_CASE_IDS)

    assert counts["error"] == 0
    assert counts["filed"] + counts["escalated"] == len(SMALL_CASE_IDS)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM sub_areas WHERE is_test_data"
        ).fetchone()
        assert rows[0] == len(SMALL_CASE_IDS)


def test_seed_produces_a_real_paused_escalation():
    """ZL-001 is an R1 case (escalate) - seeding it must leave a genuine
    escalation row with an open (unresolved) status, ready for the review
    screen to answer."""
    seed_demo(case_ids=["ZL-001"])

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        open_escalations = repo.list_open_escalations(conn)

    assert len(open_escalations) == 1
    assert open_escalations[0]["rule"] == "R1"


def test_reset_removes_only_test_data():
    """A live (non-test) sub_area sharing no relationship with the demo data
    must survive a reset untouched - reset_demo's WHERE clause must never
    reach beyond is_test_data=True (PLAN.md: 'Reset wipes test rows only')."""
    from tilsynsagent.sources.plandata import SubAreaRecord

    live_lokplan_id = 777001
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.transaction():
        live_record = SubAreaRecord(
            feature_id="reset-test.live",
            lokplan_id=live_lokplan_id,
            delnr="A",
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
            doklink="https://dokument.plandata.dk/reset-test.pdf",
        )
        repo.get_or_create_sub_area(conn, live_record, is_test_data=False)

    try:
        seed_demo(case_ids=SMALL_CASE_IDS)
        reset_demo()

        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            test_remaining = conn.execute(
                "SELECT COUNT(*) FROM sub_areas WHERE is_test_data"
            ).fetchone()[0]
            live_remaining = conn.execute(
                "SELECT COUNT(*) FROM sub_areas WHERE lokplan_id = %s", (live_lokplan_id,)
            ).fetchone()[0]

        assert test_remaining == 0
        assert live_remaining == 1
    finally:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.transaction():
            conn.execute("DELETE FROM sub_areas WHERE lokplan_id = %s", (live_lokplan_id,))


def test_seed_reset_seed_is_repeatable():
    """The core demo UX (PLAN.md: 'Press reset, do it again') - seeding,
    resetting, and seeding again must not collide on unique constraints
    (feature_id, checkpoint thread ids) despite reusing the same case ids."""
    first = seed_demo(case_ids=SMALL_CASE_IDS)
    reset_demo()
    second = seed_demo(case_ids=SMALL_CASE_IDS)

    assert first == second

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM sub_areas WHERE is_test_data"
        ).fetchone()[0]
        assert count == len(SMALL_CASE_IDS)

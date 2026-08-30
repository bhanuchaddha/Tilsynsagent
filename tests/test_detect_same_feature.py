"""Integration test for graph._detect_node's same-feature dedup guard.

A fetched record whose feature_id matches the sub-area's last stored version
is the same source row re-served by the WFS query (most commonly: a re-run
before the watermark advanced past it), not a genuinely new revision.
_detect_node must route this to skip, the same as a first-ever sighting -
never to decide/apply_rules, which would otherwise compare the stored row to
itself, find no watched-field differences, and escalate a NOT_COVERED case
that isn't real.

Skipped automatically if DATABASE_URL is not set, same convention as
test_actions_integration.py.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv()

from tilsynsagent.db import repo
from tilsynsagent.graph import _detect_node, _route_after_detect
from tilsynsagent.sources.plandata import SubAreaRecord

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="requires a real DATABASE_URL"
)

TEST_LOKPLAN_ID = 777777


def _wipe_test_data(c: psycopg.Connection) -> None:
    with c.transaction():
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
    # autocommit: _detect_node opens its own connection to the same real
    # database, so setup writes made here must be visible to it immediately
    # rather than sitting in an open transaction on this connection.
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
        _wipe_test_data(c)
        yield c
        _wipe_test_data(c)


def _record(*, feature_id: str, delnr="A", **overrides) -> SubAreaRecord:
    fields = dict(
        feature_id=feature_id,
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
        doklink="https://dokument.plandata.dk/detect-test.pdf",
    )
    fields.update(overrides)
    return SubAreaRecord(**fields)


def test_same_feature_id_reroutes_to_skip(conn):
    """The exact scenario this guard exists for: the same feature_id is
    fetched twice (e.g. a re-run before the watermark advanced). The second
    pass must route to skip, not decide - even though a real new version
    exists in the sense that insert_version() was called again."""
    first = _record(feature_id="detect-test.same")
    repo.get_or_create_sub_area(conn, first)
    repo.insert_version(conn, repo.get_or_create_sub_area(conn, first), first)

    state = _detect_node({"record": first.to_dict()})
    assert state["same_feature"] is True
    assert state["changed_fields"] == {}
    assert _route_after_detect(state) == "skip"


def test_different_feature_id_same_values_still_routes_to_decide(conn):
    """A genuinely new revision with the same field values (the real
    no-visible-change case) must still reach decide/NOT_COVERED - only an
    identical feature_id short-circuits to skip, not identical field
    values."""
    first = _record(feature_id="detect-test.v1", datoopdt="2026-01-01T00:00:00.000Z")
    sub_area_id = repo.get_or_create_sub_area(conn, first)
    repo.insert_version(conn, sub_area_id, first)

    second = _record(feature_id="detect-test.v2", datoopdt="2026-02-01T00:00:00.000Z")
    state = _detect_node({"record": second.to_dict()})

    assert state["same_feature"] is False
    assert state["changed_fields"] == {}
    assert _route_after_detect(state) == "decide"


def test_different_feature_id_with_real_change_routes_to_decide(conn):
    """A genuinely new revision with a real field change reaches decide, and
    the diff correctly reflects the change - same_feature must not suppress
    real transitions."""
    first = _record(
        feature_id="detect-test.change-v1",
        datoopdt="2026-01-01T00:00:00.000Z",
        bebygpct=40,
    )
    sub_area_id = repo.get_or_create_sub_area(conn, first)
    repo.insert_version(conn, sub_area_id, first)

    second = _record(
        feature_id="detect-test.change-v2",
        datoopdt="2026-02-01T00:00:00.000Z",
        bebygpct=70,
    )
    state = _detect_node({"record": second.to_dict()})

    assert state["same_feature"] is False
    assert state["changed_fields"] == {"bebygpct": {"before": 40, "after": 70}}
    assert _route_after_detect(state) == "decide"


def test_first_sighting_still_routes_to_skip(conn):
    """same_feature must not change the pre-existing first-sighting path."""
    record = _record(feature_id="detect-test.first")
    state = _detect_node({"record": record.to_dict()})

    assert state["before_version_id"] is None
    assert state["same_feature"] is False
    assert _route_after_detect(state) == "skip"

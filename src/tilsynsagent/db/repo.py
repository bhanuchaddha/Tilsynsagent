"""Database operations the graph and action tools use.

Thin wrappers over the schema in migrations/001_initial.sql - no ORM. Each
function takes an open psycopg connection so callers control transaction
boundaries (a run should not half-commit if a later step fails).
"""

from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row

from tilsynsagent.sources.plandata import SubAreaRecord


def get_or_create_sub_area(conn: psycopg.Connection, record: SubAreaRecord) -> int:
    row = conn.execute(
        "SELECT id FROM sub_areas WHERE lokplan_id = %s AND delnr = %s",
        (record.lokplan_id, record.delnr),
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        """
        INSERT INTO sub_areas (lokplan_id, delnr, komnr, kommunenavn)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (record.lokplan_id, record.delnr, record.komnr, record.kommunenavn),
    ).fetchone()
    return row[0]


def latest_version(conn: psycopg.Connection, sub_area_id: int) -> dict | None:
    """The most recent version stored for a sub-area, or None if this is its
    first sighting."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT * FROM sub_area_versions
            WHERE sub_area_id = %s
            ORDER BY datoopdt DESC
            LIMIT 1
            """,
            (sub_area_id,),
        )
        return cur.fetchone()


def insert_version(conn: psycopg.Connection, sub_area_id: int, record: SubAreaRecord) -> int:
    row = conn.execute(
        """
        INSERT INTO sub_area_versions
            (sub_area_id, feature_id, versionsnr, status, datoopdt,
             maxbygnhjd, maxetager, bebygpct, zonestatus, anvendelsegenerel, doklink)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (feature_id) DO UPDATE SET feature_id = EXCLUDED.feature_id
        RETURNING id
        """,
        (
            sub_area_id,
            record.feature_id,
            record.versionsnr,
            record.status,
            record.datoopdt,
            record.maxbygnhjd,
            record.maxetager,
            record.bebygpct,
            record.zonestatus,
            record.anvendelsegenerel,
            record.doklink,
        ),
    ).fetchone()
    return row[0]


def insert_diff(
    conn: psycopg.Connection,
    *,
    sub_area_id: int,
    before_version_id: int | None,
    after_version_id: int,
    changed_fields: dict,
    outcome: str,
    rule: str | None,
    rule_set: str,
) -> int:
    """Idempotent on after_version_id (UNIQUE in the schema): a re-run for a
    version that already has a diff returns the existing row instead of
    raising. This matters because interrupt() re-executes its whole node from
    the top on resume (per its own docs), so the escalate node's write can be
    attempted twice for the same version - once before the pause, once after
    Command(resume=...). Without this, the second attempt would fail with a
    UniqueViolation instead of completing the resume.
    """
    row = conn.execute(
        """
        INSERT INTO diffs
            (sub_area_id, before_version_id, after_version_id, changed_fields,
             outcome, rule, rule_set)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (after_version_id) DO UPDATE SET after_version_id = EXCLUDED.after_version_id
        RETURNING id
        """,
        (
            sub_area_id,
            before_version_id,
            after_version_id,
            json.dumps(changed_fields),
            outcome,
            rule,
            rule_set,
        ),
    ).fetchone()
    return row[0]


def insert_filing(conn: psycopg.Connection, *, diff_id: int, summary: str, citation: str) -> int:
    row = conn.execute(
        """
        INSERT INTO filings (diff_id, summary, citation)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (diff_id, summary, citation),
    ).fetchone()
    return row[0]


def insert_escalation(
    conn: psycopg.Connection,
    *,
    diff_id: int,
    thread_id: str,
    what_is_unclear: str,
    what_a_person_must_decide: str | None,
    citation: str,
) -> int:
    """Idempotent on diff_id (UNIQUE in the schema) - see insert_diff's
    docstring for why the escalate node can attempt this write twice for the
    same diff across an interrupt()/resume cycle."""
    row = conn.execute(
        """
        INSERT INTO escalations
            (diff_id, thread_id, what_is_unclear, what_a_person_must_decide, citation)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (diff_id) DO UPDATE SET diff_id = EXCLUDED.diff_id
        RETURNING id
        """,
        (diff_id, thread_id, what_is_unclear, what_a_person_must_decide, citation),
    ).fetchone()
    return row[0]


def insert_escalation_resolution(
    conn: psycopg.Connection,
    *,
    escalation_id: int,
    label: str,
    reason: str,
    rule: str | None,
    resolved_by: str,
) -> int:
    row = conn.execute(
        """
        INSERT INTO escalation_resolutions
            (escalation_id, label, reason, rule, resolved_by)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (escalation_id, label, reason, rule, resolved_by),
    ).fetchone()
    return row[0]


def get_watermark(conn: psycopg.Connection) -> tuple[str, list[str]]:
    row = conn.execute(
        "SELECT last_datoopdt, seen_at_watermark FROM watermark WHERE id = 1"
    ).fetchone()
    if row is None:
        return "1900-01-01T00:00:00.000Z", []
    last_datoopdt, seen = row
    return last_datoopdt.isoformat().replace("+00:00", "Z"), list(seen)


def save_watermark(conn: psycopg.Connection, last_datoopdt: str, seen_at_watermark: list[str]) -> None:
    conn.execute(
        """
        INSERT INTO watermark (id, last_datoopdt, seen_at_watermark, updated_at)
        VALUES (1, %s, %s, now())
        ON CONFLICT (id) DO UPDATE SET
            last_datoopdt = EXCLUDED.last_datoopdt,
            seen_at_watermark = EXCLUDED.seen_at_watermark,
            updated_at = now()
        """,
        (last_datoopdt, json.dumps(seen_at_watermark)),
    )

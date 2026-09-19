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


def get_or_create_sub_area(
    conn: psycopg.Connection, record: SubAreaRecord, *, is_test_data: bool = False
) -> int:
    row = conn.execute(
        "SELECT id FROM sub_areas WHERE lokplan_id = %s AND delnr = %s",
        (record.lokplan_id, record.delnr),
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        """
        INSERT INTO sub_areas (lokplan_id, delnr, komnr, kommunenavn, is_test_data)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (record.lokplan_id, record.delnr, record.komnr, record.kommunenavn, is_test_data),
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


def insert_grounding(
    conn: psycopg.Connection,
    *,
    diff_id: int,
    citation_kind: str,
    clause_id: str,
    clause_quote: str,
    field_name: str,
    field_before: str,
    field_after: str,
    reasoning: str,
    outcome: str,
    document_page_count: int | None,
    document_chars: int | None,
) -> int:
    """Records what justified a grounded decision - a clause or a field.

    Idempotent on diff_id (UNIQUE in the schema) for the same reason
    insert_diff and insert_escalation are - see insert_diff's docstring. A
    grounded run does not interrupt, but it shares the re-execution surface
    of every other node, and a UniqueViolation on a retry would turn a
    recoverable re-run into a failed one.

    The schema's groundings_citation_is_complete CHECK (migration 005) is the
    last gate on a half-filled citation. actions/ground.py refuses one before
    getting here; this is what makes that refusal unbypassable.
    """
    row = conn.execute(
        """
        INSERT INTO groundings
            (diff_id, citation_kind, clause_id, clause_quote,
             field_name, field_before, field_after, reasoning, outcome,
             document_page_count, document_chars)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (diff_id) DO UPDATE SET diff_id = EXCLUDED.diff_id
        RETURNING id
        """,
        (
            diff_id,
            citation_kind,
            clause_id,
            clause_quote,
            field_name,
            field_before,
            field_after,
            reasoning,
            outcome,
            document_page_count,
            document_chars,
        ),
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


def list_open_escalations(conn: psycopg.Connection) -> list[dict]:
    """Escalations with no row in escalation_resolutions yet, newest first -
    the review queue (review/app.py). escalation_resolutions.escalation_id
    is UNIQUE, so a missing join match is exactly "unresolved"."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT e.id AS escalation_id, e.diff_id, e.what_is_unclear,
                   e.what_a_person_must_decide, e.citation, e.escalated_at,
                   d.rule, d.changed_fields, s.kommunenavn, s.komnr, s.lokplan_id, s.delnr
            FROM escalations e
            JOIN diffs d ON d.id = e.diff_id
            JOIN sub_areas s ON s.id = d.sub_area_id
            LEFT JOIN escalation_resolutions r ON r.escalation_id = e.id
            WHERE r.id IS NULL
            ORDER BY e.escalated_at DESC
            """
        )
        return cur.fetchall()


def get_escalation_detail(conn: psycopg.Connection, escalation_id: int) -> dict | None:
    """Everything the review screen needs for one escalation: the diff's
    before/after versions, the rule (or none, for a NOT_COVERED case handed
    to assess()), and whether it has already been resolved."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT e.id AS escalation_id, e.diff_id, e.thread_id, e.what_is_unclear,
                   e.what_a_person_must_decide, e.citation, e.escalated_at,
                   d.rule, d.rule_set, d.changed_fields, d.before_version_id, d.after_version_id,
                   s.id AS sub_area_id, s.kommunenavn, s.komnr, s.lokplan_id, s.delnr,
                   r.id AS resolution_id, r.label AS resolution_label,
                   r.reason AS resolution_reason, r.resolved_by, r.resolved_at
            FROM escalations e
            JOIN diffs d ON d.id = e.diff_id
            JOIN sub_areas s ON s.id = d.sub_area_id
            LEFT JOIN escalation_resolutions r ON r.escalation_id = e.id
            WHERE e.id = %s
            """,
            (escalation_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute(
            "SELECT * FROM sub_area_versions WHERE id = %s", (row["after_version_id"],)
        )
        row["after"] = cur.fetchone()
        row["before"] = None
        if row["before_version_id"] is not None:
            cur.execute(
                "SELECT * FROM sub_area_versions WHERE id = %s", (row["before_version_id"],)
            )
            row["before"] = cur.fetchone()
        return row


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


def status_counts(conn: psycopg.Connection) -> dict:
    """What the system has watched, decided and escalated - the numbers the
    status page publishes.

    Live and demo rows are counted separately rather than summed: a status
    page that reports demo activity as production activity is worse than no
    status page, because it is confidently wrong. `is_test_data` on sub_areas
    is the discriminator (migration 003), reached through the sub_area_id
    every other table hangs off.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE NOT s.is_test_data)                    AS sub_areas_live,
              COUNT(*) FILTER (WHERE s.is_test_data)                        AS sub_areas_demo
            FROM sub_areas s
            """
        )
        counts = dict(cur.fetchone())

        cur.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE NOT s.is_test_data) AS versions_live,
              COUNT(*) FILTER (WHERE s.is_test_data)     AS versions_demo
            FROM sub_area_versions v JOIN sub_areas s ON s.id = v.sub_area_id
            """
        )
        counts.update(cur.fetchone())

        cur.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE NOT s.is_test_data) AS filings_live,
              COUNT(*) FILTER (WHERE s.is_test_data)     AS filings_demo,
              MAX(f.filed_at)                            AS last_filed_at
            FROM filings f
            JOIN diffs d ON d.id = f.diff_id
            JOIN sub_areas s ON s.id = d.sub_area_id
            """
        )
        counts.update(cur.fetchone())

        cur.execute(
            """
            SELECT
              COUNT(*) FILTER (WHERE NOT s.is_test_data) AS escalations_live,
              COUNT(*) FILTER (WHERE s.is_test_data)     AS escalations_demo,
              COUNT(*) FILTER (WHERE r.id IS NULL)       AS escalations_open,
              MAX(e.escalated_at)                        AS last_escalated_at
            FROM escalations e
            JOIN diffs d ON d.id = e.diff_id
            JOIN sub_areas s ON s.id = d.sub_area_id
            LEFT JOIN escalation_resolutions r ON r.escalation_id = e.id
            """
        )
        counts.update(cur.fetchone())

        cur.execute("SELECT last_datoopdt FROM watermark WHERE id = 1")
        row = cur.fetchone()
        counts["watermark"] = row["last_datoopdt"] if row else None

        return counts

def grounding_stats(conn: psycopg.Connection) -> dict:
    """The grounding rate, and what it is made of.

    **This is the number the phase exists to produce.** Everything else in
    this system either follows a rule or waits for a person; the grounding
    rate is the share of uncovered cases the agent settled by reading the
    document, with nobody in the path. A rate of zero means the loop has
    nothing to watch - no autonomous decisions, nothing that can degrade
    quietly - and a rate of one would mean nothing ever escalates, which for
    this domain would be a bug rather than an achievement.

    Denominator is diffs whose rule is NULL: those are exactly the cases the
    rule engine returned NOT_COVERED on, which are exactly the cases that
    reach the ground node. A rule-decided filing was never a candidate for
    grounding and must not dilute the rate.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              COUNT(*)                                          AS uncovered_total,
              COUNT(g.id)                                       AS grounded_total,
              COUNT(*) FILTER (WHERE g.outcome = 'file')        AS grounded_file,
              COUNT(*) FILTER (WHERE g.outcome = 'ignore')      AS grounded_ignore,
              COUNT(*) FILTER (WHERE g.id IS NULL)              AS abstained
            FROM diffs d
            LEFT JOIN groundings g ON g.diff_id = d.id
            WHERE d.rule IS NULL
            """
        )
        stats = dict(cur.fetchone())

        cur.execute(
            """
            SELECT AVG(document_page_count)::float AS mean_page_count,
                   COUNT(DISTINCT clause_id)       AS distinct_clauses_used
            FROM groundings
            """
        )
        stats.update(cur.fetchone())

    total = stats["uncovered_total"] or 0
    stats["grounding_rate"] = (stats["grounded_total"] / total) if total else 0.0
    return stats


def recent_groundings(conn: psycopg.Connection, limit: int = 25) -> list[dict]:
    """Recent grounded decisions with the source each rests on - the cards the
    Business tab shows. Newest first.

    The before and after versions are selected as JSON, not only joined, so a
    field-cited decision can be checked against the record it claims to read
    without a second query per card (review/repo.py's flagged_grounded_runs).
    The before version is LEFT JOINed: a sub-area's first sighting has none,
    and a row that cites a field on a first sighting should surface as a
    failing check rather than vanish from the screen.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT g.id AS grounding_id, g.clause_id, g.clause_quote, g.reasoning,
                   g.outcome, g.citation_kind, g.field_name, g.field_before,
                   g.field_after, g.document_page_count, g.document_chars, g.grounded_at,
                   d.id AS diff_id, d.changed_fields,
                   s.kommunenavn, s.komnr, s.lokplan_id, s.delnr, s.is_test_data,
                   av.doklink,
                   to_jsonb(bv) - 'id' - 'sub_area_id' AS before,
                   to_jsonb(av) - 'id' - 'sub_area_id' AS after
            FROM groundings g
            JOIN diffs d ON d.id = g.diff_id
            JOIN sub_areas s ON s.id = d.sub_area_id
            JOIN sub_area_versions av ON av.id = d.after_version_id
            LEFT JOIN sub_area_versions bv ON bv.id = d.before_version_id
            ORDER BY g.grounded_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()

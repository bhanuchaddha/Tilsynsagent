"""Applies SQL files in db/migrations/ in order, tracking what has run.

No framework - the migration set is small and the ordering is filename-based.
Each file runs once, inside its own transaction, recorded in schema_migrations.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _ensure_tracking_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def applied_migrations(conn: psycopg.Connection) -> set[str]:
    _ensure_tracking_table(conn)
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def run_migrations(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Applies every .sql file not yet recorded, in filename order. Returns
    the filenames actually applied."""
    applied = []
    with psycopg.connect(database_url, autocommit=False) as conn:
        already = applied_migrations(conn)
        conn.commit()
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in already:
                continue
            sql = path.read_text()
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                )
            applied.append(path.name)
    return applied


def main() -> None:
    load_dotenv()
    database_url = os.environ["DATABASE_URL"]
    applied = run_migrations(database_url)
    if applied:
        print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("no pending migrations")


if __name__ == "__main__":
    main()

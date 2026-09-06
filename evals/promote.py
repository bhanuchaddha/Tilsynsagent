"""Promoting an annotated production run into the golden dataset.

**Why this is the step that closes the loop.** Everything before it is
detection: a scorer notices, an alert fires, a person labels. None of that
prevents the failure recurring. The dataset is what makes a fix permanent -
once a case is in it, the CI gate replays it on every pull request forever,
and the same mistake cannot ship again without someone seeing it happen.
That is the difference between "we fixed it" and "it cannot come back".

**It writes to the working tree; a person commits.** Deliberately, and it is
the same reasoning as review/app.py's manual dataset append. The golden
dataset is *a definition of what is correct*, not a log of what happened. A
job that appended to it automatically would be letting production traffic
edit the specification, and the specification would then drift toward
whatever the system already does - which is precisely the failure the
dataset exists to catch. A human reading a diff before committing is the
control, and it costs almost nothing.

**Provenance stays visible.** ``AQ-###`` alongside ``ZL-###`` (hand-labelled
from the register) and ``EC-###`` (answered in the review queue), with
``origin: "annotation-derived"``. Anyone reading cases.jsonl can see which
cases came from a person watching production and which were written up front,
without cross-referencing anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_CASES_PATH = REPO_ROOT / "evals" / "golden" / "cases.jsonl"

ORIGIN = "annotation-derived"
ID_PREFIX = "AQ"

# How a reviewer's verdict becomes a case label. The mapping is not symmetric
# and should not be: "correct" means keep what the agent decided, while every
# way of being wrong means something different about what *should* have
# happened, and that is the thing a test case has to encode.
VERDICT_TO_LABEL = {
    "correct": None,  # keep the agent's own outcome - see _label_for
    "wrong - should have escalated": "escalate",
    "wrong - decided the opposite": "__invert__",
    "register and document disagree": "escalate",
}


def next_case_id(path: Path = GOLDEN_CASES_PATH) -> str:
    """The next AQ-### id. Same shape as review/app.py's _next_case_id, and
    deliberately a separate sequence rather than a shared counter: the id
    prefix is the provenance, and a shared counter would make AQ-007 and
    EC-007 look related when they have nothing to do with each other."""
    if not path.exists():
        return f"{ID_PREFIX}-001"
    numbers = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        case_id = json.loads(line)["id"]
        if case_id.startswith(f"{ID_PREFIX}-"):
            numbers.append(int(case_id.split("-")[1]))
    return f"{ID_PREFIX}-{(max(numbers) + 1) if numbers else 1:03d}"


def _label_for(verdict: str, agent_outcome: str) -> str:
    """The label a case gets, from the reviewer's verdict and what the agent did.

    ``"wrong - decided the opposite"`` inverts file/ignore, because that is
    literally what the reviewer said: the decision was the wrong way round.
    Every other kind of wrong means the agent should not have decided at all,
    which is an escalate label.
    """
    mapped = VERDICT_TO_LABEL.get(verdict, "escalate")
    if mapped is None:
        return agent_outcome
    if mapped == "__invert__":
        return "ignore" if agent_outcome == "file" else "file"
    return mapped


def build_case(
    *,
    trace_id: str,
    verdict: str,
    comment: str | None,
    agent_outcome: str,
    changed_fields: dict,
    before: dict | None,
    after: dict,
    source: dict,
    clause_id: str | None = None,
    clause_quote: str | None = None,
    case_id: str | None = None,
) -> dict:
    """One golden case, in the exact shape evals/cases.py and the scorers read.

    ``rule`` is None and ``escalated_rule`` absent, because these cases were
    never decided by a rule - the rule engine returned NOT_COVERED and the
    document decided. What replaces it is ``grounded_clause``: the clause the
    agent used, kept so a future reader can see not only that the agent was
    wrong but *what it read* when it was wrong, which is the difference
    between a failing test and a diagnosable one.
    """
    return {
        "id": case_id or next_case_id(),
        "label": _label_for(verdict, agent_outcome),
        "reason": comment or f"Annotated in production as: {verdict}",
        "rule": None,
        "rule_set": "v2",
        "origin": ORIGIN,
        "human_verdict": verdict,
        "agent_outcome": agent_outcome,
        "grounded_clause": (
            {"clause_id": clause_id, "clause_quote": clause_quote} if clause_id else None
        ),
        "trace_id": trace_id,
        "source": source,
        "before": before,
        "after": after,
        "changed_fields": changed_fields,
    }


def append_cases(cases: list[dict], *, path: Path = GOLDEN_CASES_PATH) -> list[str]:
    """Appends cases to the golden dataset. Returns the ids written.

    Skips a trace already promoted: a nightly that re-reads completed queue
    items would otherwise re-append the same case every night, and a dataset
    with duplicates scores the same behaviour twice, which quietly weights it.
    """
    existing_traces = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                trace = json.loads(line).get("trace_id")
                if trace:
                    existing_traces.add(trace)

    written = []
    with open(path, "a") as f:
        for case in cases:
            if case.get("trace_id") in existing_traces:
                continue
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
            written.append(case["id"])
            existing_traces.add(case.get("trace_id"))
    return written


def promote_completed(*, dry_run: bool = False) -> list[str]:
    """Reads answered queue items and writes them as golden cases.

    Returns the case ids written (or that would be written, for a dry run).
    """
    from tilsynsagent.obs.annotation import fetch_completed

    completed = fetch_completed()
    if not completed:
        return []

    cases = []
    for item in completed:
        verdict = item.get("verdict")
        if not verdict:
            continue
        context = _trace_context(item["trace_id"])
        if context is None:
            print(
                f"skipping {item['trace_id']}: could not recover the decision it was about",
                file=sys.stderr,
            )
            continue
        cases.append(
            build_case(
                trace_id=item["trace_id"],
                verdict=verdict,
                comment=item.get("comment"),
                case_id=None if dry_run else next_case_id(),
                **context,
            )
        )

    if dry_run:
        for case in cases:
            print(json.dumps(case, ensure_ascii=False, indent=2))
        return [c["id"] for c in cases]
    return append_cases(cases)


def _trace_context(trace_id: str) -> dict | None:
    """The decision a trace was about, read back from the database.

    A Langfuse trace records what happened; the register records what the
    change *was*, and a golden case needs the latter - the before/after
    values a scorer replays. Joined on the trace's session id, which is the
    LangGraph thread id, which the diff can be found from.

    Returns None when the run cannot be located, which is a real case: the
    trace may predate a demo reset. Skipped with a message rather than
    guessed at, because a golden case built from a half-remembered decision
    is worse than no case.
    """
    import os

    import psycopg
    from psycopg.rows import dict_row

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return None

    thread_id = _session_id_for(trace_id)
    if not thread_id:
        return None

    try:
        with psycopg.connect(database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT d.id AS diff_id, d.changed_fields, d.outcome,
                       d.before_version_id, d.after_version_id,
                       s.kommunenavn, s.komnr, s.lokplan_id, s.delnr,
                       g.clause_id, g.clause_quote,
                       av.doklink
                FROM diffs d
                JOIN sub_areas s ON s.id = d.sub_area_id
                JOIN sub_area_versions av ON av.id = d.after_version_id
                LEFT JOIN groundings g ON g.diff_id = d.id
                WHERE av.feature_id LIKE %s OR d.id::text = %s
                ORDER BY d.detected_at DESC
                LIMIT 1
                """,
                (f"%{thread_id}%", thread_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "agent_outcome": row["outcome"],
                "changed_fields": row["changed_fields"],
                "before": _version(cur, row["before_version_id"]),
                "after": _version(cur, row["after_version_id"]),
                "clause_id": row["clause_id"],
                "clause_quote": row["clause_quote"],
                "source": {
                    "register": "plandata.dk",
                    "theme": "theme_pdk_lokalplandelomraade_med_historik",
                    "municipality": row["kommunenavn"],
                    "municipality_code": row["komnr"],
                    "plan_id": row["lokplan_id"],
                    "plan_name": None,
                    "plan_number": None,
                    "sub_area": row["delnr"],
                    "document": row["doklink"],
                },
            }
    except Exception as exc:  # noqa: BLE001
        print(f"could not read the decision behind {trace_id}: {exc}", file=sys.stderr)
        return None


def _version(cur, version_id: int | None) -> dict | None:
    if version_id is None:
        return None
    cur.execute("SELECT * FROM sub_area_versions WHERE id = %s", (version_id,))
    row = cur.fetchone()
    if row is None:
        return None
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


def _session_id_for(trace_id: str) -> str | None:
    """The LangGraph thread id behind a trace - obs/langfuse_setup.py sets it
    as the Langfuse session id precisely so this join is one string."""
    try:
        from langfuse import get_client

        trace = get_client().api.trace.get(trace_id)
        return getattr(trace, "session_id", None)
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote annotated runs into the golden dataset.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be written, write nothing."
    )
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()

    written = promote_completed(dry_run=args.dry_run)
    if not written:
        print("nothing to promote: no completed annotation queue items with a verdict.")
        return 0

    print(f"{'would write' if args.dry_run else 'wrote'} {len(written)} case(s): {', '.join(written)}")
    if not args.dry_run:
        print(
            "\nThese are in your working tree, uncommitted, on purpose.\n"
            "Read the diff before committing: the golden dataset is a definition of\n"
            "what is correct, not a record of what happened."
        )

    from tilsynsagent import obs

    obs.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Builds demo data by feeding hand-labelled golden cases through the real
agent, as if they had arrived from the register.

This does not write filings/escalations directly - it does not know what the
agent will decide. It constructs a before/after SubAreaRecord pair for each
case, drives them through the same graph.make_graph()/run_input() path
runner.py uses, and lets the agent reach its own outcome. So a seeded filing
or escalation on screen is the agent's own decision, not a canned row, and a
seeded escalation is a real interrupt() pause with a resumable thread id.

A handful of each shape:
  - `file` cases (R2, R3)          -> decides on its own, files
  - `escalate` cases (R1, R4)      -> pauses, waits for a person
  - not_covered_cases.jsonl        -> rules silent, model looks, still pauses

Every sub_area this creates is marked is_test_data=True (see
migrations/003_demo_data.sql) so demo/reset.py can remove it without
touching live run history.

Feature ids and lokplan_ids are namespaced under DEMO_LOKPLAN_BASE, well
outside any real plandata.dk lokplan_id observed in the golden dataset, so
a seed run can never collide with a real sub-area.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langgraph.types import Interrupt

from tilsynsagent import obs
from tilsynsagent.demo.documents import build_document, doklink_for
from tilsynsagent.graph import make_graph, run_input
from tilsynsagent.sources.plandata import SubAreaRecord

logger = logging.getLogger("tilsynsagent.demo.seed")

# evals/ is a repo-root package, not part of the installed tilsynsagent
# package. The `tilsynsagent` console script runs from .venv/bin/ and does
# not get the repo root on sys.path automatically, so it is added here
# before the deferred `from evals.cases import load_all_cases` below.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEMO_LOKPLAN_BASE = 900_000_000

# A handful of each shape, picked by rule/origin so the queue shows a
# realistic mix rather than every case.
DEMO_CASE_IDS = [
    # file: R2 (value changed), R3 (value gained)
    "ZL-004", "ZL-005", "ZL-009", "ZL-024",
    # escalate: R1 (impossible record), R4 (value lost)
    "ZL-001", "ZL-002", "ZL-013", "ZL-016",
    # rules silent -> assess, then ground. The whole not-covered set, not a
    # pair of it: grounding is a model decision and it abstains on some of
    # these, correctly. Two cases can therefore both abstain and leave a demo
    # with no grounded decision at all to show, which is the one thing a demo
    # must not do. Five reliably produces some of each, and the mix of
    # grounded and abstained is itself the honest picture.
    "NC-001", "NC-002", "NC-003", "NC-004", "NC-005",
]


def _demo_lokplan_id(case_id: str) -> int:
    """Deterministic across processes (unlike builtin hash(), which is
    salted per-run) so re-seeding after demo/reset.py reuses the same
    lokplan_id for a given case rather than accumulating orphaned rows."""
    digest = hashlib.sha256(case_id.encode()).hexdigest()
    return DEMO_LOKPLAN_BASE + int(digest[:8], 16) % 1_000_000


def _record(
    case: dict, version: dict, *, versionsnr: int, delnr: str, doklink: str | None = None
) -> SubAreaRecord:
    lokplan_id = _demo_lokplan_id(case["id"])
    return SubAreaRecord(
        feature_id=f"demo.{case['id']}.v{versionsnr}",
        lokplan_id=lokplan_id,
        delnr=delnr,
        komnr=case["source"].get("municipality_code") or 0,
        kommunenavn=case["source"].get("municipality"),
        versionsnr=versionsnr,
        status=version.get("status"),
        datoopdt=_as_datoopdt(version.get("updated")),
        maxbygnhjd=version.get("maxbygnhjd"),
        maxetager=version.get("maxetager"),
        bebygpct=version.get("bebygpct"),
        zonestatus=version.get("zonestatus"),
        anvendelsegenerel=version.get("anvendelsegenerel"),
        # A demo document when one was built for this case, otherwise the
        # case's real doklink. Both are fetched by the same code; the demo
        # one is a file:// URL, which documents/cache.py accepts precisely
        # so the demo exercises the real fetch/extract/split path.
        doklink=doklink or case["source"].get("document"),
    )


def _as_datoopdt(updated: str | None) -> str:
    """Golden cases store `updated` as a bare date (e.g. "2021-12-16");
    SubAreaRecord.datoopdt expects the WFS feed's millisecond-precision
    timestamp shape. Any fixed time-of-day is fine here - only the ordering
    between before and after matters, and the two dates already differ."""
    if updated and "T" in updated:
        return updated
    return f"{updated}T00:00:00.000Z"


def _run_case(graph, case: dict, *, doklink: str | None = None) -> str:
    """Feeds one case's before version, then its after version, through the
    real graph. Returns the outcome reached: "filed", "escalated", or
    "error"."""
    delnr = "demo"
    before = _record(case, case["before"], versionsnr=1, delnr=delnr, doklink=doklink)
    after = _record(case, case["after"], versionsnr=2, delnr=delnr, doklink=doklink)

    # Traced and scored exactly like an unattended run - see obs/online.py's
    # score_completed_run. A demo that showed tracing but no online scoring
    # would be demonstrating a different system from the one that runs
    # unattended, which is the one failure mode a demo must not have.
    thread_before = f"demo-{case['id']}-v1"
    graph.invoke(
        run_input(before, is_test_data=True),
        config=obs.trace_config(
            {"configurable": {"thread_id": thread_before}},
            thread_id=thread_before,
            tags=["demo"],
        ),
    )

    thread_after = f"demo-{case['id']}-v2"
    # Tagged "grounded" whenever this case can reach the ground node at all -
    # the LLM judge filters on this tag, and obs/annotation.py walks traces
    # by it to build the review queue.
    tags = ["demo", "grounded"] if doklink else ["demo"]
    result = graph.invoke(
        run_input(after, is_test_data=True),
        config=obs.trace_config(
            {"configurable": {"thread_id": thread_after}},
            thread_id=thread_after,
            tags=tags,
        ),
    )
    obs.score_completed_run(result, record=after, thread_id=thread_after)

    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupts:
        what = interrupts[0].value.get("what_is_unclear") if isinstance(interrupts[0], Interrupt) else interrupts
        logger.info("seed %s -> escalated: %s", case["id"], what)
        return "escalated"

    outcome_result = result.get("result", {}) if isinstance(result, dict) else {}
    if "grounding_id" in outcome_result:
        grounded_outcome = result.get("grounded_outcome") or "file"
        logger.info(
            "seed %s -> grounded %s on clause %s (diff %s)",
            case["id"],
            grounded_outcome,
            result.get("grounded_clause_id"),
            outcome_result["diff_id"],
        )
        return f"grounded_{grounded_outcome}"
    if "filing_id" in outcome_result:
        logger.info("seed %s -> filed (diff %s)", case["id"], outcome_result["diff_id"])
        return "filed"
    if "escalation_id" in outcome_result:
        logger.info("seed %s -> escalated (diff %s)", case["id"], outcome_result["diff_id"])
        return "escalated"
    logger.warning("seed %s -> unexpected result: %s", case["id"], result)
    return "error"


def seed_demo(
    database_url: str | None = None,
    case_ids: list[str] | None = None,
    *,
    template: str | None = "standard",
) -> dict:
    """Runs the demo cases through the real graph. Returns counts.

    ``template`` selects which synthetic plan document each demo case is
    given: "standard" (clauses production can split) or "drifted" (a
    numbering shape it cannot - see demo/documents.py). Pass None to use each
    case's real plandata.dk doklink instead, which is slower (real documents
    are ~8 MB) and depends on the network, but is the honest path for showing
    that grounding works on real documents rather than only on ours.
    """
    from evals.cases import load_all_cases

    database_url = database_url or os.environ["DATABASE_URL"]
    case_ids = case_ids or DEMO_CASE_IDS

    all_cases = {c["id"]: c for c in load_all_cases()}
    cases = [all_cases[cid] for cid in case_ids if cid in all_cases]
    missing = [cid for cid in case_ids if cid not in all_cases]
    if missing:
        logger.warning("seed cases not found in golden dataset, skipping: %s", missing)

    counts = {"filed": 0, "escalated": 0, "grounded_file": 0, "grounded_ignore": 0, "error": 0}
    graph, saver_cm = make_graph(database_url)
    try:
        graph.checkpointer.setup()
        for case in cases:
            doklink = None
            if template is not None:
                path = build_document(
                    plan_id=_demo_lokplan_id(case["id"]), template=template
                )
                doklink = doklink_for(path)
            outcome = _run_case(graph, case, doklink=doklink)
            counts[outcome] = counts.get(outcome, 0) + 1
    finally:
        # flush(), not shutdown(): shutdown() stops Langfuse's background
        # worker thread for the whole process, and seed_demo is called
        # repeatedly - from tests, from the review screen, from a CLI
        # invocation that seeds more than once. A second call after
        # shutdown() hung indefinitely trying to use the torn-down client
        # (reproduced live: three seed_demo() calls in one process, the
        # third hangs on an established Langfuse connection with zero CPU
        # progress). Process-exit shutdown is main()'s job below, called
        # exactly once.
        obs.flush()
        saver_cm.__exit__(None, None, None)

    logger.info("seed complete: %s", counts)
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    from tilsynsagent.db.migrate import run_migrations

    run_migrations(os.environ["DATABASE_URL"])
    try:
        seed_demo()
    finally:
        # The one shutdown() call for this process - see seed_demo's
        # docstring on why it only flushes, not shuts down, internally.
        obs.shutdown()


if __name__ == "__main__":
    main()

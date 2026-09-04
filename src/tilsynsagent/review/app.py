"""The review queue: a Streamlit screen over the escalations a person can
answer. Launched by `tilsynsagent review` (cli.py), which shells out to
`streamlit run` on this file.

Streamlit re-runs this whole script top-to-bottom on every interaction, so
anything that must happen exactly once per click (resuming a paused run,
writing a resolution) is guarded by a form submit rather than by a plain
button, and the underlying writes are already idempotent regardless
(db/repo.py's insert_escalation_resolution is UNIQUE on escalation_id and
raises rather than double-writing; graph.resume_run's Command(resume=...)
against an already-completed thread is a LangGraph no-op - see graph.py's
_escalate_node docstring on re-entry).

Plain and ugly on purpose (docs/PLAN.md Phase 3): three views (queue, one
escalation, after-answer confirmation), no styling beyond Streamlit's
defaults.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import streamlit as st
import streamlit.runtime
from dotenv import load_dotenv

from tilsynsagent.db import repo
from tilsynsagent.demo.reset import reset_demo
from tilsynsagent.graph import make_graph, resume_run
from tilsynsagent.llm.pricing import load_pricing

RESOLVED_BY = os.environ.get("TILSYNSAGENT_REVIEWER", "reviewer")
GOLDEN_CASES_PATH = Path(__file__).resolve().parents[3] / "evals" / "golden" / "cases.jsonl"


def _conn() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"])


def _next_case_id() -> str:
    """ZL-### continues the real hand-labelled sequence; escalation-derived
    cases get their own EC-### sequence so the two provenances stay visually
    distinguishable in the file, matching origin's job (evals/golden/README.md)."""
    if not GOLDEN_CASES_PATH.exists():
        return "EC-001"
    existing = [
        json.loads(line)["id"]
        for line in GOLDEN_CASES_PATH.read_text().splitlines()
        if line.strip()
    ]
    ec_numbers = [int(cid[3:]) for cid in existing if cid.startswith("EC-")]
    return f"EC-{(max(ec_numbers) + 1) if ec_numbers else 1:03d}"


def _append_to_golden_dataset(detail: dict, label: str, reason: str) -> str:
    """Appends one escalation-derived case to evals/golden/cases.jsonl, in
    the exact shape evals/cases.py and the scorers already read (see
    evals/golden/README.md's "What a case records"). Manual, one click at a
    time - see PLAN.md Phase 3, "The answer goes into the dataset -
    manually," on why this is never automatic."""
    case_id = _next_case_id()
    # The rule that *escalated* is recorded under escalated_rule, never under
    # "rule". "rule" means "the rule that produced this label", and the label
    # here came from a person - often disagreeing with the engine, which is
    # the whole reason the case is interesting. Writing the escalating rule
    # into "rule" would assert that R-whatever files this case when it
    # actually escalates it, and tests/test_rules_engine.py's 100% golden
    # check would fail on a claim the dataset never meant to make.
    case = {
        "id": case_id,
        "label": label,
        "reason": reason,
        "rule": None,
        "escalated_rule": detail["rule"],
        "rule_set": detail["rule_set"],
        "origin": "escalation-derived",
        "source": {
            "register": "plandata.dk",
            "theme": "theme_pdk_lokalplandelomraade_med_historik",
            "municipality": detail["kommunenavn"],
            "municipality_code": detail["komnr"],
            "plan_id": detail["lokplan_id"],
            "plan_name": None,
            "plan_number": None,
            "sub_area": detail["delnr"],
            "document": detail["citation"],
        },
        "before": _version_dict(detail["before"]),
        "after": _version_dict(detail["after"]),
        "changed_fields": detail["changed_fields"],
    }
    with open(GOLDEN_CASES_PATH, "a") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")
    return case_id


def _version_dict(row: dict | None) -> dict | None:
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


def _sidebar() -> None:
    with st.sidebar:
        st.header("Demo controls")
        confirm = st.checkbox("I understand this deletes all demo/test data")
        if st.button("Reset demo data", disabled=not confirm):
            counts = reset_demo()
            st.success(
                f"Removed {counts['sub_areas']} demo sub-area(s), "
                f"{counts['checkpoint_threads']} paused run(s)."
            )
            st.session_state.pop("selected_escalation_id", None)
            st.rerun()


def _queue_view() -> None:
    st.title("Review queue")
    with _conn() as conn:
        escalations = repo.list_open_escalations(conn)

    if not escalations:
        st.info("No escalations waiting. Seed demo data (`tilsynsagent seed-demo`) or wait for a run.")
        return

    st.caption(f"{len(escalations)} waiting, newest first")
    for esc in escalations:
        with st.container(border=True):
            cols = st.columns([3, 4, 2])
            cols[0].markdown(
                f"**{esc['kommunenavn'] or esc['komnr']}** · "
                f"plan {esc['lokplan_id']}, sub-area {esc['delnr']}"
            )
            cols[1].write(esc["what_is_unclear"])
            if cols[2].button("Open", key=f"open-{esc['escalation_id']}"):
                st.session_state["selected_escalation_id"] = esc["escalation_id"]
                st.rerun()


def _field_diff_table(before: dict | None, after: dict, changed_fields: dict) -> None:
    """before/after are raw sub_area_versions rows (db/repo.py's
    get_escalation_detail) - _version_dict()'s field names, not
    WATCHED_FIELDS, so "updated"/"version" show even though they are not
    watched: they are exactly what moves in a NOT_COVERED case, which is the
    point of showing them."""
    b_dict = _version_dict(before)
    a_dict = _version_dict(after)
    fields = ["status", "updated", "version", "maxbygnhjd", "maxetager",
              "bebygpct", "zonestatus", "anvendelsegenerel"]
    rows = []
    for field in fields:
        b = b_dict.get(field) if b_dict else None
        a = a_dict.get(field) if a_dict else None
        watched = field in changed_fields
        rows.append({
            "field": field,
            "before": b,
            "after": a,
            "changed": "yes" if watched else "",
        })
    st.dataframe(rows, hide_index=True, width="stretch")


def _escalation_view(escalation_id: int) -> None:
    with _conn() as conn:
        detail = repo.get_escalation_detail(conn, escalation_id)

    if detail is None:
        st.error("Escalation not found.")
        return

    if st.button("← Back to queue"):
        st.session_state.pop("selected_escalation_id", None)
        st.rerun()

    st.title(f"{detail['kommunenavn'] or detail['komnr']} — plan {detail['lokplan_id']}, sub-area {detail['delnr']}")

    already_resolved = detail["resolution_id"] is not None

    st.subheader("What changed")
    _field_diff_table(detail["before"], detail["after"], detail["changed_fields"])

    st.subheader("What the agent could not decide")
    st.write(f"**Rule fired:** {detail['rule'] or 'none — the rule set was silent on this case'}")
    st.write(detail["what_is_unclear"])
    if detail["what_a_person_must_decide"]:
        st.write(f"**What a person must decide:** {detail['what_a_person_must_decide']}")
    if detail["citation"]:
        st.markdown(f"[Source document]({detail['citation']})")

    if already_resolved:
        st.success(
            f"Resolved as **{detail['resolution_label']}** by {detail['resolved_by']} "
            f"at {detail['resolved_at']}: {detail['resolution_reason']}"
        )
        _add_to_dataset_section(detail)
        return

    st.subheader("Answer it")
    with st.form(key=f"resolve-{escalation_id}"):
        label = st.radio("Decision", options=["file", "ignore"], horizontal=True)
        reason = st.text_area("Reason (required)")
        submitted = st.form_submit_button("Submit and resume the run")

    if submitted:
        if not reason.strip():
            st.error("A reason is required.")
            return
        with _conn() as conn, conn.transaction():
            repo.insert_escalation_resolution(
                conn,
                escalation_id=escalation_id,
                label=label,
                reason=reason,
                rule=detail["rule"],
                resolved_by=RESOLVED_BY,
            )
        database_url = os.environ["DATABASE_URL"]
        graph, saver_cm = make_graph(database_url)
        try:
            graph.checkpointer.setup()
            resume_run(graph, detail["thread_id"])
        finally:
            saver_cm.__exit__(None, None, None)
        st.session_state["just_resolved"] = escalation_id
        st.rerun()


def _is_scoreable_label(label: str) -> bool:
    """The eval pipeline only has routes for file/escalate (evals/README.md:
    "There is no ignore route" until Phase 6 grounding lands) - an
    escalation resolved "ignore" must not be offered for evals/golden/
    cases.jsonl, or expected_route() would see a label it cannot map."""
    return label != "ignore"


def _add_to_dataset_section(detail: dict) -> None:
    st.subheader("Add to dataset")

    if not _is_scoreable_label(detail["resolution_label"]):
        st.caption(
            "This resolution was 'ignore', which the eval pipeline cannot score yet - "
            "evals/README.md: there is no ignore route until Phase 6 grounding lands. "
            "Not added to evals/golden/cases.jsonl."
        )
        return

    st.caption(
        "One click appends this resolution to evals/golden/cases.jsonl, tagged "
        "escalation-derived. Manual on purpose — see docs/PLAN.md Phase 3."
    )
    added_key = f"added-{detail['escalation_id']}"
    if st.session_state.get(added_key):
        st.success(f"Added as {st.session_state[added_key]}.")
        return
    if st.button("Add to dataset", key=f"add-dataset-{detail['escalation_id']}"):
        case_id = _append_to_golden_dataset(
            detail, detail["resolution_label"], detail["resolution_reason"]
        )
        st.session_state[added_key] = case_id
        st.rerun()


def _status_view() -> None:
    """What the system has watched, decided, escalated and cost.

    Live and demo figures are shown separately and never summed: a status
    page that counts demo activity as production activity is worse than no
    status page, because it is confidently wrong.
    """
    st.title("Status")

    with _conn() as conn:
        counts = repo.status_counts(conn)

    st.caption(
        "Live figures are the unattended runs. Demo figures come from "
        "`tilsynsagent seed-demo` and are cleared by the reset in the sidebar."
    )

    live, demo = st.columns(2)
    with live:
        st.subheader("Live")
        st.metric("Sub-areas watched", counts["sub_areas_live"])
        st.metric("Versions recorded", counts["versions_live"])
        st.metric("Filed", counts["filings_live"])
        st.metric("Escalated", counts["escalations_live"])
    with demo:
        st.subheader("Demo")
        st.metric("Sub-areas watched", counts["sub_areas_demo"])
        st.metric("Versions recorded", counts["versions_demo"])
        st.metric("Filed", counts["filings_demo"])
        st.metric("Escalated", counts["escalations_demo"])

    st.divider()
    st.subheader("Open escalations")
    st.metric("Waiting for a person", counts["escalations_open"])

    st.subheader("Watermark")
    st.write(
        f"Last source update processed: **{counts['watermark'] or 'never run'}**"
    )
    if counts["last_filed_at"]:
        st.write(f"Last filing: {counts['last_filed_at']}")
    if counts["last_escalated_at"]:
        st.write(f"Last escalation: {counts['last_escalated_at']}")

    st.divider()
    st.subheader("Cost per run")
    pricing = load_pricing()
    st.write(
        "Token prices are recorded by hand in `config/pricing.toml` with the "
        "date each was checked, because Groq publishes no machine-readable "
        "price feed. Per-run cost is computed from actual token usage, not "
        "estimated:"
    )
    st.table(
        [
            {
                "model": e["model"],
                "input $/1M": e["input_per_1m"],
                "output $/1M": e["output_per_1m"],
                "checked on": e["checked_on"],
            }
            for e in pricing.entries()
        ]
    )
    st.caption(
        "The most recent recorded eval pass cost $0.0078 for 27 model calls "
        "(docs/evals/baseline-2026-09-04.md); the same pass on gpt-oss-20b "
        "cost $0.0040 (docs/evals/model-experiment-2026-09-04.md)."
    )



def main() -> None:
    load_dotenv()
    st.set_page_config(page_title="Tilsynsagent — review queue", layout="wide")

    _sidebar()

    queue_tab, status_tab = st.tabs(["Review queue", "Status"])

    with status_tab:
        _status_view()

    with queue_tab:
        _main_queue()


def _main_queue() -> None:
    if st.session_state.get("just_resolved"):
        st.success("Run resumed and finished. The escalation is now resolved.")
        del st.session_state["just_resolved"]

    selected = st.session_state.get("selected_escalation_id")
    if selected:
        _escalation_view(selected)
    else:
        _queue_view()


# Streamlit executes this file top-to-bottom as a script on `streamlit run`
# (cli.py's `review` command) - runtime.exists() is true only in that
# context, so plain `import tilsynsagent.review.app` (tests, the golden-case
# append helpers exercised directly) does not also launch the UI.
if streamlit.runtime.exists():
    main()

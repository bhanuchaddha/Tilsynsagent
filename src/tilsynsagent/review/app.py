"""The review queue: a Streamlit screen over the escalations a person can
answer. Launched by `tilsynsagent review` (cli.py), which shells out to
`streamlit run` on this file.

Streamlit re-runs this whole script top-to-bottom on every interaction, so
anything that must happen exactly once per click (resuming a paused run,
writing a resolution) is guarded by a form submit rather than by a plain
button. The underlying writes are also idempotent regardless
(db/repo.py's insert_escalation_resolution is UNIQUE on escalation_id;
graph.resume_run's Command(resume=...) against an already-completed thread
is a LangGraph no-op).

Three views: queue, one escalation, after-answer confirmation.

Langfuse owns traces, scores, annotation queues, prompt versions, and
dataset runs. This app owns operational state Langfuse has no concept of:
the review queue of paused runs, the watermark, open escalations, the
grounding rate, and the alert files. The Business tab deep-links out to
Langfuse rather than reimplementing it.

Every tab body executes on every rerun whether visible or not, so queries
stay narrow. Nothing `st.*`-shaped is testable, so real logic lives in
`review/repo.py`.
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
from tilsynsagent.obs.alerts import open_alerts
from tilsynsagent.review import repo as ui

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
    the exact shape evals/cases.py and the scorers read. Manual, one click
    at a time."""
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


def _add_to_dataset_section(detail: dict) -> None:
    st.subheader("Add to dataset")

    st.caption(
        "One click appends this resolution to evals/golden/cases.jsonl, "
        "tagged escalation-derived."
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
    st.caption("Cost per model, computed from the pricing table below.")



def _langfuse_base() -> str | None:
    return os.environ.get("LANGFUSE_BASE_URL")


@st.cache_data(ttl=300, show_spinner=False)
def _document_text(doklink: str) -> str:
    """The full text of a plan document, memoised for this render.

    A tab body runs on every Streamlit rerun and these are large PDFs, so the
    read is cached twice: documents/cache.py keeps the bytes on disk
    content-addressed, and this keeps the extracted text in memory for five
    minutes.

    Never raises. An empty string makes the clause checks fail rather than
    silently pass.
    """
    if not doklink:
        return ""
    try:
        from tilsynsagent.documents import extract_text, fetch_document

        fetched = fetch_document(doklink)
        if not fetched.ok:
            return ""
        extracted = extract_text(fetched.content)
        return extracted.text if extracted.ok else ""
    except Exception:  # noqa: BLE001 - a scoring read must not break the screen
        return ""


def _business_view() -> None:
    """For someone who will never open the code.

    One question is asked of this reader, and it is the only one they can
    answer that nobody else can: **was the agent right?** Everything on this
    screen exists to make that question answerable in under a minute - the
    change the agent saw, what it decided, and the clause it decided from.

    No scorer names in the banner beyond the one that fired, no prompt
    versions, no case ids. Those are the Developer tab's business, and the
    same failure appears there one dataset later.
    """
    st.title("Production quality")

    with _conn() as conn:
        groundings = repo.recent_groundings(conn, limit=25)

    flagged = ui.flagged_grounded_runs(groundings, document_text=_document_text)
    queue_url = ui.langfuse_queue_url(_langfuse_base(), os.environ.get("LANGFUSE_QUEUE_URL"))

    alerts = [a for a in open_alerts() if "quality-drop" in a["name"]]
    if alerts:
        newest = alerts[0]
        st.error(f"**{newest['title']}**")
        st.caption(
            f"Recorded in `{newest['name']}`"
            + (f", seen again {newest['recurrences']} time(s) since." if newest["recurrences"] else ".")
        )
    elif flagged:
        st.warning(
            f"{len(flagged)} recent decision(s) failed an automated check. "
            "No quality alert has fired yet - a single failure is a case, not a trend."
        )
    else:
        st.success("No quality alert. Recent decisions passed their automated checks.")

    st.divider()
    st.subheader("Decisions the agent made on its own")
    st.caption(
        "These are the only decisions in this system that no person reviewed before "
        "they were recorded. The agent read the plan document and settled the case "
        "from a clause in it. For each one, the question is simply whether it was right."
    )

    if not groundings:
        st.info(
            "None yet. Grounded decisions only happen on changes the written rules do "
            "not cover. Run `tilsynsagent seed-demo` to produce some."
        )
        return

    flagged_ids = {f["grounding_id"] for f in flagged}
    for row in groundings:
        is_flagged = row["grounding_id"] in flagged_ids
        with st.container(border=True):
            header, action = st.columns([5, 2])
            header.markdown(
                f"**{row['kommunenavn'] or row['komnr']}** · plan {row['lokplan_id']}, "
                f"sub-area {row['delnr']}"
                + ("  ·  *demo*" if row["is_test_data"] else "")
            )
            if is_flagged:
                header.markdown(":red[**Flagged by an automated check**]")

            st.write(f"**What changed:** {_changed_fields_sentence(row['changed_fields'])}")
            st.write(
                f"**What the agent decided:** "
                + ("file it as something a reader should see" if row["outcome"] == "file"
                   else "nothing new for a reader to see")
            )
            st.markdown(
                f"**The clause it used** — {row['clause_id']}:\n\n> {row['clause_quote']}"
            )
            if row["reasoning"]:
                st.caption(row["reasoning"])

            if is_flagged:
                for name, comment in next(f["failing"] for f in flagged if f["grounding_id"] == row["grounding_id"]):
                    st.markdown(f":red[⚠ {name}] — {comment}")

            if queue_url:
                action.link_button("Review in Langfuse", queue_url)
            if row["doklink"]:
                action.markdown(f"[Source document]({row['doklink']})")


def _changed_fields_sentence(changed_fields: dict) -> str:
    """Changed fields in plain language, for a reader who does not know the
    register's Danish column names."""
    names = {
        "maxbygnhjd": "maximum building height",
        "maxetager": "maximum storeys",
        "bebygpct": "site coverage percentage",
        "zonestatus": "zone status",
        "anvendelsegenerel": "permitted use",
        "status": "plan status",
    }
    if not changed_fields:
        return "nothing visible in the register changed - only the plan's revision date moved"
    parts = []
    for field, change in changed_fields.items():
        label = names.get(field, field)
        before = change.get("before") if isinstance(change, dict) else None
        after = change.get("after") if isinstance(change, dict) else None
        parts.append(f"{label}: {before if before is not None else 'blank'} → {after if after is not None else 'blank'}")
    return "; ".join(parts)


def _developer_view() -> None:
    """The same failure the Business tab shows, one dataset later.

    The one thing this screen must do that a score cannot: separate a **new
    case** from a **case that newly fails**. A new failing case is the dataset
    doing its job - somebody labelled a production run because the agent got
    it wrong. A case that used to pass and now fails is a regression. The
    responses are opposite and an aggregate makes them look identical.
    """
    st.title("Regressions")

    comparison = ui.latest_comparison()
    if comparison is None:
        st.info(
            "Fewer than two nightly runs recorded. Run `tilsynsagent nightly` twice - "
            "the second run is the first one that has anything to compare against."
        )
        return

    previous_score = comparison.previous.mean_score if comparison.previous else 0.0
    if comparison.regressed:
        st.error(
            f"**Regression: {previous_score:.3f} → {comparison.current.mean_score:.3f}**"
        )
    else:
        st.success(
            f"No regression: {previous_score:.3f} → {comparison.current.mean_score:.3f}"
        )
    st.caption(
        f"`{comparison.previous.name if comparison.previous else '—'}` → "
        f"`{comparison.current.name}`, tolerance ±0.02 — temperature=0 is not "
        "determinism on a hosted mixture-of-experts endpoint, and an alert that fires "
        "on run-to-run wobble gets muted."
    )

    st.divider()
    new_col, failing_col = st.columns(2)
    with new_col:
        st.subheader(f"New cases ({len(comparison.new_case_ids)})")
        st.caption(
            "Cases the dataset gained since the last run. A new case that fails is **not "
            "a regression** — it is a case a person added precisely because the agent got "
            "it wrong. The score dropping here is the dataset working."
        )
        st.write(", ".join(f"`{c}`" for c in comparison.new_case_ids) or "None.")
    with failing_col:
        st.subheader(f"Newly failing ({len(comparison.newly_failing_case_ids)})")
        st.caption(
            "Cases that used to pass and now do not. The dataset did not change; the "
            "behaviour did. **These are the regressions.**"
        )
        st.write(", ".join(f"`{c}`" for c in comparison.newly_failing_case_ids) or "None.")

    st.divider()
    st.subheader("Per-scorer movement")
    st.dataframe(
        [
            {
                "scorer": name,
                "previous": f"{comparison.previous.per_scorer.get(name, 0):.3f}" if comparison.previous else "—",
                "current": f"{comparison.current.per_scorer.get(name, 0):.3f}",
                "delta": f"{delta:+.3f}",
                "regressed": "yes" if name in comparison.regressed_scorers else "",
            }
            for name, delta in sorted(comparison.per_scorer_delta.items())
        ],
        hide_index=True,
        width="stretch",
    )

    st.subheader("Prompt versions")
    st.caption(
        "A prompt edit changes behaviour exactly as much as a code change and appears in "
        "no code diff. If a version moved between these runs, look there first. The fix "
        "is a new version in Langfuse with the `production` label moved to it — no deploy."
    )
    st.dataframe(
        [
            {
                "step": step,
                "previous": (comparison.previous.prompt_versions or {}).get(step, "—") if comparison.previous else "—",
                "current": version,
                "changed": "yes" if comparison.previous and (comparison.previous.prompt_versions or {}).get(step) != version else "",
            }
            for step, version in sorted(comparison.current.prompt_versions.items())
        ],
        hide_index=True,
        width="stretch",
    )

    st.subheader("Judge vs. human")
    reports = sorted(
        (Path(__file__).resolve().parents[3] / "var" / "evals").glob("judge-agreement-*.md"),
        reverse=True,
    )
    if not reports:
        st.caption(
            "Not measured yet. Agreement needs human labels from the annotation queue, "
            "which arrive days after the judge scores they are about."
        )
    else:
        st.caption(f"Most recent: `{reports[0].name}`")
        st.markdown(reports[0].read_text())


def _operator_view() -> None:
    """What is running, what is stuck, and what grounding costs."""
    st.title("Operations")

    with _conn() as conn:
        counts = repo.status_counts(conn)
        grounding = repo.grounding_stats(conn)
        groundings = repo.recent_groundings(conn, limit=50)

    st.subheader("Grounding rate")
    st.caption(
        "The number this whole layer exists to produce: how often the agent settles a "
        "case the written rules do not cover by reading the plan document, with no "
        "person in the path."
    )
    a, b, c = st.columns(3)
    a.metric("Decided from the document", grounding["grounded_total"])
    b.metric("Handed to a person", grounding["abstained"])
    c.metric("Grounding rate", f"{grounding['grounding_rate']:.0%}")
    st.write(ui.grounding_rate_sentence(grounding))

    d, e = st.columns(2)
    d.metric("Grounded: filed", grounding["grounded_file"])
    e.metric("Grounded: ignored", grounding["grounded_ignore"])
    st.caption(
        "'Ignored' is not the agent doing nothing. It is a positive claim backed by a "
        "quoted clause — the most heavily evidenced outcome the system produces."
    )

    st.divider()
    st.subheader("Open alerts")
    alerts = open_alerts()
    summary = ui.alert_summary(alerts)
    if not alerts:
        st.success("No alerts on file.")
    else:
        st.write(
            f"{summary['total']} alert file(s) — "
            f"{summary['real']} real, {summary['demo']} demo, "
            f"{summary['recurring']} with repeat occurrences."
        )
        for alert in alerts[:10]:
            st.markdown(
                f"- {'*(demo)* ' if alert['is_demo'] else ''}**{alert['title']}** "
                f"— `{alert['name']}`"
                + (f" · seen again ×{alert['recurrences']}" if alert["recurrences"] else "")
            )
        st.caption(
            "Alerts are files, not Slack messages: Langfuse's free tier forgets after 30 "
            "days, so anything that must outlive that is committed at the moment it "
            "happens. A repeat appends to the same file rather than creating a new one."
        )

    st.divider()
    st.subheader("Runs and queue")
    f, g, h = st.columns(3)
    f.metric("Open escalations", counts["escalations_open"])
    g.metric("Sub-areas watched (live)", counts["sub_areas_live"])
    h.metric("Versions recorded (live)", counts["versions_live"])
    st.write(f"Watermark — last source update processed: **{counts['watermark'] or 'never run'}**")

    st.subheader("Document cost")
    st.write(ui.document_cache_rate(groundings))


def main() -> None:
    load_dotenv()
    st.set_page_config(page_title="Tilsynsagent — review queue", layout="wide")

    _sidebar()

    # Every tab body executes on every rerun, visible or not - so each of these
    # keeps its queries narrow, and none of them fetches a document.
    queue_tab, status_tab, business_tab, developer_tab, operator_tab = st.tabs(
        ["Review queue", "Status", "Business", "Developer", "Operator"]
    )

    with status_tab:
        _status_view()

    with business_tab:
        _business_view()

    with developer_tab:
        _developer_view()

    with operator_tab:
        _operator_view()

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

"""Synthetic demo documents, and the drift they demonstrate.

These go through the entire real path - written to disk, served over file://,
fetched, parsed by pypdf, split by the same regex production uses. Only the
content is synthetic. The tests that matter are the ones about the drifted
template, because that template is the whole of demo 2.
"""

from __future__ import annotations

import pytest

from tilsynsagent.demo.documents import (
    DEMO_CLAUSES,
    TEMPLATES,
    build_document,
    clear_demo_documents,
    doklink_for,
)
from tilsynsagent.documents import (
    extract_text,
    fetch_document,
    retrieve_for_fields,
    split_clauses,
)


def _clauses(tmp_path, template):
    path = build_document(plan_id=900001, template=template, directory=tmp_path)
    result = fetch_document(doklink_for(path), directory=tmp_path / f"cache-{template}")
    assert result.ok
    extracted = extract_text(result.content)
    assert extracted.ok, extracted.error
    return split_clauses(extracted.text), extracted


def test_an_unknown_template_is_refused(tmp_path):
    with pytest.raises(ValueError):
        build_document(plan_id=1, template="nonsense", directory=tmp_path)


def test_the_standard_template_round_trips_every_clause(tmp_path):
    clauses, _ = _clauses(tmp_path, "standard")
    assert [c.clause_id for c in clauses] == [c[0] for c in DEMO_CLAUSES]


def test_a_demo_document_is_long_enough_to_be_realistic(tmp_path):
    """A document whose entire text is the clauses retrieval is looking for
    makes retrieval look easy. Real plans are ~80,000 characters, of which
    the relevant clause is one paragraph."""
    _, extracted = _clauses(tmp_path, "standard")
    assert len(extracted.text) > 3000


def test_a_demo_document_travels_the_real_fetch_path(tmp_path):
    """file:// is accepted by documents/cache.py precisely so the demo
    exercises the real fetch, extract and split rather than a stub."""
    path = build_document(plan_id=900002, template="standard", directory=tmp_path)
    link = doklink_for(path)
    assert link.startswith("file://")
    assert fetch_document(link, directory=tmp_path / "c").ok


def test_the_drifted_template_loses_clauses_but_not_all_of_them(tmp_path):
    """A template failing 100% of the time is a bug demo - the obvious answer
    is 'fix the parser'. Partial degradation is a drift demo, and it is the
    one that survives questioning."""
    standard, _ = _clauses(tmp_path, "standard")
    drifted, _ = _clauses(tmp_path, "drifted")
    assert 0 < len(drifted) < len(standard)


def test_the_drifted_template_hides_the_clauses_the_demo_changes(tmp_path):
    """The demo changes height and storeys, so those are the clauses that
    must go missing - otherwise the grounding rate would not move."""
    drifted, _ = _clauses(tmp_path, "drifted")
    assert retrieve_for_fields(drifted, {"maxbygnhjd": {}}) == []
    assert retrieve_for_fields(drifted, {"maxetager": {}}) == []


def test_the_standard_template_grounds_those_same_fields(tmp_path):
    standard, _ = _clauses(tmp_path, "standard")
    assert retrieve_for_fields(standard, {"maxbygnhjd": {}})
    assert retrieve_for_fields(standard, {"maxetager": {}})


def test_the_drift_is_not_the_section_sign_shape(tmp_path):
    """The plan originally proposed `§ 6.3` as the drift. The corpus probe
    found that shape is 19% of the real corpus and production was widened to
    accept it - so demonstrating drift with it would demonstrate nothing, and
    would be contradicted by this project's own recorded evidence."""
    from tilsynsagent.documents.clauses import split_clauses as split

    assert [c.clause_id for c in split("§ 6.3 Bebyggelse i 2 etager.")] == ["6.3"]


def test_a_verbatim_quote_survives_the_pdf_round_trip(tmp_path):
    """The Danish decimal comma in 8,5 is load-bearing for demo 1: the
    verbatim scorer fails a quote that tidies it to 8.5, so the comma has to
    survive being written to and read back out of a PDF."""
    clauses, _ = _clauses(tmp_path, "standard")
    clause = next(c for c in clauses if c.clause_id == "6.2")
    assert "8,5" in clause.text


def test_clearing_demo_documents_returns_a_count(tmp_path):
    build_document(plan_id=900003, template="standard", directory=tmp_path)
    build_document(plan_id=900004, template="standard", directory=tmp_path)
    assert clear_demo_documents(directory=tmp_path) == 2
    assert clear_demo_documents(directory=tmp_path) == 0


def test_every_template_builds(tmp_path):
    for template in TEMPLATES:
        assert build_document(plan_id=900005, template=template, directory=tmp_path).exists()

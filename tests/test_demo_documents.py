"""Synthetic demo documents.

These go through the entire real path - written to disk, served over file://,
fetched, parsed by pypdf. Only the content is synthetic.

The tests that matter now are the ones asserting the document is *realistic*:
long enough that finding the relevant clause is real work, and carrying a
Danish decimal comma that survives the PDF round trip. The drifted-template
tests were deleted with the template in stage 1 - it existed to break a clause
splitter that no longer exists.
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
from tilsynsagent.documents import extract_text, fetch_document


def _text(tmp_path, template="standard"):
    path = build_document(plan_id=900001, template=template, directory=tmp_path)
    result = fetch_document(doklink_for(path), directory=tmp_path / f"cache-{template}")
    assert result.ok
    extracted = extract_text(result.content)
    assert extracted.ok, extracted.error
    return extracted


def test_an_unknown_template_is_refused(tmp_path):
    with pytest.raises(ValueError):
        build_document(plan_id=1, template="nonsense", directory=tmp_path)


def test_the_standard_template_round_trips_every_clause(tmp_path):
    """Every clause reaches the model, because the whole document does. The
    check is on the text itself now rather than on a splitter's output."""
    extracted = _text(tmp_path)
    for clause_id, clause_text in DEMO_CLAUSES:
        assert clause_id in extracted.text
        # The first few words are enough: pypdf reflows long paragraphs
        # across line breaks, which _normalise handles at scoring time.
        assert " ".join(clause_text.split()[:4]) in " ".join(extracted.text.split())


def test_a_demo_document_is_long_enough_to_be_realistic(tmp_path):
    """A document whose entire text is the clauses retrieval is looking for
    makes the model's job look easy. Real plans are ~80,000 characters, of
    which the relevant clause is one paragraph."""
    extracted = _text(tmp_path)
    assert len(extracted.text) > 3000


def test_a_demo_document_travels_the_real_fetch_path(tmp_path):
    """file:// is accepted by documents/cache.py precisely so the demo
    exercises the real fetch, extract and split rather than a stub."""
    path = build_document(plan_id=900002, template="standard", directory=tmp_path)
    link = doklink_for(path)
    assert link.startswith("file://")
    assert fetch_document(link, directory=tmp_path / "c").ok


def test_a_verbatim_quote_survives_the_pdf_round_trip(tmp_path):
    """The Danish decimal comma in 8,5 is load-bearing for demo 1: the
    verbatim scorer fails a quote that tidies it to 8.5, so the comma has to
    survive being written to and read back out of a PDF."""
    extracted = _text(tmp_path)
    assert "8,5" in extracted.text


def test_clearing_demo_documents_returns_a_count(tmp_path):
    build_document(plan_id=900003, template="standard", directory=tmp_path)
    build_document(plan_id=900004, template="standard", directory=tmp_path)
    assert clear_demo_documents(directory=tmp_path) == 2
    assert clear_demo_documents(directory=tmp_path) == 0


def test_every_template_builds(tmp_path):
    for template in TEMPLATES:
        assert build_document(plan_id=900005, template=template, directory=tmp_path).exists()

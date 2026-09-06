"""Clause splitting and retrieval.

The regex here was set by measurement, not by guess (see
docs/evals/corpus-probe-2026-09-05.md: 22/31 real documents with a bare
`6.3` pattern, 28/31 once `§ 6.3` is accepted). These tests pin both shapes
and pin abstention, which is the behaviour that keeps grounding safe.
"""

from __future__ import annotations

from tilsynsagent.documents.clauses import (
    Clause,
    FIELD_KEYWORDS,
    MAX_RETRIEVED_CHARS,
    render_clauses,
    retrieve_for_fields,
    split_clauses,
)

DOC = """\
Redegørelse

Denne lokalplan omhandler bygningshøjder og etageantal i området.

Bestemmelser

1.1 Lokalplanens formål er at fastlægge anvendelsen til boligformål.

6.2 Bebyggelse må ikke opføres med en større højde end 8,5 m over terræn.

6.3 Der må opføres 2 bygninger i 2 etager med hver 8 boliger.

11.1 En ændring af planens status medfører ikke ændringer i bestemmelserne.
"""

SECTION_DOC = DOC.replace("\n6.2 ", "\n§ 6.2 ").replace("\n6.3 ", "\n§ 6.3 ")


def test_bare_numbering_splits():
    ids = [c.clause_id for c in split_clauses(DOC)]
    assert ids == ["1.1", "6.2", "6.3", "11.1"]


def test_section_sign_numbering_splits_too():
    """19% of the real corpus numbers clauses `§ 6.3`. Matching only the bare
    form grounded 22 of 31 documents; accepting this grounds 28."""
    ids = [c.clause_id for c in split_clauses(SECTION_DOC)]
    assert ids == ["1.1", "6.2", "6.3", "11.1"]


def test_a_cross_reference_inside_prose_is_not_a_clause():
    """"jf. 6.3" mid-sentence is a reference, not a clause opening. The line
    anchor is what distinguishes them."""
    text = DOC + "\nDette gælder også for bebyggelse, jf. 6.3 ovenfor.\n"
    ids = [c.clause_id for c in split_clauses(text)]
    assert ids.count("6.3") == 1


def test_unrecognised_numbering_yields_nothing_rather_than_guessing():
    """Three real corpus documents legitimately produce nothing. Returning []
    routes the record to a person, which is the safe direction."""
    assert split_clauses("Der er ingen nummererede bestemmelser her.") == []
    assert split_clauses("") == []


def test_a_clause_keeps_its_own_number_so_a_quote_is_checkable():
    """obs/grounded.py compares a model's quote against this text, so the
    clause body must include its own heading."""
    clause = next(c for c in split_clauses(DOC) if c.clause_id == "6.2")
    assert clause.text.startswith("6.2")
    assert "8,5 m" in clause.text


def test_duplicate_ids_keep_the_longest_occurrence():
    """A number appearing in a table of contents and again as the real clause
    must resolve to the body, not the index line."""
    text = "6.3 Bebyggelse\n\n6.3 Der må opføres 2 bygninger i 2 etager med hver 8 boliger.\n"
    clauses = split_clauses(text)
    assert len(clauses) == 1
    assert "bygninger" in clauses[0].text


def test_retrieval_finds_the_clause_for_the_changed_field():
    clauses = split_clauses(DOC)
    ids = [c.clause_id for c in retrieve_for_fields(clauses, {"maxetager": {}})]
    assert "6.3" in ids


def test_retrieval_abstains_when_nothing_matches():
    """An empty retrieval means the ground node has nothing to ground on and
    the record escalates - which is correct, not a gap to paper over."""
    clauses = [Clause("1.1", "1.1 Lokalplanens formål er at sikre vejadgang.")]
    assert retrieve_for_fields(clauses, {"maxbygnhjd": {}}) == []


def test_no_changed_fields_asks_a_different_question_rather_than_abstaining():
    """An empty changed_fields is not an empty question. It is a revision
    where nothing the register watches moved - the largest single shape in the
    not-covered population - and it asks whether the change altered anything a
    reader would see. Keying on changed_fields alone returned nothing for
    every one of them, so the whole population the ground node exists to serve
    abstained without ever having looked. Measured: grounding rate went from
    0% to 40% on the demo set when this fallback was added."""
    selected = retrieve_for_fields(split_clauses(DOC), {})
    assert [c.clause_id for c in selected] == ["11.1"]


def test_no_changed_fields_still_abstains_when_no_clause_speaks_to_it():
    """The fallback is a different question, not a guarantee of an answer."""
    clauses = [Clause("7.1", "7.1 Facader skal fremstå i blank mur eller træ.")]
    assert retrieve_for_fields(clauses, {}) == []


def test_no_clauses_at_all_always_abstains():
    assert retrieve_for_fields([], {}) == []
    assert retrieve_for_fields([], {"maxbygnhjd": {}}) == []


def test_retrieved_clauses_are_returned_in_document_order():
    """A model handed 11.1 before 6.2 has been given a false picture of the
    document's structure."""
    clauses = split_clauses(DOC)
    selected = retrieve_for_fields(clauses, {"maxbygnhjd": {}, "maxetager": {}, "status": {}})
    ids = [c.clause_id for c in selected]
    assert ids == sorted(ids, key=lambda i: (int(i.split(".")[0]), int(i.split(".")[1])))


def test_retrieval_respects_the_character_budget():
    clauses = [Clause(f"{i}.1", f"{i}.1 " + "højde " * 400) for i in range(1, 6)]
    selected = retrieve_for_fields(clauses, {"maxbygnhjd": {}})
    assert len(render_clauses(selected)) <= MAX_RETRIEVED_CHARS + len(selected[0].text)


def test_a_specific_keyword_outranks_a_generic_one():
    """"bebyggelse" appears in most clauses of most plans; "bygningshøjde"
    names the field. The specific one must win or retrieval returns noise."""
    generic = Clause("2.1", "2.1 Bebyggelse i området skal ske efter en samlet plan.")
    specific = Clause("6.2", "6.2 Bygningshøjde må ikke overstige 8,5 m.")
    selected = retrieve_for_fields([generic, specific], {"maxbygnhjd": {}}, max_clauses=1)
    assert [c.clause_id for c in selected] == ["6.2"]


def test_every_watched_field_has_retrieval_vocabulary():
    """A watched field with no keywords would silently never retrieve
    anything, which reads as "the document said nothing" rather than as a
    missing map entry."""
    from tilsynsagent.rules.engine import WATCHED_FIELDS

    assert set(WATCHED_FIELDS) <= set(FIELD_KEYWORDS)

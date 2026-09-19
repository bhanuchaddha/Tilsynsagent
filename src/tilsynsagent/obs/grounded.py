"""Online scorers for grounded decisions - the ones no person reviews.

**Why these are the most important scorers in the project.** Every other
decision this system makes is either produced by deterministic code or read
by a human before it means anything. Grounded decisions are neither. They are
the only population that can get worse without anyone noticing, which makes
them the only population where a scorer is doing load-bearing work rather
than confirming what a rule already guaranteed.

**Three failure classes, and only one of them is catchable here.** This
matters more than the code:

| Failure | Caught by | Why not the others |
|---|---|---|
| Quotes a clause that is not in the document | **these scorers** | Purely mechanical |
| Quotes a real clause that does not support the conclusion | **an LLM judge** | Needs reading and reasoning |
| Register and document genuinely disagree | **a human** | Needs authority over what is true |

Everything in this module is the first row. It is mechanical, it is cheap, it
runs on every grounded decision, and it cannot tell you whether a correctly
quoted clause actually justifies the conclusion drawn from it. Pretending
otherwise would be the failure mode this table exists to prevent. The judge
(obs/judge.py) covers the second row and is itself measured against the third.

**The surviving rule: a judge may never decide an outcome, nor be the only
thing between a decision and a reader.** These code scorers run first and
independently.

Reuses obs/online.py's ``OnlineScore`` shape so ``record_scores`` needs no
change - a grounded score lands on the same trace, by the same session id, as
every other online score.
"""

from __future__ import annotations

import re

from tilsynsagent.obs.online import OnlineScore

# Fields whose vocabulary a quote should mention if it genuinely governs them.
QUOTE_FIELD_TERMS: dict[str, tuple[str, ...]] = {
    "maxbygnhjd": ("højde", "hojde", "meter", "m", "etage"),
    "maxetager": ("etage", "etager", "etageantal"),
    "bebygpct": ("bebyggelsesprocent", "procent", "%", "etageareal"),
    "zonestatus": ("zone", "byzone", "landzone", "sommerhus"),
    "anvendelsegenerel": ("anvend", "formål", "formaal", "bolig", "erhverv", "område"),
}


def _normalise(text: str) -> str:
    """Whitespace and case only.

    Deliberately not more than that. A quote that turns ``8,5`` into ``8.5``
    has been *modified*, and must fail: the Danish decimal comma is how the
    number appears in the document, a reader checking the decision will search
    for the document's form, and a model that silently reformats a figure is a
    model that might silently change one. Whitespace and case are normalised
    because PDF extraction inserts line breaks and column gaps that no human
    typed and no model should be penalised for.
    """
    return re.sub(r"\s+", " ", text).strip().lower()


def _mentions(text: str, term: str) -> bool:
    r"""Whether a quote uses a term, as a word rather than as a substring.

    Substring matching is unusable here and was caught by a test rather than
    by reading: the unit "m" for metres appears inside "mur" (masonry), so a
    clause about facade materials scored as a clause about building height.
    A word boundary on the left and a boundary-or-digit-suffix on the right
    keeps "8,5 m" and "meter" while rejecting "mur" and "kommune".

    Danish letters are not in Python's \w for the ASCII flag, so the pattern
    is built explicitly rather than relying on \b, which would treat "å" as a
    boundary and match "højde" inside "bygningshøjde" only by accident.
    """
    if not term:
        return False
    if term == "%":
        return "%" in text
    return re.search(rf"(?<![a-zæøå0-9]){re.escape(term)}(?![a-zæøå])", text) is not None


def clause_id_exists(*, clause_id: str, document_text: str) -> OnlineScore:
    """The cited clause number actually appears in the document.

    **This scorer changed shape in stage 1 and got stronger.** It used to
    check the cited id against ``retrieved_clause_ids`` - the four clauses
    retrieval had handed the model. That was a check against a pre-filtered
    subset, and it disappeared with retrieval. Checking against the whole
    document is what survives, and it is the better check: it asks whether
    the clause exists *in the source*, not whether it was in a list this
    system built.

    A clause id that appears nowhere in the document is a fabrication in the
    strictest sense, and grounded decisions are the only ones no person
    reviews. This is the cheapest and most conclusive check in the project.

    The id must appear as a clause *number*, not as any occurrence of those
    digits: "6.3" occurs inside "16.3" and inside a date, and either would
    let a fabricated number score as real.
    """
    if not clause_id:
        return OnlineScore("clause_id_exists", 0.0, "grounded decision cited no clause")
    if not document_text:
        return OnlineScore(
            "clause_id_exists", 0.0, "no document text available to check the clause id against"
        )
    pattern = rf"(?<![0-9.]){re.escape(clause_id)}(?![0-9])"
    if re.search(pattern, document_text):
        return OnlineScore(
            "clause_id_exists", 1.0, f"clause {clause_id} appears in the document"
        )
    return OnlineScore(
        "clause_id_exists",
        0.0,
        f"cited clause {clause_id!r}, which does not appear in the document text",
    )


def clause_is_verbatim(*, clause_quote: str, document_text: str) -> OnlineScore:
    """The quote appears in the document, character for character.

    Also stronger than the version it replaces, and for the same reason: it
    checks against the whole document rather than against the one retrieved
    clause the model was handed. A quote that is real but came from a
    different part of the plan now passes this check honestly, where before
    it failed for the wrong reason.

    Whitespace and case are normalised; nothing else is - see ``_normalise``.
    A digit that changed, a comma turned into a point, an expanded
    abbreviation, or a translation all fail here, and they should: the quote
    is the reader's route back to the document, and a quote that cannot be
    found in the document is not a citation.
    """
    if not clause_quote:
        return OnlineScore("clause_is_verbatim", 0.0, "grounded decision quoted nothing")
    if not document_text:
        return OnlineScore(
            "clause_is_verbatim", 0.0, "no document text available to check the quote against"
        )
    if _normalise(clause_quote) in _normalise(document_text):
        return OnlineScore(
            "clause_is_verbatim", 1.0, "quote appears verbatim in the document"
        )
    return OnlineScore(
        "clause_is_verbatim",
        0.0,
        f"quote is not in the document verbatim: {clause_quote[:120]!r}",
    )


def field_citation_is_real(
    *, field_name: str, field_before: str, field_after: str, before: dict, after: dict
) -> OnlineScore:
    """A field-cited decision names a real field and quotes its real values.

    The counterpart to ``clause_id_exists`` for the citation kind stage 1
    introduced, and it exists so that the new path is not the unchecked one.
    A decision resting on ``status: F -> V`` is only traceable if ``status``
    is a field of the record and those really were its two values; otherwise
    it is the same fabrication as an invented clause number, and it would be
    the easier one to get away with because it looks like arithmetic on data
    the reader assumes was verified.

    Values are compared as strings after normalisation, because the model is
    given rendered values and returns rendered values - ``versionsnr: 3``
    reaches it as "3". An empty side is accepted (a field that gained or lost
    a value) but only when the other side matches, and both sides empty is
    not a citation at all.
    """
    if not field_name:
        return OnlineScore("field_citation_is_real", 0.0, "grounded decision named no field")
    if field_name not in (before or {}) and field_name not in (after or {}):
        return OnlineScore(
            "field_citation_is_real",
            0.0,
            f"cited field {field_name!r}, which is not a field of the record "
            f"(fields: {sorted(set(before or {}) | set(after or {}))})",
        )

    def _rendered(version: dict, key: str) -> str:
        value = (version or {}).get(key)
        return "" if value is None else _normalise(str(value))

    actual_before = _rendered(before, field_name)
    actual_after = _rendered(after, field_name)
    claimed_before = _normalise(field_before)
    claimed_after = _normalise(field_after)

    if not claimed_before and not claimed_after:
        return OnlineScore(
            "field_citation_is_real",
            0.0,
            f"cited field {field_name!r} with neither a before nor an after value",
        )
    mismatches = []
    if claimed_before and claimed_before != actual_before:
        mismatches.append(f"before {field_before!r} but record has {actual_before!r}")
    if claimed_after and claimed_after != actual_after:
        mismatches.append(f"after {field_after!r} but record has {actual_after!r}")
    if mismatches:
        return OnlineScore(
            "field_citation_is_real",
            0.0,
            f"cited field {field_name!r} with values that are not in the record: "
            + "; ".join(mismatches),
        )
    return OnlineScore(
        "field_citation_is_real",
        1.0,
        f"field {field_name} really did go {actual_before or '(none)'} -> "
        f"{actual_after or '(none)'}",
    )


def quote_mentions_the_changed_field(
    *, clause_quote: str, changed_fields: dict
) -> OnlineScore:
    """The quoted text is about the field that changed.

    A weaker check than the two above, and honestly so: it is a keyword
    floor, not a judgement about whether the clause supports the conclusion.
    A perfectly quoted clause about parking does not justify a decision about
    building height, and this catches the blatant version of that. The
    subtle version - a real height clause that does not actually settle *this*
    change - needs the judge, and no amount of keyword matching will substitute.

    **It abstains on most grounded runs, and that is now the common case.**
    ``changed_fields`` holds the five watched fields, and the not-covered
    route is by definition the one where all five are identical - so it is
    ``{}`` on every case that reaches grounding through the normal path, and
    this scorer returns 1.0 having checked nothing. That is stated plainly
    rather than dressed up: it is a real check only for a grounded run that
    also had a watched-field change, which is rare. The load-bearing checks
    on a clause citation are clause_id_exists and clause_is_verbatim; whether
    the quote actually supports the conclusion is the LLM judge's job.

    Its committed drift threshold (0.85) is set for the population it can
    actually judge; a window of free 1.0s does not move it either way.
    """
    if not clause_quote:
        return OnlineScore(
            "quote_mentions_the_changed_field", 0.0, "grounded decision quoted nothing"
        )
    checkable = [f for f in (changed_fields or {}) if f in QUOTE_FIELD_TERMS]
    if not checkable:
        return OnlineScore(
            "quote_mentions_the_changed_field",
            1.0,
            "no changed field has vocabulary this scorer can check",
        )
    lowered = _normalise(clause_quote)
    for field in checkable:
        for term in QUOTE_FIELD_TERMS[field]:
            if _mentions(lowered, term):
                return OnlineScore(
                    "quote_mentions_the_changed_field",
                    1.0,
                    f"quote mentions {term!r}, the vocabulary of changed field {field!r}",
                )
    return OnlineScore(
        "quote_mentions_the_changed_field",
        0.0,
        f"quote does not mention the vocabulary of any changed field {sorted(checkable)}",
    )


def abstained_when_ungrounded(
    *,
    grounded: bool,
    outcome: str,
    citation_kind: str,
    clause_id: str,
    clause_quote: str,
    field_name: str = "",
    field_before: str = "",
    field_after: str = "",
) -> OnlineScore:
    """A run that could not ground did not act anyway.

    The structural counterpart to online.py's ``escalated_when_uncovered``,
    and checkable with no expected answer for the same reason: an ungrounded
    run reaching an autonomous outcome means the decision architecture leaked,
    which is a bug rather than a quality drop. Also fails the inverse - a
    grounded run recorded without the evidence that makes it grounded.

    Both citation kinds count as evidence, and neither counts when it is only
    half present. This mirrors Grounding.is_decided and the schema CHECK in
    migration 005 deliberately: the same condition is stated in the model
    contract, in the write tool, in the schema, and here, so a leak has to get
    past four independent statements of it rather than one.
    """
    if not grounded:
        if outcome in ("file", "ignore"):
            return OnlineScore(
                "abstained_when_ungrounded",
                0.0,
                f"run could not ground the case but recorded outcome {outcome!r} anyway",
            )
        return OnlineScore(
            "abstained_when_ungrounded", 1.0, "ungrounded case escalated rather than acted on"
        )
    if citation_kind == "clause":
        if not clause_id or not clause_quote:
            return OnlineScore(
                "abstained_when_ungrounded",
                0.0,
                "run acted as grounded on a clause without both a clause id and a quote",
            )
        return OnlineScore(
            "abstained_when_ungrounded", 1.0, f"grounded {outcome} cites clause {clause_id}"
        )
    if citation_kind == "field":
        if not field_name or not (field_before or field_after):
            return OnlineScore(
                "abstained_when_ungrounded",
                0.0,
                "run acted as grounded on a field without both a name and a value",
            )
        return OnlineScore(
            "abstained_when_ungrounded",
            1.0,
            f"grounded {outcome} cites field {field_name}: "
            f"{field_before or '(none)'} -> {field_after or '(none)'}",
        )
    return OnlineScore(
        "abstained_when_ungrounded",
        0.0,
        f"run acted as grounded with citation_kind {citation_kind!r}, which is neither "
        "'clause' nor 'field' and cannot be checked",
    )


def score_grounded_run(
    *,
    grounded: bool,
    outcome: str,
    citation_kind: str,
    clause_id: str,
    clause_quote: str,
    field_name: str = "",
    field_before: str = "",
    field_after: str = "",
    document_text: str,
    before: dict | None = None,
    after: dict | None = None,
    changed_fields: dict,
) -> list[OnlineScore]:
    """Every grounded scorer, applied to one grounded run.

    ``abstained_when_ungrounded`` runs on *every* run that reached the ground
    node, decided or not - it is the one that catches an ungrounded run
    acting anyway, which by definition happens when nothing else here would
    have anything to score.

    The evidence scorers run only on decided runs, and which ones run depends
    on what the decision rested on. A clause-cited decision is checked against
    the document; a field-cited one against the record. Running the clause
    scorers on a field citation would produce three guaranteed zeros for a
    decision that is perfectly traceable, which would poison exactly the rates
    drift.py watches.
    """
    scores = [
        abstained_when_ungrounded(
            grounded=grounded,
            outcome=outcome,
            citation_kind=citation_kind,
            clause_id=clause_id,
            clause_quote=clause_quote,
            field_name=field_name,
            field_before=field_before,
            field_after=field_after,
        )
    ]
    if grounded and citation_kind == "clause":
        scores.extend(
            [
                clause_id_exists(clause_id=clause_id, document_text=document_text),
                clause_is_verbatim(clause_quote=clause_quote, document_text=document_text),
                quote_mentions_the_changed_field(
                    clause_quote=clause_quote, changed_fields=changed_fields
                ),
            ]
        )
    elif grounded and citation_kind == "field":
        scores.append(
            field_citation_is_real(
                field_name=field_name,
                field_before=field_before,
                field_after=field_after,
                before=before or {},
                after=after or {},
            )
        )
    return scores

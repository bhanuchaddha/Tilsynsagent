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

# Fields whose vocabulary a quote should mention if it genuinely governs
# them. Same map as documents/clauses.FIELD_KEYWORDS, deliberately not
# imported from it: that map is tuned for *retrieval recall* (find any clause
# that might be relevant) and this one is a *verification floor* (the quote
# must actually be about the field). Tying them together would mean loosening
# retrieval silently loosens verification, which is the direction that hides
# problems rather than surfacing them.
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


def clause_id_exists(*, clause_id: str, retrieved_clause_ids: list[str]) -> OnlineScore:
    """The cited clause is one the agent was actually shown.

    A clause id outside the retrieved set is a fabrication in the strictest
    sense: the model did not read that clause, because it was never given it.
    This is the cheapest and most conclusive check in the project.
    """
    if not clause_id:
        return OnlineScore("clause_id_exists", 0.0, "grounded decision cited no clause")
    if clause_id in (retrieved_clause_ids or []):
        return OnlineScore(
            "clause_id_exists", 1.0, f"clause {clause_id} was among those retrieved"
        )
    return OnlineScore(
        "clause_id_exists",
        0.0,
        f"cited clause {clause_id!r}, which was not retrieved from the document "
        f"(retrieved: {sorted(retrieved_clause_ids or [])})",
    )


def clause_is_verbatim(*, clause_quote: str, clause_text: str) -> OnlineScore:
    """The quote appears in the clause, character for character.

    Whitespace and case are normalised; nothing else is - see ``_normalise``.
    A digit that changed, a comma turned into a point, an expanded
    abbreviation, or a translation all fail here, and they should: the quote
    is the reader's route back to the document, and a quote that cannot be
    found in the document is not a citation.
    """
    if not clause_quote:
        return OnlineScore("clause_is_verbatim", 0.0, "grounded decision quoted nothing")
    if not clause_text:
        return OnlineScore(
            "clause_is_verbatim", 0.0, "no clause text available to check the quote against"
        )
    if _normalise(clause_quote) in _normalise(clause_text):
        return OnlineScore(
            "clause_is_verbatim", 1.0, "quote appears verbatim in the cited clause"
        )
    return OnlineScore(
        "clause_is_verbatim",
        0.0,
        f"quote is not in the cited clause verbatim: {clause_quote[:120]!r}",
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
    *, grounded: bool, outcome: str, clause_id: str, clause_quote: str
) -> OnlineScore:
    """A run that could not ground did not act anyway.

    The structural counterpart to online.py's ``escalated_when_uncovered``,
    and checkable with no expected answer for the same reason: an ungrounded
    run reaching an autonomous outcome means the decision architecture leaked,
    which is a bug rather than a quality drop. Also fails the inverse - a
    grounded run recorded without the evidence that makes it grounded.
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
    if not clause_id or not clause_quote:
        return OnlineScore(
            "abstained_when_ungrounded",
            0.0,
            "run acted as grounded without both a clause id and a quote",
        )
    return OnlineScore(
        "abstained_when_ungrounded", 1.0, f"grounded {outcome} cites clause {clause_id}"
    )


def score_grounded_run(
    *,
    grounded: bool,
    outcome: str,
    clause_id: str,
    clause_quote: str,
    clause_text: str,
    retrieved_clause_ids: list[str],
    changed_fields: dict,
) -> list[OnlineScore]:
    """Every grounded scorer, applied to one grounded run.

    ``abstained_when_ungrounded`` runs on *every* run that reached the ground
    node, decided or not - it is the one that catches an ungrounded run
    acting anyway, which by definition happens when nothing else here would
    have anything to score. The evidence scorers run only on decided runs,
    where there is a quote to check.
    """
    scores = [
        abstained_when_ungrounded(
            grounded=grounded,
            outcome=outcome,
            clause_id=clause_id,
            clause_quote=clause_quote,
        )
    ]
    if grounded:
        scores.extend(
            [
                clause_id_exists(
                    clause_id=clause_id, retrieved_clause_ids=retrieved_clause_ids
                ),
                clause_is_verbatim(clause_quote=clause_quote, clause_text=clause_text),
                quote_mentions_the_changed_field(
                    clause_quote=clause_quote, changed_fields=changed_fields
                ),
            ]
        )
    return scores

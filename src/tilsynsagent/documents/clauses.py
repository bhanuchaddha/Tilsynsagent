"""Splitting a plan document into numbered clauses, and picking the few that
bear on the fields that actually changed.

**Why clause-targeted retrieval and not embeddings.** A cited clause id is
checkable: `clause_id_exists` and `clause_is_verbatim` in obs/grounded.py are
mechanical string operations against the document the agent was handed. A
vector match is not checkable in that sense - "the retriever thought these
were similar" is not evidence a reader can audit, and CLAUDE.md's one rule
requires every autonomous decision traceable to the source that justified it.
Retrieval that produces a *quotable identifier* is the only kind that
survives that rule.

**The regex accepts `§ 6.3` as well as `6.3`, and that is measured, not
guessed.** The corpus probe over all 31 real documents in the golden set
(docs/evals/corpus-probe-2026-09-05.md) found two dominant templates: bare
`6.3 <text>` and `§ 6.3 <text>`. Matching only the bare form grounded 22 of
31 documents; accepting the section sign as well grounds 28 of 31. The three
that still yield nothing are two scans with no text layer and one 1970s plan
with prose-only numbering - all three correctly abstain, which is the safe
direction.

**Retrieval abstains rather than guessing.** ``retrieve_for_fields`` returns
an empty list when no clause scores above zero for the changed fields. An
empty retrieval means the ground node has nothing to ground on and the record
escalates - which is the correct behaviour and the thing the phase must not
be tempted to paper over.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A numbered clause heading: "6.3 ", "6.3. ", "§ 6.3 ". Anchored to the start
# of a line so a cross-reference inside prose ("jf. 6.3") is not mistaken for
# a clause opening. See the module docstring on why the section sign is
# optional rather than a separate pattern.
CLAUSE_RE = re.compile(r"^[ \t]*(?:§[ \t]*)?(\d{1,2}\.\d{1,2})[ \t.]", re.MULTILINE)

# How much clause text one grounding call may be given. ~4k characters is
# roughly 1k tokens - enough for four full clauses, small enough that a
# grounded call costs a fraction of what handing the model the whole 27k-token
# document would, and small enough that the model cannot quietly answer from
# some other part of the plan it was never asked about.
MAX_RETRIEVED_CHARS = 4000

# A single clause longer than this is almost always a split failure - a
# heading matched inside a table of contents, swallowing the rest of the
# document. Truncated rather than dropped: the opening of such a clause is
# still the right text.
MAX_CLAUSE_CHARS = 2000

# Danish keywords per watched register field. These are the register's own
# field names translated into the vocabulary a plan document actually uses -
# `maxbygnhjd` never appears in a PDF, `bygningshøjde` and `højde` do. Each
# keyword appears in 26-29 of the 31 corpus documents, so this is a map
# against measured vocabulary rather than a guess at it.
FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "maxbygnhjd": ("højde", "bygningshøjde", "hoejde", "meter over", "m over terræn"),
    "maxetager": ("etage", "etager", "etageantal", "i 2 etager", "fulde etager"),
    "bebygpct": ("bebyggelsesprocent", "bebyggelse", "procent", "etageareal"),
    "zonestatus": ("zone", "byzone", "landzone", "sommerhusområde", "zonestatus"),
    "anvendelsegenerel": ("anvendelse", "anvendes", "formål", "bebyggelse", "område"),
    # Not watched fields, but they are what moves in a no-visible-change
    # revision - the exact population the not-covered path handles.
    "status": ("vedtaget", "aflyst", "status", "forslag"),
}

# Generic words that match almost any clause. Kept in FIELD_KEYWORDS because
# they do carry signal, but scored lower so a clause matching only these does
# not outrank one naming the field directly.
_WEAK_KEYWORDS = frozenset(
    {"bebyggelse", "område", "procent", "anvendes", "zone", "status", "meter over"}
)


@dataclass(frozen=True)
class Clause:
    """One numbered clause, as it appears in the document.

    ``text`` is the clause body *including* its own number, because that is
    what a quote must be checkable against - obs/grounded.py's
    ``clause_is_verbatim`` compares the model's quote to this string with
    only whitespace and case normalised.
    """

    clause_id: str
    text: str

    def score_for(self, keywords: tuple[str, ...]) -> int:
        """How well this clause matches a field's vocabulary.

        Weighted, not a bare count: a clause that names the field directly
        ("bygningshøjde") beats one that merely mentions "bebyggelse", which
        appears in most clauses of most plans.
        """
        lowered = self.text.lower()
        score = 0
        for keyword in keywords:
            if keyword in lowered:
                score += 1 if keyword in _WEAK_KEYWORDS else 3
        return score


def split_clauses(text: str, *, max_clause_chars: int = MAX_CLAUSE_CHARS) -> list[Clause]:
    """Splits extracted document text into numbered clauses.

    A clause runs from its own heading to the start of the next one. Returns
    an empty list for a document whose numbering this does not recognise -
    which is an abstention, not an error: see the module docstring on the
    three corpus documents that legitimately produce nothing.

    Duplicate clause ids (a number appearing once in a table of contents and
    again as the real clause) keep the *longest* occurrence, which is the
    body rather than the index line.
    """
    if not text:
        return []

    matches = list(CLAUSE_RE.finditer(text))
    if not matches:
        return []

    by_id: dict[str, str] = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.start() : end].strip()
        if len(body) > max_clause_chars:
            body = body[:max_clause_chars].rstrip()
        clause_id = match.group(1)
        if len(body) > len(by_id.get(clause_id, "")):
            by_id[clause_id] = body

    return [Clause(cid, by_id[cid]) for cid in sorted(by_id, key=_clause_sort_key)]


def _clause_sort_key(clause_id: str) -> tuple[int, int]:
    major, _, minor = clause_id.partition(".")
    return int(major), int(minor)


# What a no-visible-change revision is asked about. The largest single shape
# in the not-covered population is a revision where *no watched field moved*
# at all - the plan's status or version advanced and nothing else the register
# tracks did. Keying retrieval on changed_fields alone returns nothing for
# those, so every one of them abstains, and the whole population the ground
# node exists to serve would never be grounded.
#
# The question that population actually poses is different, and the vocabulary
# has to match it: not "what does the document say about height", but "does
# any clause say whether an administrative change of this kind alters what a
# reader would see on the land". Danish plans answer that explicitly, in a
# clause about what a status change does and does not affect.
NO_CHANGE_KEYWORDS: tuple[str, ...] = (
    "ændring",
    "aendring",
    "status",
    "vedtaget",
    "bestemmelser",
    "medfører ikke",
    "gælder",
    "ophæves",
    "aflyst",
    "endeligt",
)


def retrieve_for_fields(
    clauses: list[Clause],
    changed_fields: dict,
    *,
    max_clauses: int = 4,
    max_chars: int = MAX_RETRIEVED_CHARS,
) -> list[Clause]:
    """The few clauses that bear on what changed.

    Scored per field and merged, so a two-field change gets clauses for both
    rather than four clauses about whichever field scored loudest. Returns
    ``[]`` when nothing scores - abstention, which the caller must treat as
    "cannot ground" rather than as "no restrictions apply".

    **An empty ``changed_fields`` is not an empty question.** It is a
    revision where nothing the register watches moved, which is the single
    largest shape in the not-covered population, and it asks whether the
    change altered anything a reader would see. Retrieval falls back to
    NO_CHANGE_KEYWORDS for it rather than returning nothing - returning
    nothing would abstain on the whole population without ever having looked.
    """
    if not clauses:
        return []

    if not changed_fields:
        return _retrieve_by_keywords(
            clauses, NO_CHANGE_KEYWORDS, max_clauses=max_clauses, max_chars=max_chars
        )

    best: dict[str, int] = {}
    for field in changed_fields:
        keywords = FIELD_KEYWORDS.get(field)
        if not keywords:
            continue
        for clause in clauses:
            score = clause.score_for(keywords)
            if score > best.get(clause.clause_id, 0):
                best[clause.clause_id] = score

    ranked = sorted(
        (c for c in clauses if best.get(c.clause_id, 0) > 0),
        key=lambda c: (-best[c.clause_id], _clause_sort_key(c.clause_id)),
    )

    return _take(ranked, max_clauses=max_clauses, max_chars=max_chars)


def _retrieve_by_keywords(
    clauses: list[Clause], keywords: tuple[str, ...], *, max_clauses: int, max_chars: int
) -> list[Clause]:
    """Rank clauses against one keyword list. Abstains when nothing scores."""
    scored = [(c.score_for(keywords), c) for c in clauses]
    ranked = [c for score, c in sorted(scored, key=lambda p: (-p[0], _clause_sort_key(p[1].clause_id))) if score > 0]
    return _take(ranked, max_clauses=max_clauses, max_chars=max_chars)


def _take(ranked: list[Clause], *, max_clauses: int, max_chars: int) -> list[Clause]:
    selected: list[Clause] = []
    used = 0
    for clause in ranked[:max_clauses]:
        if used + len(clause.text) > max_chars and selected:
            break
        selected.append(clause)
        used += len(clause.text)

    # Presented to the model in document order, not score order: a plan reads
    # top to bottom and a model handed 7.4 before 6.3 has been given a subtly
    # false picture of the document's structure.
    return sorted(selected, key=lambda c: _clause_sort_key(c.clause_id))


def render_clauses(clauses: list[Clause]) -> str:
    """The retrieved clauses as the grounding prompt presents them."""
    return "\n\n".join(c.text for c in clauses)

# Corpus probe: can clause retrieval work on real plan documents?

**Date:** 2026-09-05
**Question:** across every real source document the golden dataset points at,
how many can be split into numbered clauses at all?
**Verdict: go.** 28 of 31 (90%) yield at least one clause, median 55 distinct
clauses per document.

## Why this was measured before anything was built

Grounding is the first thing this system does with no human in the path, and
the whole feedback loop depends on it producing decisions. But the clause
regex had only ever been tried on *one* document (47 clauses). One document
is not evidence about a corpus of municipal PDFs spanning fifty years of
templates.

If a large fraction of real documents yielded zero clauses, retrieval would
abstain and every record would escalate. That is *safe* - abstention is the
correct failure direction - but the grounding rate would be too low to
demonstrate anything, and the phase would have been built on a premise
nobody checked. This number is cheap to measure and decides whether the
phase works, so it was measured first.

## What was run

Every distinct `doklink` in `evals/golden/cases.jsonl` and
`not_covered_cases.jsonl` - **31 distinct documents** - fetched live, text
extracted with `pypdf`, and split on the clause-heading regex.

## The corpus

| | |
|---|---|
| Documents | 31 |
| Fetched HTTP 200 | 31 / 31 |
| Median size | 7.9 MB |
| **Largest** | **156.1 MB** |
| Median pages | 53 |
| Pages range | 9 - 348 |
| Median extracted text | 80,127 chars (~23k tokens) |
| AES-encrypted, empty password | 1 / 31 |

## The finding that changed the design

Two clause-numbering templates are in common use, not one:

| Regex | Documents yielding >= 1 clause |
|---|---|
| `^\s*(\d{1,2}\.\d{1,2})\s+` (bare `6.3`) | **22 / 31** (71%) |
| `^[ \t]*(?:§[ \t]*)?(\d{1,2}\.\d{1,2})[ \t.]` (also `§ 6.3`) | **28 / 31** (90%) |

Six documents number their clauses `§ 6.3`. They are ordinary modern
municipal plans, not an edge case - and they had been invisible to the
original pattern. The shipped `CLAUSE_RE` accepts both.

This is worth stating plainly because the phase plan had proposed inventing
a synthetic "drifted" template that emits `§ 6.3` to demonstrate input drift.
That template is not hypothetical; it is 19% of the real corpus, and
production now handles it. The drift demo therefore uses a numbering shape
that is genuinely outside what production accepts - see
`src/tilsynsagent/demo/documents.py`.

## Distribution (with the shipped regex)

Distinct clauses per document:

```
0, 0, 0, 1, 1, 1, 11, 23, 30, 42, 43, 47, 49, 51, 53, 55, 55, 58,
61, 67, 74, 74, 86, 88, 90, 92, 93, 116, 135, 136, 157
```

Median **55**. Three quarters of the corpus yields more than 40 clauses -
comfortably more than the 4 a single grounding call retrieves.

## The three that yield nothing, and why that is correct

| Document | Pages | Extracted chars | Why |
|---|---|---|---|
| `20_1071769_1690462796590.pdf` | 43 | 1,945 | Scan, no text layer |
| `20_1059615_APPROVED_1195550042867.pdf` | 9 | 10,571 | 1972 plan; prose-only numbering ("§ l") |
| `20_9735056_1655297758410.pdf` | 52 | 80,127 | Has text, numbering this regex does not recognise |

All three abstain. An abstention routes the record to a person, which is the
system's stated behaviour when the rule set does not cover a case. None of
them produces a wrong grounded decision, which is the only outcome that would
have been a problem.

`extract.py` refuses any document under 2,000 characters of extractable text
outright (the scan case), rather than letting the splitter run on OCR-less
page furniture.

## Keyword coverage

The Danish vocabulary `clauses.FIELD_KEYWORDS` retrieves against, and how
many of the 31 documents contain each term at all:

| Keyword | Documents |
|---|---|
| `højde` | 29 |
| `anvendelse` | 29 |
| `zone` | 28 |
| `etager` | 26 |
| `bebyggelsesprocent` | 26 |

## Operating consequences recorded here

1. **A 40 MB stream ceiling is not theoretical.** One real document is
   156 MB. The ceiling is enforced on bytes received, not on
   `Content-Length` - see `documents/cache.py`.
2. **`cryptography` is a hard dependency.** One corpus document is
   AES-encrypted with an empty password; `pypdf` raises `DependencyError`
   without it, which would present as "grounding never works" rather than as
   a missing package.
3. **Caching matters.** Median 7.9 MB per record fetched on every run is a
   real cost. The cache key is `sha256(doklink)`, and since the register
   embeds a timestamp in the URL, a revised document gets a new key with no
   invalidation logic.

## Reproducing

The probe is `tests/test_documents_live.py::test_corpus_probe`, deselected by
default (it fetches 31 documents, ~400 MB). Run it with
`uv run pytest tests/test_documents_live.py -m live`.

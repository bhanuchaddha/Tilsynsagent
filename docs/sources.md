# Sources

Every source the agent reads is publicly available. Nothing private,
confidential, or access-restricted is used.

A source is not added to this list until one real fetch against it has
succeeded. Documented availability is not sufficient — the constraints that
matter (rate limits, encoding, structure drift, access policy) only appear on a
live call.

---

## Verified

### Plandata.dk — the Danish national plan register

**What it publishes.** Every municipal local plan (*lokalplan*) in Denmark, as
structured records rather than only as PDFs. A plan is divided into sub-areas
(*delområder*), and each sub-area carries the development rules that apply to
it: maximum building height, maximum storeys, built percentage, zone status,
permitted use.

**Endpoint.** WFS 2.0 at `https://geoserver.plandata.dk/geoserver/ows`,
GeoJSON output.

**Verified 2026-08-07** with live calls:

| Check | Result |
|---|---|
| Service reachable | `GetCapabilities` → 200, `application/xml` |
| Current adopted sub-areas | `pdk:theme_pdk_lokalplandelomraade_vedtaget` → **66,232 records** |
| Versioned history | `pdk:theme_pdk_lokalplandelomraade_med_historik` → **110,042 records** |
| Change feed | `CQL_FILTER=datoopdt AFTER <timestamp>` → **643 records** updated in ~5 weeks |
| Before/after pairs | Confirmed — same sub-area at multiple versions, with differing regulated values |

**Fields that matter.** `maxbygnhjd` (max building height, metres), `maxetager`
(max storeys), `bebygpct` (built percentage), `zonestatus`, `anvendelsegenerel`
(general use), `kommunenavn` (municipality), `datoopdt` (last updated),
`versionsnr`, `status`, and `doklink` — a direct URL to the source PDF.

**Why this source works for this project:**

- **Change detection is exact, not fuzzy.** A height limit moving from 12.5 m to
  10 m is an unambiguous, machine-detectable fact. No judgement is needed about
  *whether* something changed, which isolates the interesting judgement: whether
  it matters.
- **Citation is precise.** Every record carries `doklink` to the source
  document, so "traceable to the source that justified it" has an exact target.
- **Real history exists.** Before/after pairs can be drawn from the archive, so
  the golden dataset is labelled from real changes rather than invented ones.
- **Volume is right.** Roughly 15–20 updates a day nationally — enough to
  produce real cases, small enough to reason about.

**Access.** Public, no authentication. Polling is deliberately generous; the
change feed is queried by timestamp rather than by re-fetching the full set.

---

## Also reachable, not currently used

Verified reachable 2026-08-07, retained as fallbacks:

- **retsinformation.dk** — national legal information. `robots.txt` permits
  crawling, publishes a sitemap.
- **lovtidende.dk** — official gazette. Same.
- **finanstilsynet.dk** — financial regulator guidance.

These are candidates if the primary source proves insufficient. They are
document-centric rather than field-centric, so change detection against them is
text diffing rather than field comparison — a harder problem, and not the one
this project is trying to demonstrate first.

---

## Format for future entries

Each verified source records: what it publishes, the endpoint, how change is
detected, the date its fetch was verified with real calls, the observed volume,
and any access constraints.

# The domain

What the register actually contains, and the vocabulary used everywhere else
in this codebase. Read this before `sources.md` (what we fetch and why it was
chosen) or `rules.md` (what a change means).

## Hierarchy

```
Region (Hovedstaden, Sjælland, ...)
  └─ Commune / kommune        (komnr, kommunenavn — e.g. Gentofte, 157)
       └─ Local plan / lokalplan   (lokplan_id — e.g. "Søborg Hovedgade 11-13")
            └─ Sub-area / delområde   (delnr — e.g. "I")  ← the record's basic unit
                 └─ Version              (versionsnr, datoopdt)
```

- **Commune** — the municipal authority that adopts local plans. Identified by
  `komnr` (a stable numeric code) and `kommunenavn` (name).
- **Local plan (*lokalplan*)** — one zoning plan a commune adopts for an area.
  Identified by `lokplan_id`, with a human name and a plan number.
- **Sub-area (*delområde*)** — a local plan is carved into one or more
  sub-areas, because different parts of the same plan can carry different
  rules (a height limit in one corner, a different permitted use in another).
  Identified within a plan by `delnr` (e.g. "I", "II", "3").
- **Version** — a sub-area's rules get revised over time. The feed keeps every
  version ever recorded, not just the current one — see
  [Two layers, one choice](#two-layers-one-choice) below.

**The natural key for "this sub-area across its whole history" is the pair
`(lokplan_id, delnr)`** — neither field alone identifies a sub-area. A single
version of it is identified by `feature_id` (see
[Feature ids](#feature-ids-and-layer-names) below). This is exactly the split
`sub_areas` / `sub_area_versions` uses in the schema
(`src/tilsynsagent/db/migrations/001_initial.sql`).

**The feed itself is flat.** There is no nested commune → plan → sub-area
structure in the WFS response — every feature is one sub-area-version row with
all four hierarchy fields (`komnr`, `lokplan_id`, `delnr`, `versionsnr`) sitting
side by side as plain properties, the way a denormalised SQL view would. The
hierarchy above is something this project imposes by keying on those fields; it
is not a structure the source hands us.

## Geographic scope: none, currently

There is **no filter** on commune, region, or `komnr` anywhere in the running
pipeline. `PlandataClient.fetch_since()` / `count_since()`
(`src/tilsynsagent/sources/plandata.py`) query the whole national WFS feed
unconditionally.

"Zealand" is not a running filter — it describes where the **golden dataset's
34 hand-labelled cases** happened to be sampled from
(`evals/golden/README.md`: *"37,717 historical sub-area records across
Zealand... the 34 were sampled across change shapes and spread across
municipalities"*). Two cases are deliberately drawn from outside Zealand to
capture a pattern (height/storeys transposed) that doesn't occur in the
Zealand sample. So: the system watches all of Denmark; Zealand is the sampling
frame the rule set was checked against, not a geographic gate the agent
enforces.

## Two layers, one choice

Plandata.dk publishes (at least) two versions of the sub-area layer:

| Layer | Contents | Row count (2026-08-07) |
|---|---|---|
| `theme_pdk_lokalplandelomraade_vedtaget` | current *adopted* state only | 66,232 |
| `theme_pdk_lokalplandelomraade_med_historik` | every version ever recorded | 110,042 (110,175 as of 2026-08-29) |

*(`vedtaget` = "adopted"; `med historik` = "with history".)*

This project queries **`_med_historik`**. Change detection needs the old and
new version of a sub-area both present at once — the `_vedtaget` layer only
ever shows you the current state, so there is nothing to diff against.

## Feature ids and layer names

Two different WFS-standard identifiers appear throughout the code and logs,
at two different levels — worth not confusing:

**`pdk:theme_pdk_lokalplandelomraade_med_historik`** — a *layer name*
(`typeName` in the WFS request). This is "which table am I querying," the WFS
analogue of a SQL table name.
- `pdk` is the namespace prefix (Plandata.dk's GeoServer workspace).
- `theme_pdk_lokalplandelomraade_med_historik` is the layer itself.

**`theme_pdk_lokalplandelomraade_med_historik.922012`** — a *feature id*:
`<layer name>.<numeric id>`, WFS's standard `typeName.fid` convention for one
row within that layer. It is GeoServer's internal row identifier — unrelated
to `lokplan_id` or `delnr`.

This is exactly the `feature_id` field on `SubAreaRecord`
(`feature["id"]` in `SubAreaRecord.from_feature()`), and why
`sub_area_versions.feature_id` is declared `UNIQUE` in the schema: it's the one
field WFS itself guarantees is a stable, unique row identifier, which is what
makes the idempotent `ON CONFLICT (feature_id)` insert in `insert_version()`
safe.

## A real request and response

Verified live, 2026-08-29 (`docs/sources.md` records the original verification
from 2026-08-07). This is exactly the request `PlandataClient.fetch_since()`
builds, minus the `CQL_FILTER`/`sortBy` used for incremental pulls:

```
GET https://geoserver.plandata.dk/geoserver/ows
    ?service=WFS
    &version=2.0.0
    &request=GetFeature
    &typeName=pdk:theme_pdk_lokalplandelomraade_med_historik
    &outputFormat=application/json
    &propertyName=komnr,kommunenavn,lokplan_id,delnr,versionsnr,status,
                  datoopdt,maxbygnhjd,maxetager,bebygpct,zonestatus,
                  anvendelsegenerel,doklink
    &count=2
```

No authentication. `propertyName` is the field allowlist — geometry is never
requested, though `"geometry": null` still appears in the response because WFS
always includes that key.

Response (GeoJSON `FeatureCollection`):

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": "theme_pdk_lokalplandelomraade_med_historik.922012",
      "geometry": null,
      "properties": {
        "lokplan_id": 1049410,
        "delnr": "IB",
        "komnr": 746,
        "kommunenavn": "Skanderborg",
        "versionsnr": 1,
        "status": "V",
        "datoopdt": "2021-12-16T10:51:22.266Z",
        "maxbygnhjd": null,
        "maxetager": null,
        "bebygpct": null,
        "zonestatus": null,
        "anvendelsegenerel": "Boligområde",
        "doklink": "https://dokument.plandata.dk/20_1049410_DRAFT_1186728711621.pdf"
      }
    }
  ],
  "totalFeatures": 110175,
  "numberMatched": 110175,
  "numberReturned": 2,
  "timeStamp": "2026-08-29T12:52:20.666Z",
  "crs": null
}
```

Reading this against the pipeline:

- `totalFeatures` / `numberMatched` count every sub-area-version row in the
  whole national layer, unfiltered — the number to sanity-check "no scope
  filter is applied" against.
- This particular row is a freshly-created plan (`versionsnr: 1`, most watched
  fields `null`) — the common "record being completed" shape that rule **R3**
  exists for. Under rule set v2, R3 files this rather than ignoring it. It is
  common in the wild but under-represented in the 34 hand-labelled cases, per
  `evals/golden/README.md`.
- The incremental fetch used by `fetch_since()` adds
  `CQL_FILTER=datoopdt>=<watermark>&sortBy=datoopdt` on top of this same
  request — that is the only difference between a full pull and a change-feed
  pull. See `sources/plandata.py`'s module docstring for why `>=` and not an
  exact-timestamp filter.

## Two versions of the same sub-area, for real

Fetching every historical row for one sub-area (`lokplan_id=3637896,
delnr='I'` — this is golden-dataset case `ZL-020`) returns exactly two rows,
live:

| | `feature_id` | `versionsnr` | `status` | `datoopdt` | `bebygpct` | `zonestatus` |
|---|---|---|---|---|---|---|
| row 1 | `...82469` | 1 | `F` (forslag / proposed) | 2017-04-28 | `null` | `Byzone` |
| row 2 | `...567209` | **1** | `V` (vedtaget / adopted) | 2019-05-10 | `70` | `null` |

Two different, permanent `feature_id`s — a `feature_id` is issued once per row
and never reused or reissued for "the same" row. But **`versionsnr` stayed `1`
on both.** The plan moving from proposed to adopted was recorded as a `status`
change (`F → V`), not a version bump. Query
`CQL_FILTER=versionsnr>1` and you do get rows back (14,195 nationally,
2026-08-29) — so `versionsnr` does increment somewhere in the register — but it
increments on *substantive redraws* of a sub-area, and a proposal being
formally adopted isn't treated as one.

**What this means for "is it a new version": `versionsnr` is not the signal
that matters here, `feature_id` is.** Neither `detect.py` nor `repo.py`
branches on `versionsnr` or `status` to decide whether to compare two rows —
they're stored as plain columns and nothing more. The actual logic, walking
`sources/plandata.py` → `db/repo.py` → `detect.py`:

1. `WatermarkStore.fetch_new()` pulls every row with `datoopdt >= watermark`,
   oldest first — this is what "fetch all records after a certain date" means
   concretely.
2. For each row, `get_or_create_sub_area()` resolves the `(lokplan_id, delnr)`
   identity row (creating it on first sighting).
3. `insert_version()` inserts the row **unconditionally**, keyed on
   `feature_id`. The only storage-layer de-duplication is `ON CONFLICT
   (feature_id)` — "have I already ingested this exact row" — never "does this
   row's `versionsnr` match what I last stored." A `status`-only change is
   still a new `feature_id`, so it's still stored as a new version.
4. `latest_version()` then looks up whichever previously-stored row has the
   latest `datoopdt` for that sub-area. **`_detect_node` in `graph.py` compares
   that row's `feature_id` to the newly fetched record's `feature_id` before
   anything else runs.** If they match, the fetched record is the same source
   row re-served by the WFS feed — most commonly, a re-run whose watermark
   hadn't advanced past it yet — and the record is routed straight to `skip`,
   the same treatment as a sub-area's first-ever sighting. Only when the
   `feature_id` genuinely differs does `diff_versions()` run and compare the
   5 watched fields between old and new.

This guard exists because of a real bug caught in production data: two
interrupted `run-once` calls re-fetched the same window before the watermark
was saved, and — before this guard existed — each re-fetched record reached
`diff_versions()`, compared a stored row to itself, found zero watched-field
differences, and fell through to `NOT_COVERED` → `assess()` → `escalate`. 28
false escalations were written this way and later deleted once the guard
landed; see `src/tilsynsagent/graph.py`'s `same_feature` state key and
`_route_after_detect`. The rule engine only ever sees a pair of *genuinely
distinct* stored rows now.

It's still the **rule engine** (R1–R4 in `rules/engine.py`), not the storage
or diff layer, that decides whether a `status`-only-looking transition
between two genuinely different `feature_id`s also moved a watched field
enough to matter — in the real ZL-020 pair it did (`bebygpct` and
`zonestatus` both changed alongside the `F → V` status flip), landing on
rule **R4** (a field losing its value, mixed with a gain, outranks the gain
on precedence → escalate) exactly as `ZL-020` is labelled under rule set v2.

## From feed to record

`SubAreaRecord.from_feature()` (`src/tilsynsagent/sources/plandata.py`) is the
one place a raw WFS property name is read. Required identity fields use
dict-index access (`p["lokplan_id"]`) and fail loudly if absent; every watched
field uses `.get()`, because a missing key and an explicit `null` both need to
collapse to Python `None` — the register genuinely omits fields as its normal
shape, not as an error case. See `docs/rules.md` for what happens once two
versions of a record are compared, and `evals/golden/cases.jsonl` (case
`ZL-020`) for a real hand-labelled example of a record moving through that
comparison.

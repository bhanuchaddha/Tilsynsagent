"""WFS client for Plandata.dk and watermark handling.

Endpoint and field names verified live against
https://geoserver.plandata.dk/geoserver/ows (docs/sources.md). Two things
learned from that verification that shape this module:

- ``datoopdt`` is millisecond-precision, and distinct sub-areas of the same
  plan can update within the same or adjacent milliseconds (observed: three
  sub-areas of one plan updated 130-350ms apart during a bulk import). A CQL
  ``AFTER`` filter on the exact last-seen timestamp would silently drop any
  sibling that shares it. The watermark therefore filters with ``>=`` on the
  last-seen timestamp and the caller is expected to de-duplicate against
  feature ids already processed for that timestamp - see
  ``WatermarkStore.seen_at_watermark``.
- ``resultType=hits`` returns ``numberMatched`` without fetching geometry or
  properties, which is how the incremental-vs-full verification (Phase 1
  verification 2) checks its count cheaply.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

WFS_URL = "https://geoserver.plandata.dk/geoserver/ows"
TYPE_NAME = "pdk:theme_pdk_lokalplandelomraade_med_historik"

WATCHED_PROPERTIES = (
    "komnr",
    "kommunenavn",
    "lokplan_id",
    "delnr",
    "versionsnr",
    "status",
    "datoopdt",
    "maxbygnhjd",
    "maxetager",
    "bebygpct",
    "zonestatus",
    "anvendelsegenerel",
    "doklink",
)

PAGE_SIZE = 1000
_EPOCH = "1900-01-01T00:00:00.000Z"


@dataclass(frozen=True)
class SubAreaRecord:
    """One sub-area at one version, as returned by the WFS feed."""

    feature_id: str
    lokplan_id: int
    delnr: str
    komnr: int
    kommunenavn: str | None
    versionsnr: int
    status: str | None
    datoopdt: str
    maxbygnhjd: float | None
    maxetager: float | None
    bebygpct: float | None
    zonestatus: str | None
    anvendelsegenerel: str | None
    doklink: str | None

    @property
    def sub_area_key(self) -> tuple[int, str]:
        """Identifies a sub-area across versions - not a single version of it."""
        return (self.lokplan_id, self.delnr)

    def to_dict(self) -> dict:
        """Plain-dict form, safe to put in msgpack-serialized graph state -
        see graph.GraphState.record."""
        return {
            "feature_id": self.feature_id,
            "lokplan_id": self.lokplan_id,
            "delnr": self.delnr,
            "komnr": self.komnr,
            "kommunenavn": self.kommunenavn,
            "versionsnr": self.versionsnr,
            "status": self.status,
            "datoopdt": self.datoopdt,
            "maxbygnhjd": self.maxbygnhjd,
            "maxetager": self.maxetager,
            "bebygpct": self.bebygpct,
            "zonestatus": self.zonestatus,
            "anvendelsegenerel": self.anvendelsegenerel,
            "doklink": self.doklink,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SubAreaRecord:
        return cls(**data)

    @classmethod
    def from_feature(cls, feature: dict) -> SubAreaRecord:
        p = feature["properties"]
        return cls(
            feature_id=feature["id"],
            lokplan_id=p["lokplan_id"],
            delnr=p["delnr"],
            komnr=p["komnr"],
            kommunenavn=p.get("kommunenavn"),
            versionsnr=p["versionsnr"],
            status=p.get("status"),
            datoopdt=p["datoopdt"],
            maxbygnhjd=p.get("maxbygnhjd"),
            maxetager=p.get("maxetager"),
            bebygpct=p.get("bebygpct"),
            zonestatus=p.get("zonestatus"),
            anvendelsegenerel=p.get("anvendelsegenerel"),
            doklink=p.get("doklink"),
        )


class PlandataClient:
    """Thin wrapper over the WFS 2.0 GetFeature endpoint used by this project."""

    def __init__(self, base_url: str = WFS_URL, timeout: float = 30.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PlandataClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def count_since(self, watermark: str) -> int:
        """Cheap count of records at or after ``watermark``, via resultType=hits."""
        resp = self._client.get(
            "",
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeName": TYPE_NAME,
                "outputFormat": "application/json",
                "resultType": "hits",
                "CQL_FILTER": f"datoopdt>={watermark}",
            },
        )
        resp.raise_for_status()
        text = resp.text
        marker = 'numberMatched="'
        start = text.find(marker) + len(marker)
        end = text.find('"', start)
        return int(text[start:end])

    def fetch_since(self, watermark: str) -> Iterator[SubAreaRecord]:
        """All records at or after ``watermark``, oldest first, paginated.

        Ordering by ``datoopdt`` ascending means the last record yielded is
        always the new high-water mark, whatever the total count.
        """
        start_index = 0
        while True:
            resp = self._client.get(
                "",
                params={
                    "service": "WFS",
                    "version": "2.0.0",
                    "request": "GetFeature",
                    "typeName": TYPE_NAME,
                    "outputFormat": "application/json",
                    "CQL_FILTER": f"datoopdt>={watermark}",
                    "sortBy": "datoopdt",
                    "propertyName": ",".join(WATCHED_PROPERTIES),
                    "count": PAGE_SIZE,
                    "startIndex": start_index,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            features = data["features"]
            if not features:
                return
            for feature in features:
                yield SubAreaRecord.from_feature(feature)
            if len(features) < PAGE_SIZE:
                return
            start_index += PAGE_SIZE


@dataclass
class WatermarkStore:
    """Tracks the last ``datoopdt`` seen and the feature ids seen at exactly
    that timestamp, so a re-run with ``>=`` does not re-file work already
    done for records sharing the boundary instant.
    """

    # None when the store is only used in-memory for one fetch_new() pass and
    # persisted elsewhere (runner.py persists to Postgres via repo.py instead).
    path: Path | None = None
    last_datoopdt: str = _EPOCH
    seen_at_watermark: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> WatermarkStore:
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text())
        return cls(
            path=path,
            last_datoopdt=data.get("last_datoopdt", _EPOCH),
            seen_at_watermark=set(data.get("seen_at_watermark", [])),
        )

    def save(self) -> None:
        assert self.path is not None, "WatermarkStore.save() requires a path"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "last_datoopdt": self.last_datoopdt,
                    "seen_at_watermark": sorted(self.seen_at_watermark),
                    "saved_at": datetime.now(UTC).isoformat(),
                },
                indent=2,
            )
        )

    def is_new(self, record: SubAreaRecord) -> bool:
        if record.datoopdt > self.last_datoopdt:
            return True
        if record.datoopdt == self.last_datoopdt:
            return record.feature_id not in self.seen_at_watermark
        return False

    def advance(self, record: SubAreaRecord) -> None:
        if record.datoopdt > self.last_datoopdt:
            self.last_datoopdt = record.datoopdt
            self.seen_at_watermark = {record.feature_id}
        elif record.datoopdt == self.last_datoopdt:
            self.seen_at_watermark.add(record.feature_id)

    def fetch_new(self, client: PlandataClient) -> Iterator[SubAreaRecord]:
        """New records since this watermark, filtering out any that share
        the boundary timestamp and were already seen, then advancing the
        watermark as records are yielded."""
        for record in client.fetch_since(self.last_datoopdt):
            if not self.is_new(record):
                continue
            self.advance(record)
            yield record

"""Fetching a plan document, with a size ceiling and a content-addressed
cache.

The ceiling is enforced on the stream, not on Content-Length, since a header
is a claim by the server and the bytes are the fact: this reads in chunks
and aborts the moment the accumulated size crosses the limit.

The cache key is sha256(doklink), with no invalidation logic. The register's
doklink embeds the plan id and a millisecond timestamp
(``20_9719017_1606817668820.pdf``), so a revised document arrives under a
different URL and therefore a different key.

Nothing here raises. Every failure - HTTP status, timeout, oversize,
unreadable cache - comes back as ``FetchResult(error=...)``. The grounding
node treats any error as "cannot ground", which routes to escalation.

``file://`` URLs are accepted so demo/documents.py can put synthetic PDFs on
the same code path as real ones.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# The stream ceiling. Above this the fetch aborts and the record escalates.
# The probe's largest real document was 156 MB; 40 MB covers 30 of 31
# documents in the corpus and keeps a single run's memory and Groq token
# budget bounded.
MAX_DOCUMENT_BYTES = 40 * 1024 * 1024

# Read granularity for the streaming ceiling check.
_CHUNK_BYTES = 256 * 1024

DEFAULT_TIMEOUT_SECONDS = 60.0


def cache_dir() -> Path:
    """Where fetched documents live. Overridable by env so tests and the
    demo reset can point at a directory they own."""
    configured = os.environ.get("TILSYNSAGENT_DOCUMENT_CACHE")
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "tilsynsagent" / "documents"


def cache_path_for(doklink: str, *, directory: Path | None = None) -> Path:
    """sha256 of the URL - see the module docstring on why no invalidation."""
    directory = directory or cache_dir()
    return directory / f"{hashlib.sha256(doklink.encode()).hexdigest()}.pdf"


@dataclass(frozen=True)
class FetchResult:
    """What a fetch produced, or why it produced nothing.

    ``ok`` is exactly ``error is None``. A caller must check it; ``content``
    is None on every failure path.
    """

    doklink: str
    content: bytes | None = None
    error: str | None = None
    cache_hit: bool = False
    fetch_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.content is not None

    @property
    def size_bytes(self) -> int:
        return len(self.content) if self.content else 0


def _read_file_url(doklink: str) -> bytes:
    path = Path(unquote(urlparse(doklink).path))
    return path.read_bytes()


def fetch_document(
    doklink: str,
    *,
    directory: Path | None = None,
    max_bytes: int = MAX_DOCUMENT_BYTES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    client=None,
) -> FetchResult:
    """Fetches one plan document, from cache when it is already there.

    Never raises - see the module docstring. The returned FetchResult carries
    ``cache_hit`` and ``fetch_ms`` because the cost of grounding is part of
    what this phase has to be able to show: a 30 MB document per record is a
    real operating cost, and a cache hit rate is the number that makes it
    tolerable.
    """
    started = time.monotonic()
    path = cache_path_for(doklink, directory=directory)

    if path.exists():
        try:
            content = path.read_bytes()
            return FetchResult(
                doklink,
                content=content,
                cache_hit=True,
                fetch_ms=(time.monotonic() - started) * 1000,
            )
        except OSError as exc:
            logger.warning("cached document %s unreadable (%s); refetching", path, exc)

    try:
        if doklink.startswith("file://"):
            content = _read_file_url(doklink)
            if len(content) > max_bytes:
                return FetchResult(
                    doklink,
                    error=f"document is {len(content)} bytes, over the {max_bytes} ceiling",
                )
        else:
            content = _stream_download(doklink, max_bytes=max_bytes, timeout=timeout, client=client)
    except _OversizeDocument as exc:
        return FetchResult(doklink, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        return FetchResult(doklink, error=f"{type(exc).__name__}: {exc}")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written via a temp file in the same directory then renamed, so a
        # crash mid-write cannot leave a truncated PDF that later reads as a
        # cache hit - a corrupt cached document would be indistinguishable
        # from a genuinely unparseable one.
        tmp = path.with_suffix(".part")
        tmp.write_bytes(content)
        tmp.replace(path)
    except OSError as exc:
        logger.warning("document %s could not be cached (%s); continuing", doklink, exc)

    return FetchResult(
        doklink, content=content, cache_hit=False, fetch_ms=(time.monotonic() - started) * 1000
    )


class _OversizeDocument(Exception):
    """Internal: the stream crossed the ceiling. Converted to a FetchResult
    error by fetch_document, never allowed out of this module."""


def _stream_download(doklink: str, *, max_bytes: int, timeout: float, client=None) -> bytes:
    """Downloads in chunks, aborting past the ceiling.

    The check is on bytes actually received. A server that under-reports
    Content-Length, or omits it entirely, cannot talk its way past this.
    """
    import httpx

    owns_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        with client.stream("GET", doklink) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes(_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise _OversizeDocument(
                        f"document exceeded the {max_bytes} byte ceiling while streaming "
                        f"(at least {total} bytes); refusing to read further"
                    )
                chunks.append(chunk)
        return b"".join(chunks)
    finally:
        if owns_client:
            client.close()


def clear_cache(*, directory: Path | None = None, prefix_urls: list[str] | None = None) -> int:
    """Removes cached documents. Returns how many files were deleted.

    With ``prefix_urls`` only those exact documents are removed (demo reset,
    which must not throw away the real corpus a run has paid to fetch);
    without it, the whole directory's PDFs go.
    """
    directory = directory or cache_dir()
    if not directory.exists():
        return 0
    if prefix_urls is not None:
        targets = [cache_path_for(u, directory=directory) for u in prefix_urls]
    else:
        targets = list(directory.glob("*.pdf"))
    removed = 0
    for path in targets:
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("could not remove cached document %s: %s", path, exc)
    return removed

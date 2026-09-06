"""The document layer: fetch, ceiling, cache, extract.

The properties tested here are the ones that decide whether grounding is safe
rather than whether it works: the size ceiling is enforced on bytes rather
than on a header, and nothing in the fetch path raises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tilsynsagent.documents.cache import (
    FetchResult,
    cache_path_for,
    clear_cache,
    fetch_document,
)
from tilsynsagent.documents.extract import MIN_USEFUL_CHARS, extract_text


class _FakeStream:
    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_bytes(self, _size):
        yield from self._chunks


class _FakeClient:
    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self._status = status
        self.closed = False

    def stream(self, _method, _url):
        return _FakeStream(self._chunks, self._status)

    def close(self):
        self.closed = True


def test_cache_key_is_the_url_so_a_revised_document_gets_a_new_key(tmp_path):
    """The register embeds a timestamp in the doklink, so a revised document
    arrives under a different URL. That is what removes the need for any
    invalidation logic at all - the property worth pinning."""
    a = cache_path_for("https://x/20_1_1000.pdf", directory=tmp_path)
    b = cache_path_for("https://x/20_1_2000.pdf", directory=tmp_path)
    assert a != b
    assert a == cache_path_for("https://x/20_1_1000.pdf", directory=tmp_path)


def test_ceiling_is_enforced_on_the_stream_not_on_content_length(tmp_path):
    """A server that under-reports or omits Content-Length must not get past
    the ceiling. This fake never declares a length at all."""
    chunks = [b"x" * 1024] * 40
    result = fetch_document(
        "https://x/big.pdf",
        directory=tmp_path,
        max_bytes=10 * 1024,
        client=_FakeClient(chunks),
    )
    assert not result.ok
    assert "ceiling" in result.error


def test_oversize_document_is_not_cached(tmp_path):
    fetch_document(
        "https://x/big.pdf",
        directory=tmp_path,
        max_bytes=1024,
        client=_FakeClient([b"y" * 4096]),
    )
    assert not cache_path_for("https://x/big.pdf", directory=tmp_path).exists()


def test_http_error_returns_a_result_and_never_raises(tmp_path):
    result = fetch_document(
        "https://x/missing.pdf", directory=tmp_path, client=_FakeClient([], status=404)
    )
    assert isinstance(result, FetchResult)
    assert not result.ok
    assert result.error


def test_a_second_fetch_is_a_cache_hit(tmp_path):
    client = _FakeClient([b"%PDF-1.4 hello"])
    first = fetch_document("https://x/a.pdf", directory=tmp_path, client=client)
    assert first.ok and not first.cache_hit
    second = fetch_document("https://x/a.pdf", directory=tmp_path, client=_FakeClient([]))
    assert second.ok and second.cache_hit
    assert second.content == first.content


def test_file_urls_are_accepted_so_demo_documents_use_the_real_path(tmp_path):
    source = tmp_path / "plan.pdf"
    source.write_bytes(b"%PDF-1.4 demo")
    result = fetch_document(source.as_uri(), directory=tmp_path / "cache")
    assert result.ok
    assert result.content == b"%PDF-1.4 demo"


def test_clear_cache_removes_only_the_named_documents(tmp_path):
    fetch_document("https://x/a.pdf", directory=tmp_path, client=_FakeClient([b"a"]))
    fetch_document("https://x/b.pdf", directory=tmp_path, client=_FakeClient([b"b"]))
    removed = clear_cache(directory=tmp_path, prefix_urls=["https://x/a.pdf"])
    assert removed == 1
    assert not cache_path_for("https://x/a.pdf", directory=tmp_path).exists()
    assert cache_path_for("https://x/b.pdf", directory=tmp_path).exists()


def test_unreadable_bytes_extract_to_an_error_not_an_exception():
    result = extract_text(b"this is not a pdf")
    assert not result.ok
    assert result.error


def test_a_scan_with_no_text_layer_is_reported_rather_than_split():
    """The corpus probe found two real documents like this. Grounding on
    OCR-less page furniture would be grounding on noise, so extract refuses
    it outright rather than handing a splitter something meaningless."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    import io

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(100, 700, "side 1")
    c.save()
    result = extract_text(buf.getvalue())
    assert not result.ok
    assert "text layer" in result.error
    assert result.page_count == 1


def test_min_useful_chars_is_above_the_observed_scan_sizes():
    """312 and 1,945 chars were the two scans in the real corpus - the
    threshold must sit above both or they would split into noise."""
    assert MIN_USEFUL_CHARS > 1945

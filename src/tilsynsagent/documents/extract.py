"""PDF bytes to text.

**``cryptography`` is a hard requirement, not an optional extra.** Real
plandata.dk documents are AES-encrypted with an *empty* password - they are
not access-restricted, they are just encrypted-at-rest by whatever produced
them. ``pypdf`` raises ``DependencyError`` on those without ``cryptography``
installed, so an environment missing it would silently ground nothing and
escalate everything, which looks like a model problem and is a packaging
problem. Verified live: one of the 31 corpus documents is encrypted this way.

Like cache.py, this never raises for a document-side problem. A PDF with no
text layer (a scan) is a real and common case - the corpus probe found two -
and the correct response is to abstain and escalate, not to crash.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Below this, treat the extraction as having produced nothing usable. The
# probe found scanned documents yielding 312 and 1,945 characters over 17 and
# 43 pages respectively - page furniture from the OCR-less text layer, not
# clause text. Grounding on that would be grounding on noise.
MIN_USEFUL_CHARS = 2000


@dataclass(frozen=True)
class ExtractResult:
    text: str = ""
    page_count: int = 0
    encrypted: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text)


def extract_text(content: bytes) -> ExtractResult:
    """Extracts a document's full text, page by page.

    An encrypted-with-empty-password document is decrypted transparently
    (see the module docstring). A document that will not decrypt with an
    empty password is genuinely access-restricted and is reported as an
    error - this project reads public sources only, and guessing a password
    is not something it does.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - packaging error, not runtime
        return ExtractResult(error=f"pypdf is not installed: {exc}")

    try:
        reader = PdfReader(io.BytesIO(content))
        encrypted = bool(reader.is_encrypted)
        if encrypted:
            # Returns 0 when the empty password is rejected. pypdf needs
            # `cryptography` to attempt this at all for AES documents and
            # raises DependencyError without it - caught below and reported
            # as an error rather than silently abstaining.
            if reader.decrypt("") == 0:
                return ExtractResult(
                    encrypted=True,
                    error="document is password-protected; this project reads public sources only",
                )
        pages = reader.pages
        text = "\n".join((page.extract_text() or "") for page in pages)
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        return ExtractResult(error=f"{type(exc).__name__}: {exc}")

    if len(text.strip()) < MIN_USEFUL_CHARS:
        return ExtractResult(
            text="",
            page_count=len(pages),
            encrypted=encrypted,
            error=(
                f"document has {len(text.strip())} characters of extractable text over "
                f"{len(pages)} pages - most likely a scan with no text layer"
            ),
        )

    return ExtractResult(text=text, page_count=len(pages), encrypted=encrypted)

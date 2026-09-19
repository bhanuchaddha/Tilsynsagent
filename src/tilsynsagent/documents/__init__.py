"""Reading the source plan document, so a decision can be grounded in a
clause rather than inferred from five register fields.

Widens the *not-covered* path only. A register record that is internally
inconsistent (R1) or that has lost a value (R4) still escalates: the
document cannot say whether the register transposed two numbers, and it
cannot explain why a value was dropped.

Two modules, in the order the data moves:

- ``cache.py``   — fetch the PDF, with a streaming size ceiling and a
  content-addressed disk cache. Never raises.
- ``extract.py`` — PDF bytes to text. ``cryptography`` is required, not
  optional: real plandata.dk documents are AES-encrypted with an empty
  password.

The model is given the whole document and decides from all of it. A
decision must name its source, and a quoted clause is checked against the
full document text.
"""

from tilsynsagent.documents.cache import (
    MAX_DOCUMENT_BYTES,
    FetchResult,
    cache_path_for,
    clear_cache,
    fetch_document,
)
from tilsynsagent.documents.extract import ExtractResult, extract_text

__all__ = [
    "MAX_DOCUMENT_BYTES",
    "ExtractResult",
    "FetchResult",
    "cache_path_for",
    "clear_cache",
    "extract_text",
    "fetch_document",
]

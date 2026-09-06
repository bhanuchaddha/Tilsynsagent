"""Reading the source plan document, so a decision can be grounded in a
clause rather than inferred from five register fields.

**Why this exists at all.** Until this package, every decision the agent made
was either a deterministic rule or an escalation to a person. Nothing in that
population can silently get worse: rules do not drift, and a human is in the
path of everything else. Grounding produces the first decisions the agent
makes with no human in the path — which is the only kind that can degrade
quietly, and therefore the precondition for a production feedback loop that
has anything to detect.

**What it does not do.** It widens the *not-covered* path only. A register
record that is internally inconsistent (R1) or that has lost a value (R4)
still escalates: the document cannot say whether the register transposed two
numbers, and it cannot explain why a value was dropped. Grounding is not a
rescue for those and must never be used as one.

Three modules, in the order the data moves:

- ``cache.py``   — fetch the PDF, with a streaming size ceiling and a
  content-addressed disk cache. Never raises.
- ``extract.py`` — PDF bytes to text. ``cryptography`` is required, not
  optional: real plandata.dk documents are AES-encrypted with an empty
  password.
- ``clauses.py`` — text to numbered clauses, and clause retrieval for the
  fields that actually changed.
"""

from tilsynsagent.documents.cache import (
    MAX_DOCUMENT_BYTES,
    FetchResult,
    cache_path_for,
    clear_cache,
    fetch_document,
)
from tilsynsagent.documents.clauses import (
    CLAUSE_RE,
    FIELD_KEYWORDS,
    MAX_RETRIEVED_CHARS,
    Clause,
    retrieve_for_fields,
    split_clauses,
)
from tilsynsagent.documents.extract import ExtractResult, extract_text

__all__ = [
    "CLAUSE_RE",
    "FIELD_KEYWORDS",
    "MAX_DOCUMENT_BYTES",
    "MAX_RETRIEVED_CHARS",
    "Clause",
    "ExtractResult",
    "FetchResult",
    "cache_path_for",
    "clear_cache",
    "extract_text",
    "fetch_document",
    "retrieve_for_fields",
    "split_clauses",
]

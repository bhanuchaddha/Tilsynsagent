"""Factory for the Groq client both assess.py and summarise.py use.

Verified live (2026-08-22): Langfuse v4's OpenAI-compatible drop-in
(``langfuse.openai``) wraps the ``openai`` package specifically - it imports
``openai`` internally and raises ModuleNotFoundError without it installed.
Groq's SDK is OpenAI-shaped but is not the ``openai`` package, so the
drop-in does not apply here. Tracing instead uses Langfuse's own
``@observe()`` decorator plus ``update_current_generation(usage_details=...)``
at the two call sites (obs/langfuse_setup.py's ``record_generation``) - this
is Langfuse's supported low-level API for non-OpenAI clients, not a
hand-rolled substitute for it.

This factory's only job is: return a plain groq.Groq(), unconditionally. No
Langfuse involvement happens here - that keeps assess()/summarise() free to
call groq_client() whether or not Langfuse keys are present.
"""

from __future__ import annotations

import os

from groq import Groq


def groq_client() -> Groq:
    return Groq(api_key=os.environ["GROQ_API_KEY"])

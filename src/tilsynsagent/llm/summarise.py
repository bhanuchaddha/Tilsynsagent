"""The summarisation step: turns a filed decision into readable prose.

The decision is already made by rules.engine.apply_rules by the time this
runs - this step does not decide anything, it writes. Given the rule that
fired, the changed fields, and the source document, it produces the summary
text stored in filings.summary.
"""

from __future__ import annotations

import os
import time

from groq import Groq
from pydantic import BaseModel, Field

from tilsynsagent import obs
from tilsynsagent.llm.client import groq_client
from tilsynsagent.llm.prompts import SUMMARISE_PROMPT_NAME, get_prompt, record_last_prompt
from tilsynsagent.llm.schema import response_format
from tilsynsagent.llm.usage import Usage

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# The system prompt now lives in the Langfuse prompt registry - see
# llm/prompts.py. The pinned fallback there is byte-identical to what this
# constant held before the registry existed.


class Summary(BaseModel):
    summary: str = Field(
        description="One or two sentences: what changed and why it was filed, in plain language."
    )


def build_prompt(
    *, sub_area_description: str, changed_fields: dict, rule: str, rule_reason: str, doklink: str
) -> str:
    return f"""\
Sub-area: {sub_area_description}

Changed fields (before -> after):
{changed_fields}

Filed under rule {rule}: {rule_reason}

Source document: {doklink}

Write the record summary."""


def summarise(
    *,
    sub_area_description: str,
    changed_fields: dict,
    rule: str,
    rule_reason: str,
    doklink: str,
    client: Groq | None = None,
    model: str = DEFAULT_MODEL,
) -> str:
    client = client or groq_client()
    system = get_prompt(SUMMARISE_PROMPT_NAME)
    record_last_prompt(system)
    prompt = build_prompt(
        sub_area_description=sub_area_description,
        changed_fields=changed_fields,
        rule=rule,
        rule_reason=rule_reason,
        doklink=doklink,
    )
    with obs.record_generation("summarise", model=model) as gen:
        start = time.monotonic()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system.text},
                {"role": "user", "content": prompt},
            ],
            response_format=response_format(Summary, "summary"),
            temperature=0,
        )
        latency_ms = (time.monotonic() - start) * 1000
        usage = Usage.from_groq(resp, model=model, latency_ms=latency_ms)
        usage.record()
        if gen is not None:
            gen.update(
                input=prompt,
                output=resp.choices[0].message.content,
                metadata={
                    "prompt_name": system.name,
                    "prompt_version": system.version,
                    "prompt_source": system.source,
                },
                usage_details={
                    "input": usage.prompt_tokens,
                    "output": usage.completion_tokens,
                    "total": usage.total_tokens,
                },
            )
    return Summary.model_validate_json(resp.choices[0].message.content).summary

"""The assessment step: a Groq call invoked only when rules.engine.apply_rules
returns NOT_COVERED.

The model never decides what is compliant; it decides what is worth a
person's attention, and explains why. It is given the change, the rule set's
stated boundary (docs/rules.md, "What this rule set does not cover"), and the
source document link, and returns what is unclear, what a person must decide,
and a citation - never a file/escalate/ignore label. That label is not this
step's job: apply_rules already returned NOT_COVERED, so the only outcome
downstream is escalation. Making the model choose a label it cannot act on
would just be inviting the "the model was wrong" disagreement class the
project's decision architecture exists to avoid.
"""

from __future__ import annotations

import os
import time

from groq import Groq
from pydantic import BaseModel, Field

from tilsynsagent import obs
from tilsynsagent.llm.client import groq_client
from tilsynsagent.llm.prompts import ASSESS_PROMPT_NAME, get_prompt
from tilsynsagent.llm.schema import response_format
from tilsynsagent.llm.prompts import record_last_prompt
from tilsynsagent.llm.usage import Usage
from tilsynsagent.rules.loader import load_not_covered_section

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# The system prompt now lives in the Langfuse prompt registry - see
# llm/prompts.py for why, and for the pinned fallback used when Langfuse is
# unconfigured or unreachable. The fallback text is byte-identical to what
# this constant held before the registry existed.


class Assessment(BaseModel):
    what_is_unclear: str = Field(
        description="What specifically the register cannot settle about this change."
    )
    what_a_person_must_decide: str = Field(
        description="The concrete question a person must resolve, in one sentence."
    )
    citation: str = Field(description="URL of the source document (doklink).")


def build_prompt(*, sub_area_description: str, changed_fields: dict, doklink: str) -> str:
    not_covered = load_not_covered_section()
    return f"""\
Sub-area: {sub_area_description}

Changed fields (before -> after):
{changed_fields}

Source document: {doklink}

The rule set's stated boundary - what it does not cover, and why the agent
must escalate rather than guess here:

{not_covered}

State what is unclear about this specific change, what a person must decide,
and cite the source document."""


def assess(
    *,
    sub_area_description: str,
    changed_fields: dict,
    doklink: str,
    client: Groq | None = None,
    model: str = DEFAULT_MODEL,
) -> Assessment:
    """Calls Groq for a case the rule engine returned NOT_COVERED on."""
    client = client or groq_client()
    system = get_prompt(ASSESS_PROMPT_NAME)
    record_last_prompt(system)
    prompt = build_prompt(
        sub_area_description=sub_area_description,
        changed_fields=changed_fields,
        doklink=doklink,
    )
    with obs.record_generation("assess", model=model) as gen:
        start = time.monotonic()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system.text},
                {"role": "user", "content": prompt},
            ],
            response_format=response_format(Assessment, "assessment"),
            temperature=0,
        )
        latency_ms = (time.monotonic() - start) * 1000
        usage = Usage.from_groq(resp, model=model, latency_ms=latency_ms)
        usage.record()
        if gen is not None:
            gen.update(
                input=prompt,
                output=resp.choices[0].message.content,
                # The prompt version that produced this decision, on the trace
                # itself - the one rule applied to the prompt, not just the data.
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
    return Assessment.model_validate_json(resp.choices[0].message.content)

"""The summarisation step: turns a filed decision into readable prose.

The decision is already made by rules.engine.apply_rules by the time this
runs - this step does not decide anything, it writes. Given the rule that
fired, the changed fields, and the source document, it produces the summary
text stored in filings.summary.
"""

from __future__ import annotations

import os

from groq import Groq
from pydantic import BaseModel, Field

from tilsynsagent.llm.schema import response_format

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

SYSTEM_PROMPT = """\
You write a short, factual record of a change that has already been filed as \
significant. The decision is made; your job is only to state clearly what \
changed and why it was filed, citing the rule and the source document. Do not \
add judgement, speculation, or recommendations - the rule engine already \
decided this matters. State facts from the record given to you; do not invent \
detail it does not contain."""


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
    client = client or Groq(api_key=os.environ["GROQ_API_KEY"])
    prompt = build_prompt(
        sub_area_description=sub_area_description,
        changed_fields=changed_fields,
        rule=rule,
        rule_reason=rule_reason,
        doklink=doklink,
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format=response_format(Summary, "summary"),
        temperature=0,
    )
    return Summary.model_validate_json(resp.choices[0].message.content).summary

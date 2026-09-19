"""The grounding step: reading the source plan document, and the whole
register record, to settle a case the rule set could not.

This is the first decision the agent makes with no person in the path.
assess() explains an uncovered case to a human; summarise() writes up a
decision code already made. This function's output is a decision - file or
ignore, autonomously.

Three properties matter here:

1. **Abstention is a first-class answer.** ``can_decide=False`` routes the
   record to a person, exactly as before grounding existed.
2. **The decision is checkable by code, not by trust.** A decided grounding
   names its source: a clause quoted verbatim out of the document, or a
   register field named with its before -> after values. obs/grounded.py
   checks both kinds mechanically on every grounded run.
3. **It never rescues R1 or R4.** Those escalate before this node is
   reached, since the document cannot say whether the register transposed
   two numbers (R1) or why a value was dropped (R4).

The model is given the whole document and the whole record, both versions,
and must name its source. The five watched fields keep their special status
in the rule engine, where R1-R4 are written against them; they have no
special status here, since this node only runs where those rules declined.

Structurally this mirrors assess.py - same ``record_generation``, same
``record_last_prompt``, same strict ``response_format``.
"""

from __future__ import annotations

import os
import time

from groq import Groq
from pydantic import BaseModel, Field

from tilsynsagent import obs
from tilsynsagent.llm.client import groq_client
from tilsynsagent.llm.prompts import GROUND_PROMPT_NAME, get_prompt, record_last_prompt
from tilsynsagent.llm.schema import response_format
from tilsynsagent.llm.usage import Usage

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# The outcomes a grounded decision may reach. 'escalate' is deliberately not
# here: a grounded escalation is expressed as can_decide=False, so there is
# exactly one way for the model to hand a case to a person and it cannot be
# confused with a decision it made.
GROUNDED_OUTCOMES = ("file", "ignore")

# The two kinds of source a decision may rest on. A decision cites exactly
# one: if a clause settles it, quote the clause; otherwise name the field.
# One citation rather than both keeps is_decided a single readable condition
# and keeps the audit question singular - "what did this rest on?" has one
# answer, and the answer is checkable either way.
CITATION_KINDS = ("clause", "field")


class Grounding(BaseModel):
    """What the model concluded, and what it rested that conclusion on.

    Every field is required by the strict schema, including on an abstention.
    Unused fields are empty strings, never null: Groq strict mode does not
    accept a nullable field here, and an empty string is unambiguously
    "not this" to every downstream check.
    """

    can_decide: bool = Field(
        description=(
            "True only if the document or a register field settles the question. "
            "False is the correct answer when nothing does."
        )
    )
    outcome: str = Field(
        description="'file' or 'ignore' when can_decide is true; empty string otherwise."
    )
    citation_kind: str = Field(
        description=(
            "'clause' if a clause in the document settles it, 'field' if a register "
            "field does. Empty when abstaining. Prefer 'clause' when both apply."
        )
    )
    clause_id: str = Field(
        description=(
            "The number of the clause relied on, e.g. '6.3'. "
            "Required when citation_kind is 'clause', empty otherwise."
        )
    )
    clause_quote: str = Field(
        description=(
            "The sentence from that clause, copied character for character. "
            "Required when citation_kind is 'clause', empty otherwise."
        )
    )
    field_name: str = Field(
        description=(
            "The register field relied on, e.g. 'status'. "
            "Required when citation_kind is 'field', empty otherwise."
        )
    )
    field_before: str = Field(
        description="That field's value in the earlier version. Empty otherwise."
    )
    field_after: str = Field(
        description="That field's value in the later version. Empty otherwise."
    )
    findings: str = Field(
        description=(
            "ONLY when abstaining: the clauses and fields that bear on this "
            "change, quoted with their ids, and then why they do not settle it. "
            "MUST be an empty string whenever can_decide is true."
        )
    )
    reasoning: str = Field(
        description=(
            "One or two sentences: how the cited source settles the question, or, "
            "when abstaining, what remains undecided after the findings above."
        )
    )
    citation: str = Field(description="URL of the source document (doklink).")

    @property
    def is_decided(self) -> bool:
        """A decision this system will act on autonomously.

        Stricter than ``can_decide`` alone on purpose: a model that sets
        can_decide=True but returns no usable citation has not produced
        something checkable, and an uncheckable autonomous decision is
        exactly what CLAUDE.md's one rule forbids. Treated as an abstention,
        which escalates.

        Either kind of citation is accepted, and that is load-bearing rather
        than permissive. Requiring a clause would discard a correct
        field-based decision as uncheckable - ``status: F -> V`` is a fully
        traceable source, and refusing it is what kept demo case 2 from
        working. A named field with at least one side of its transition is
        checkable against the record by code, which is the bar.
        """
        if not (self.can_decide and self.outcome in GROUNDED_OUTCOMES):
            return False
        if self.citation_kind == "clause":
            return bool(self.clause_id.strip() and self.clause_quote.strip())
        if self.citation_kind == "field":
            return bool(
                self.field_name.strip()
                and (self.field_before.strip() or self.field_after.strip())
            )
        return False

    @property
    def source_description(self) -> str:
        """One line naming what the decision rested on, for a person reading
        the filing or the queue. Never empty on a decided grounding."""
        if self.citation_kind == "clause":
            return f"clause {self.clause_id}"
        if self.citation_kind == "field":
            return f"{self.field_name}: {self.field_before or '(none)'} -> {self.field_after or '(none)'}"
        return ""


def render_record(record: dict) -> str:
    """One register version as ``field: value`` lines, every field included.

    Sorted so the before and after blocks line up visually for the model and
    for anyone reading the prompt in a trace. ``None`` renders as ``(none)``
    rather than being dropped, because a field that has no value is a fact
    about the record and dropping it would make an absence look like an
    omission by the harness.
    """
    if not record:
        return "(no version recorded)"
    return "\n".join(
        f"  {key}: {'(none)' if record[key] is None else record[key]}"
        for key in sorted(record)
    )


def build_prompt(
    *,
    sub_area_description: str,
    before: dict,
    after: dict,
    watched_changed_fields: dict,
    document_text: str,
    doklink: str,
) -> str:
    """The user message: the whole record, both versions, and the whole document.

    ``watched_changed_fields`` is passed as context, not as the question. It
    is what the rule engine already examined, and on the not-covered route it
    is empty by definition - the model needs to know the rules looked at
    those five and declined, so the change is somewhere else.
    """
    watched = (
        ", ".join(sorted(watched_changed_fields))
        if watched_changed_fields
        else "none - all five are identical in both versions"
    )
    return f"""\
Sub-area: {sub_area_description}

The five fields the written rule set watches (maxbygnhjd, maxetager,
bebygpct, zonestatus, anvendelsegenerel) were already examined by the rules,
which did not settle this case.

Watched fields that differ between the two versions below: {watched}

The register record BEFORE this change, every field:
{render_record(before)}

The register record AFTER this change, every field:
{render_record(after)}

Source document: {doklink}

Full text of that document:

{document_text}

Does a reader of this land now see something they could not see before?
Decide from the document text or from a register field above, and name which
one you used. If nothing settles it, set can_decide to false and list what
you did find."""


def ground(
    *,
    sub_area_description: str,
    before: dict,
    after: dict,
    watched_changed_fields: dict,
    document_text: str,
    doklink: str,
    client: Groq | None = None,
    model: str = DEFAULT_MODEL,
) -> Grounding:
    """Asks the model to settle an uncovered case from the document and record.

    Called only when a document was fetched and text extracted; a document
    that cannot be read is an abstention decided in code and never costs a
    model call (see graph.py's _ground_node).
    """
    client = client or groq_client()
    system = get_prompt(GROUND_PROMPT_NAME)
    record_last_prompt(system)
    prompt = build_prompt(
        sub_area_description=sub_area_description,
        before=before,
        after=after,
        watched_changed_fields=watched_changed_fields,
        document_text=document_text,
        doklink=doklink,
    )
    with obs.record_generation("ground", model=model) as gen:
        start = time.monotonic()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system.text},
                {"role": "user", "content": prompt},
            ],
            response_format=response_format(Grounding, "grounding"),
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
                    "document_chars": len(document_text),
                },
                usage_details={
                    "input": usage.prompt_tokens,
                    "output": usage.completion_tokens,
                    "total": usage.total_tokens,
                },
            )
    return Grounding.model_validate_json(resp.choices[0].message.content)

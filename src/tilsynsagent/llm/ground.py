"""The grounding step: reading clauses from the source plan document to
settle a case the register alone could not.

**This is the first decision the agent makes with no person in the path.**
assess() explains an uncovered case to a human; summarise() writes up a
decision code already made. This function's output *is* a decision - file or
ignore, autonomously. That is deliberate and it is the point of the phase: a
population of decisions nobody reviews is the only population that can
degrade quietly, and a feedback loop needs something that can degrade.

Because of that, three properties matter more here than anywhere else in the
codebase:

1. **Abstention is a first-class answer.** ``can_decide=False`` routes the
   record to a person, exactly as before grounding existed. The prompt says
   so explicitly. A grounding step that always decides is not grounding, it
   is guessing with extra steps.
2. **The decision is checkable by code, not by trust.** The returned
   ``clause_id`` must exist in the clauses the model was handed, and
   ``clause_quote`` must appear verbatim in that clause's text.
   obs/grounded.py checks both mechanically on every grounded run.
3. **It never rescues R1 or R4.** Those escalate before this node is
   reached. The document cannot say whether the register transposed two
   numbers (R1), nor why a value was dropped (R4). Grounding widens the
   not-covered path and nothing else.

Structurally this mirrors assess.py exactly - same ``record_generation``,
same ``record_last_prompt``, same strict ``response_format`` - so a reader
who understands one understands both, and the prompt-version-on-every-trace
property holds identically.
"""

from __future__ import annotations

import os
import time

from groq import Groq
from pydantic import BaseModel, Field

from tilsynsagent import obs
from tilsynsagent.documents.clauses import Clause, render_clauses
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


class Grounding(BaseModel):
    """What the model concluded from the clauses it was given.

    Every field is required by the strict schema, including on an abstention
    - the model must still say *why* it could not decide, because that
    sentence is what a person reads when the record reaches the queue.
    ``clause_id`` and ``clause_quote`` are empty strings on an abstention,
    never null: Groq strict mode does not accept a nullable field here, and
    an empty string is unambiguously "no clause" to every downstream check.
    """

    can_decide: bool = Field(
        description=(
            "True only if a clause you were given governs the changed field. "
            "False is the correct answer when it does not."
        )
    )
    outcome: str = Field(
        description="'file' or 'ignore' when can_decide is true; empty string otherwise."
    )
    clause_id: str = Field(
        description="The number of the clause relied on, e.g. '6.3'. Empty when abstaining."
    )
    clause_quote: str = Field(
        description=(
            "The sentence from that clause, copied character for character. "
            "Empty when abstaining."
        )
    )
    reasoning: str = Field(
        description=(
            "One or two sentences: how the quoted clause settles the question, or, "
            "when abstaining, what the clauses given do not cover."
        )
    )
    citation: str = Field(description="URL of the source document (doklink).")

    @property
    def is_decided(self) -> bool:
        """A decision this system will act on autonomously.

        Stricter than ``can_decide`` alone on purpose: a model that sets
        can_decide=True but returns no clause id, no quote, or an outcome
        outside GROUNDED_OUTCOMES has not produced something checkable, and
        an uncheckable autonomous decision is exactly what CLAUDE.md's one
        rule forbids. Treated as an abstention, which escalates.
        """
        return bool(
            self.can_decide
            and self.outcome in GROUNDED_OUTCOMES
            and self.clause_id.strip()
            and self.clause_quote.strip()
        )


def build_prompt(
    *,
    sub_area_description: str,
    changed_fields: dict,
    clauses: list[Clause],
    doklink: str,
) -> str:
    """The user message: the change, and the clauses retrieved for it.

    The whole document is never sent. Four clauses (~4k chars) is what
    retrieval selected for the fields that actually changed - see
    documents/clauses.py. Sending the full 27k-token document would cost
    ~25x more per record and would let the model answer from a part of the
    plan nobody asked about, which is unauditable by construction.
    """
    clause_text = render_clauses(clauses) or "(no clauses were retrieved for these fields)"
    clause_ids = ", ".join(c.clause_id for c in clauses) or "none"
    return f"""\
Sub-area: {sub_area_description}

Changed fields in the register (before -> after):
{changed_fields}

Source document: {doklink}

Clauses retrieved from that document for the changed fields (ids: {clause_ids}):

{clause_text}

Decide whether a reader of this land now sees something they could not see
before, using only the clauses above. Quote the clause you rely on exactly as
it is written. If none of these clauses governs the changed field, set
can_decide to false."""


def ground(
    *,
    sub_area_description: str,
    changed_fields: dict,
    clauses: list[Clause],
    doklink: str,
    client: Groq | None = None,
    model: str = DEFAULT_MODEL,
) -> Grounding:
    """Asks the model to settle an uncovered case from the document's clauses.

    Called only when retrieval produced at least one clause; an empty
    retrieval is an abstention decided in code and never costs a model call
    (see graph.py's _ground_node).
    """
    client = client or groq_client()
    system = get_prompt(GROUND_PROMPT_NAME)
    record_last_prompt(system)
    prompt = build_prompt(
        sub_area_description=sub_area_description,
        changed_fields=changed_fields,
        clauses=clauses,
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
                    "retrieved_clause_ids": [c.clause_id for c in clauses],
                },
                usage_details={
                    "input": usage.prompt_tokens,
                    "output": usage.completion_tokens,
                    "total": usage.total_tokens,
                },
            )
    return Grounding.model_validate_json(resp.choices[0].message.content)

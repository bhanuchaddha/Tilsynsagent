"""Shared helper for Groq's strict structured-output mode.

Verified live against openai/gpt-oss-120b (Phase 1 verification 1): the
endpoint rejects a Pydantic-generated JSON schema unless every object node
carries ``additionalProperties: false``, which Pydantic's own
``model_json_schema()`` does not emit. This is that fix, applied once, so
every strict call in the project goes through it rather than re-discovering
the requirement.
"""

from __future__ import annotations

from pydantic import BaseModel


def _add_additional_properties_false(node: object) -> object:
    if isinstance(node, dict):
        if node.get("type") == "object":
            node["additionalProperties"] = False
        for value in node.values():
            _add_additional_properties_false(value)
    elif isinstance(node, list):
        for item in node:
            _add_additional_properties_false(item)
    return node


def strict_schema(model: type[BaseModel]) -> dict:
    """A Groq-strict-mode-compatible JSON schema for a Pydantic model."""
    return _add_additional_properties_false(model.model_json_schema())


def response_format(model: type[BaseModel], name: str) -> dict:
    """The ``response_format`` payload for a Groq chat completion call."""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "schema": strict_schema(model), "strict": True},
    }

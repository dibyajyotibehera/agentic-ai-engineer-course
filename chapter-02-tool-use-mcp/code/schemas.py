"""Strict Pydantic schemas for the chapter's tools: validation that fails closed.

The raw server in ``raw_mcp_server.py`` checks arguments by hand, and its
checks are thin: a string where an integer belongs, or an invented extra
field, slips through until the handler itself trips over it. That is the
gap this module closes. Every tool's arguments are a Pydantic model with
``extra="forbid"`` and ``strict=True``, which together mean:

* a missing required field is rejected;
* a field of the wrong type is rejected, with no silent coercion (the string
  ``"5"`` is not the integer ``5``);
* an unknown field is rejected, so a model cannot smuggle in an argument
  the tool never declared;
* a value outside its declared range, pattern or enumeration is rejected.

When validation fails, Pydantic raises ``ValidationError`` and the caller
gets a JSON-RPC ``-32602`` (Invalid params) error. Nothing runs, nothing is
half-written, and the error names the exact field and the exact rule it
broke, which is what makes the failure debuggable at 2 a.m.

The same models generate the JSON Schema that ``tools/list`` advertises:
``SearchDocsArgs.model_json_schema()`` is the ``inputSchema`` the model
sees, so the contract the model reasons from and the contract the server
enforces are the same object, not two copies that can drift apart.

Requires: ``pip install pydantic`` (see ``requirements.txt``).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

__all__ = [
    "SearchDocsArgs",
    "GetTicketArgs",
    "UpdateTicketStatusArgs",
    "TOOL_SCHEMAS",
    "validate_tool_call",
]

# Every model in this module shares one configuration: forbid anything not
# declared, and refuse to coerce values into the declared type.
STRICT_CONFIG = ConfigDict(extra="forbid", strict=True)

TicketId = Annotated[
    str,
    Field(pattern=r"^TKT-\d+$", description="Ticket identifier, of the form TKT-<digits>."),
]

TicketStatus = Literal["open", "in_progress", "resolved", "closed"]


class SearchDocsArgs(BaseModel):
    """Arguments for the ``search_docs`` tool."""

    model_config = STRICT_CONFIG

    query: Annotated[str, Field(min_length=1, max_length=200, description="Topic to search for.")]
    max_results: Annotated[int, Field(ge=1, le=10, description="Maximum entries to return.")] = 5


class GetTicketArgs(BaseModel):
    """Arguments for the ``get_ticket`` tool."""

    model_config = STRICT_CONFIG

    ticket_id: TicketId


class UpdateTicketStatusArgs(BaseModel):
    """Arguments for the ``update_ticket_status`` tool.

    The cross-field rule is the interesting part: moving a ticket to
    ``resolved`` without saying how it was resolved is a write that
    destroys information, so the model must supply a resolution note.
    A per-field check cannot express that; a model validator can.
    """

    model_config = STRICT_CONFIG

    ticket_id: TicketId
    status: TicketStatus
    resolution_note: Annotated[
        str,
        Field(
            default="",
            max_length=500,
            description="How the ticket was resolved. Required when status is 'resolved'.",
        ),
    ] = ""

    @model_validator(mode="after")
    def _resolution_requires_a_note(self) -> "UpdateTicketStatusArgs":
        if self.status == "resolved" and not self.resolution_note.strip():
            raise ValueError("resolution_note is required when status is 'resolved'")
        return self


# Registry: tool name -> the model that validates its arguments. The
# scoped server looks a tool's model up here, validates with it, and only
# then calls the handler with the validated model.
TOOL_SCHEMAS: dict[str, type[BaseModel]] = {
    "search_docs": SearchDocsArgs,
    "get_ticket": GetTicketArgs,
    "update_ticket_status": UpdateTicketStatusArgs,
}


def validate_tool_call(name: str, arguments: dict) -> BaseModel:
    """Validate one tool call's arguments against its strict schema.

    Returns the validated model on success. Raises ``ValueError`` with a
    human-readable message naming the offending fields when the tool is
    unknown or the arguments fail validation. The caller translates that
    into a JSON-RPC -32602 error, so a malformed call fails closed: the
    handler is never invoked.
    """
    model = TOOL_SCHEMAS.get(name)
    if model is None:
        raise ValueError(f"unknown tool: {name}")
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        # ValidationError lists every broken field at once; joining the
        # messages keeps the JSON-RPC error readable in one line.
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ValueError(f"invalid arguments for tool {name!r}: {details}") from exc


def tool_input_schema(name: str) -> dict:
    """The JSON Schema to advertise for ``name`` in a tools/list result.

    Generated from the same Pydantic model that validates calls, so the
    advertised contract and the enforced contract cannot drift apart.
    Pydantic emits JSON Schema 2020-12, which is the dialect the Model
    Context Protocol specification defaults to for tool input schemas.
    """
    model = TOOL_SCHEMAS[name]
    schema = model.model_json_schema()
    # Pydantic adds a "title" per model; it is harmless, but the advertised
    # schema is cleaner without model-internal naming.
    schema.pop("title", None)
    return schema


if __name__ == "__main__":
    # A quick self-check, run with: python schemas.py
    print("search_docs schema:")
    print(tool_input_schema("search_docs"))
    print()
    print("Valid call:", validate_tool_call("search_docs", {"query": "scoping"}))
    for bad in (
        {"query": "scoping", "max_results": "5"},  # strict: string is not an integer
        {"query": "scoping", "surprise": True},  # extra field is forbidden
        {"query": ""},  # below the minimum length
        {"ticket_id": "TKT-1"},  # wrong tool name for these arguments
    ):
        try:
            validate_tool_call("search_docs", bad)
        except ValueError as exc:
            print(f"Rejected {bad}: {exc}")
    try:
        validate_tool_call(
            "update_ticket_status", {"ticket_id": "TKT-1001", "status": "resolved"}
        )
    except ValueError as exc:
        print(f"Rejected unresolved resolution: {exc}")

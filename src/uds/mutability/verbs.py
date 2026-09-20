"""Shared machinery of the create/delete verbs.

The ``op_create``/``op_delete`` implementations of every family repeat
the same three concerns: dispatch the REST call through the proxy (the
root boundary or the detail one, always outside the async event loop),
extract the real uuid of the created item from the response, and phrase
the result message. This module is the single home of those pieces, so
each family stays down to its genuinely specific part: building the
payload and resolving names.

It also hosts the standard tool texts of the verbs: every family phrases
creation and deletion the same way, so the agent reads one consistent
contract across the whole catalog (family-specific semantics travel as
explicit arguments, never as re-worded boilerplate).
"""

import typing

from asgiref.sync import sync_to_async

from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import RestProxy, RestTarget

__all__ = [
    "create_tool_description",
    "create_tool_title",
    "created_message",
    "delete_tool_description",
    "delete_tool_title",
    "execute_create",
    "execute_delete",
]

JsonObject = dict[str, typing.Any]


# ---------------------------------------------------------------- proxies


async def execute_create(
    target: RestTarget,
    request: ExtendedHttpRequestWithUser,
    params: JsonObject,
    parent_uuid: str | None = None,
) -> str | None:
    """POST a creation through the proxy; return the real uuid when reported.

    Root targets (no ``parent``) run through the async proxy; detail
    targets need the parent uuid to resolve (and permission check) the
    container, so the sync boundary is invoked directly.
    """
    if target.parent is not None:
        response = await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(
            target, request, params, parent_uuid
        )
    else:
        response = await RestProxy().execute(target, request, params)
    if isinstance(response, dict):
        payload = typing.cast("JsonObject", response)
        # Detail operations answer wrapped in the REST envelope
        # ({"result": ..., "stamp": ...}); root ones answer with the
        # plain item payload. Unwrap so both report the new uuid.
        if "result" in payload and isinstance(payload["result"], dict):
            payload = typing.cast("JsonObject", payload["result"])
        return payload.get("id")
    return None


async def execute_delete(
    target: RestTarget,
    request: ExtendedHttpRequestWithUser,
    parent_uuid: str | None = None,
) -> None:
    """DELETE the target through the proxy (root or detail boundary)."""
    if target.parent is not None:
        await sync_to_async(RestProxy._execute_sync, thread_sensitive=True)(target, request, {}, parent_uuid)
    else:
        await RestProxy().execute(target, request, {})


def created_message(noun: str, name: str, new_uuid: str | None) -> str:
    """Message of a creation, reporting the real uuid when known."""
    return f'{noun} "{name}" created' + (f" (uuid {new_uuid})" if new_uuid else "")


# ---------------------------------------------------- standard tool texts


def _indefinite(noun: str) -> str:
    """The indefinite article that precedes ``noun`` ("an account").

    Simple initial-letter rule: the family nouns are plain English words,
    the only vowel-initial ones needing "an".
    """
    return "an" if noun[:1].lower() in "aeiou" else "a"


def create_tool_title(noun: str) -> str:
    return f"Propose creating {_indefinite(noun)} {noun}"


def delete_tool_title(noun: str) -> str:
    return f"Propose deleting {_indefinite(noun)} {noun}"


def create_tool_description(
    noun: str,
    fields_hint: str,
    *,
    gallery: str | None = None,
    needs_parent: bool = False,
    extra: str = "",
) -> str:
    """Standard description of a creation proposal tool.

    ``fields_hint`` lists the form fields (or points to discovery);
    ``gallery`` names the get_creatable_types kind when the creation is
    type-driven; ``needs_parent`` marks detail creations; ``extra``
    carries family-specific caveats (appended after the shape).
    """
    target_hint = " (target_uuid is the PARENT's uuid)" if needs_parent else ""
    kind_hint = (
        f"one of the get_creatable_types listing for kind {gallery}"
        if gallery
        else "the creation subtype, when the kind has one"
    )
    caveat = f" {extra}" if extra else ""
    return (
        f"Propose creating a NEW {noun}{target_hint}. The values are the subtype "
        f"({kind_hint}) plus the initial values of the {noun} form fields "
        f"({fields_hint}; use get_mutable_fields with for_type to discover them)."
        f"{caveat} There is no live state to conflict with, so the proposal carries "
        "no freshness checks, and it does NOT create anything: it is queued until an "
        "administrator approves it. The real uuid is assigned at execution and "
        "reported back in the result."
    )


def delete_tool_description(noun: str, semantics: str) -> str:
    """Standard description of a deletion proposal tool.

    ``semantics`` states what the deletion does to the surrounding
    world (cascades, refusals), like the administration interface does.
    """
    return (
        f"Propose deleting {_indefinite(noun)} {noun}: {semantics} No fields are needed: the {noun} "
        "itself is the target of the proposal, and any change to it after the "
        "proposal was taken cuts and denies the flow. The proposal does NOT delete "
        "anything: it is queued until an administrator approves it."
    )

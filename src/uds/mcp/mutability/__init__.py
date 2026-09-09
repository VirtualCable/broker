"""MCP tools for the supervised mutability subsystem.

Thin tool surface (:mod:`.tools`) over :mod:`uds.mutability`; the REST
surfaces (``/flows/management``, ``/flows/own``) are the canonical way
to interact with flows, and these tools are being migrated to proxy
them.
"""

from .tools import register_mutability_tools

__all__ = [
    "register_mutability_tools",
]

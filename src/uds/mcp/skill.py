"""Skill bundle generator for UDS MCP clients.

The MCP surface described in ``uds.mcp.default_catalog`` is packaged as a
downloadable skill so an external agent (CLI, IDE assistant, browser
plugin) can connect to the UDS REST/MCP endpoint without hand-writing
the configuration.

The bundle is **server-specific**: the REST handler passes the absolute
URL of this broker's MCP endpoint, so ``mcp_config.json`` and the
instructions ship ready to use. The only value the user must supply is
the API token, referenced through the ``UDS_TOKEN`` environment
variable (the raw token is never stored server-side, so it cannot be
baked into the bundle).

The generator writes an in-memory ``.tar.gz`` containing:

- ``SKILL.md`` — compact operating guide for the surface: how the tool
  families fit together and how a change is performed. The per-tool
  contracts (arguments, semantics) travel in ``tools/list`` itself, so
  the guide never repeats them; everything it states is derived from
  the live catalog and the mutability registry.
- ``mcp_config.json`` — entry-point configuration for MCP-aware clients.
- ``README.md`` — installation instructions.

The bundle is built on demand by :class:`SkillHandler` in the REST layer.
This module keeps the bundling logic out of the request handler so the
handler stays small and the format can evolve independently.
"""

import collections.abc
import dataclasses
import io
import json
import logging
import tarfile
import time
import typing

from uds.mcp import Catalog, build_catalog
from uds.mcp.catalog import ToolDefinition
from uds.mcp.redaction import REDACTED


logger: logging.Logger = logging.getLogger(__name__)


_SKILL_NAME: typing.Final[str] = "uds-mcp"
_SKILL_VERSION: typing.Final[str] = "0.1.0"
_SKILL_MIME: typing.Final[str] = "application/gzip"

# Environment variable the client resolves to obtain the ``uat-...`` API
# token. Kept short on purpose: it is the only value the user configures.
_TOKEN_ENV: typing.Final[str] = "UDS_TOKEN"


@dataclasses.dataclass(frozen=True, slots=True)
class SkillBundle:
    """Output of the skill generator."""

    name: str
    mime_type: str
    data: bytes
    size: int
    sha256: str

    def to_download_envelope(self) -> dict[str, typing.Any]:
        """Return the JSON envelope served by the REST handler.

        The frontend uses this to either trigger a download via
        ``data:`` URIs or a manual file-save flow. The envelope is
        deliberately compact; the bundle is regenerated from the live
        catalog on every request, so it always matches the running
        broker.
        """
        import base64

        return {
            "name": self.name,
            "mime_type": self.mime_type,
            "encoding": "base64",
            "size": self.size,
            "sha256": self.sha256,
            "data": base64.b64encode(self.data).decode("ascii"),
        }


class SkillBuilder:
    """Build a server-specific MCP skill bundle from the current UDS catalog."""

    def __init__(self, mcp_url: str, catalog: Catalog | None = None) -> None:
        self._mcp_url = mcp_url.rstrip("/")
        self._catalog: Catalog = catalog or build_catalog()

    def build(self) -> SkillBundle:
        """Return the current skill bundle."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for filename, payload in self._iter_files():
                info = tarfile.TarInfo(name=f"{_SKILL_NAME}/{filename}")
                info.size = len(payload)
                info.mtime = int(time.time())
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(payload))
        data = buffer.getvalue()
        sha256 = self._fingerprint(data)
        return SkillBundle(
            name=f"{_SKILL_NAME}-{_SKILL_VERSION}.tar.gz",
            mime_type=_SKILL_MIME,
            data=data,
            size=len(data),
            sha256=sha256,
        )

    def _iter_files(self) -> collections.abc.Iterable[tuple[str, bytes]]:
        yield "SKILL.md", self._render_skill_markdown().encode("utf-8")
        yield "mcp_config.json", self._render_mcp_config().encode("utf-8")
        yield "README.md", self._render_readme().encode("utf-8")

    # ------------------------------------------------------------------
    # File renderers
    # ------------------------------------------------------------------
    def _render_skill_markdown(self) -> str:
        """Render ``SKILL.md`` as a compact operating guide.

        The tool descriptions shipped by ``tools/list`` carry the
        per-tool contracts; duplicating all of them here produced a
        ~75 KB reference that agents read shallowly. The guide instead
        teaches the shape of the surface — naming, read/write pairing,
        the discovery chain, the flow lifecycle, listing conventions,
        freshness and permission levels — using only facts derived from
        the live catalog and the mutability registry.
        """
        tools = list(self._catalog.tools())
        lines: list[str] = [
            f"# UDS MCP skill {_SKILL_VERSION}",
            "",
            "UDS exposes a Model Context Protocol surface backed by its REST API.",
            "This guide explains how the pieces fit together; each tool's own",
            "description defines its exact arguments and semantics. Prefer narrow,",
            "targeted calls over bulk reads.",
            "",
            f"- Endpoint: `{self._mcp_url}`",
            f"- Authentication: `Authorization: Bearer ${{{_TOKEN_ENV}}}`",
            f"- Surface: {len(tools)} tools, {len(list(self._catalog.resources()))} resources",
            "",
            "## Naming",
            "",
            "- `list_*` / `get_*`: read. `list_*` pages a REST collection;",
            "  `get_*` fetches one item or relation by uuid.",
            "- `propose_*`: the only way to change anything: it never applies",
            '  the change itself (see "Proposals and flows").',
            "- `create_flow` / `submit_flow` / `cancel_flow` / `update_flow_action`:",
            "  manage the batch of proposals.",
            "- Reads and writes pair by subject: the `get_servicepool_*` family",
            "  feeds the `propose_servicepool_*` family.",
            "",
        ]
        lines.extend(self._render_discovery_section(tools))
        lines.extend(self._render_flow_section())
        lines.extend(self._render_listing_section(tools))
        lines.append("## Permissions")
        lines.append("")
        lines.append(
            "- Your API token maps to a UDS user; the usual REST permissions "
            "apply on every call.\n"
            "- Staff sees the platform inventory; some tools (system logs, "
            "dashboard, security check, global config) are administrators only.\n"
            "- `propose_*` needs management permission on the target: the flow "
            "queues and executes later with the administrator's approval."
        )
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"Generated at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Guide sections (all derived from the live catalog / registry)
    # ------------------------------------------------------------------
    def _render_discovery_section(self, tools: list[ToolDefinition]) -> list[str]:
        """The chain to follow before proposing anything."""
        read_names = {t.name for t in tools}
        lines = ["## Finding things and field values", ""]
        lines.append(
            '- Inventory: `list_*` with an OData `filter` (see "Listing"), '
            "`get_*` by uuid. Platform health: `get_dashboard`, "
            "`get_checks`, `get_platform_stats`, `get_system_logs`."
        )
        lines.append(
            "- Before a proposal, discover the accepted fields and their current "
            "values with `get_mutable_fields` (pass the item uuid, or the data "
            "type as `for_type`). Dynamic pickers resolve through "
            "`get_gui_field_choices`."
        )
        if "get_creatable_types" in read_names:
            gallery_kinds = sorted(self._gallery_kinds())
            if gallery_kinds:
                lines.append(
                    "- Creations are type-driven: `get_creatable_types` lists the "
                    "subtypes of a kind (same gallery the administration interface "
                    "shows); pass the chosen one as `data_type` of the matching "
                    f"`propose_*_create`. Kinds with a gallery: {', '.join(gallery_kinds)}. "
                    "Each `propose_*_create` description names its kind's rule."
                )
        lines.append("")
        return lines

    @staticmethod
    def _render_flow_section() -> list[str]:
        """The proposal/flow lifecycle, once, for all families."""
        return [
            "## Proposals and flows",
            "",
            (
                "- Nothing applies until an administrator approves a flow. A proposal "
                "validates at propose time and again at execution; conditions the live "
                "world can change into meanwhile (provider quotas, publication state) "
                "surface as execution-time failures, reported back on the flow."
            ),
            (
                "- Lifecycle: `create_flow` (private draft) → `propose_*` tools to add "
                "actions → `submit_flow` (queued for approval; final from then on). "
                "`update_flow_action` refines a draft action; `cancel_flow` withdraws a "
                "draft or an unlocked submitted flow."
            ),
            ("- Several changes of one task share a flow; unrelated tasks use separate flows."),
            (
                "- Freshness (update/delete/set verbs): proposals bind to the target's "
                "state and the flow is denied if it changed in between, so re-read the "
                "item just before proposing. Creation proposals carry no freshness "
                "check; the real uuid appears in the execution result."
            ),
            "",
        ]

    def _render_listing_section(self, tools: list[ToolDefinition]) -> list[str]:
        """Conventions of the generated list tools."""
        lists = [t for t in tools if t.name.startswith("list_")]
        parented = sum(1 for t in lists if "parent_uuid" in (t.input_schema or {}).get("properties", {}))
        return [
            "## Listing",
            "",
            (
                f"- The {len(lists)} `list_*` tools (OData style): `filter`, `orderby`, "
                "`top`, `skip` and `select`. Filtering works on the item fields; use "
                "`select` to keep responses small."
            ),
            (
                f"- {parented} of them scope to a parent item and require its "
                "`parent_uuid` (for example the users of an authenticator)."
            ),
            (
                "- `get_item_logs` reads the log trail of one object; "
                "`get_system_logs` the global log (administrators)."
            ),
            "",
        ]

    @staticmethod
    def _gallery_kinds() -> set[str]:
        """Kinds whose creation picks a type from the gallery.

        Derived from the mutability registry: roots that register a
        ``create`` binding on a family declaring ``create_has_gallery``.
        """
        from uds.mutability import registry

        gallery_roots = {family.type_id for family in registry.all_families() if family.create_has_gallery}
        kinds: set[str] = set()
        for entry in registry.all_bindings():
            root, _, operation = entry.full_id.rpartition(".")
            if operation == "create" and root in gallery_roots:
                kinds.add(root)
        return kinds

    def _render_mcp_config(self) -> str:
        """Render the MCP client configuration entry point."""
        config = {
            "name": _SKILL_NAME,
            "version": _SKILL_VERSION,
            "mcpServers": {
                "uds": {
                    "type": "http",
                    "url": self._mcp_url,
                    "headers": {
                        "Authorization": f"Bearer ${{{_TOKEN_ENV}}}",
                    },
                },
            },
            "resources": [
                {"uri": resource.uri, "name": resource.name, "title": resource.title}
                for resource in self._catalog.resources()
            ],
            "redaction": {"placeholder": REDACTED},
        }
        return json.dumps(config, indent=2, sort_keys=True)

    def _render_readme(self) -> str:
        """Render ``README.md`` with installation instructions."""
        return (
            "# UDS MCP skill\n\n"
            f"This bundle is preconfigured for `{self._mcp_url}`.\n"
            "No server URL needs to be edited.\n\n"
            "## Install\n\n"
            "1. Save `mcp_config.json` into your MCP-aware client's skills\n"
            "   directory.\n"
            f"2. Export the `{_TOKEN_ENV}` environment variable with a\n"
            "   `uat-...` user API token issued by the administrator:\n\n"
            f"       export {_TOKEN_ENV}=uat-...\n\n"
            "3. Restart the client so it picks up the new skill.\n\n"
            "## Authentication\n\n"
            f"The client sends `Authorization: Bearer ${{{_TOKEN_ENV}}}` on\n"
            "every request. The token is mapped to the same UDS user that\n"
            "would log in interactively, so the usual REST permissions\n"
            "apply on every call.\n\n"
            "## Update\n\n"
            f"Download the bundle matching skill version `{_SKILL_VERSION}`\n"
            "again and replace the previous files. The bundle is regenerated\n"
            "on every request, so a single source of truth is the broker\n"
            "itself.\n"
        )

    @staticmethod
    def _fingerprint(data: bytes) -> str:
        import hashlib

        return hashlib.sha256(data).hexdigest()

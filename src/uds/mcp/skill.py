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

- ``SKILL.md`` — human and machine readable description derived from the
  curated catalog and the inventory walker.
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
from uds.mcp.redaction import REDACTED


logger = logging.getLogger(__name__)


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
        """Render ``SKILL.md`` from the catalog and the inventory."""
        lines: list[str] = [
            f"# UDS MCP skill {_SKILL_VERSION}",
            "",
            "UDS exposes a Model Context Protocol surface backed by its",
            "existing REST API. The bundle describes what the agent can do",
            "and how to connect.",
            "",
            f"- Endpoint: `{self._mcp_url}`",
            f"- Authentication: `Authorization: Bearer ${{{_TOKEN_ENV}}}`",
            "",
            "## What you can access",
            "",
        ]
        # Curated resources come from the catalog; the rest of the
        # inventory is summarised in the list below. We rely on the
        # catalog as the only source of curated entries so the operator
        # always has the last word.
        for resource in self._catalog.resources():
            lines.append(f"### `{resource.uri}` — {resource.title}")
            lines.append("")
            lines.append(resource.description)
            lines.append("")
            lines.append(f"- Access: {resource.access}")
            lines.append(f"- Returns: {resource.returns}")
            lines.append("")

        tools = list(self._catalog.tools())
        if tools:
            lines.append("## Tools")
            lines.append("")
            lines.append(
                "Every list tool accepts OData-style arguments: `filter`, "
                "`orderby`, `top`, `skip` and `select`. Tools that list items "
                "belonging to a parent object (for example the users of an "
                "authenticator) additionally require `parent_uuid` — the UUID "
                "of the parent item the collection is scoped to."
            )
            lines.append("")
            for tool in tools:
                properties = (tool.input_schema or {}).get("properties", {})
                requires_parent = "parent_uuid" in properties
                lines.append(f"### `{tool.name}` — {tool.title}")
                lines.append("")
                lines.append(tool.description)
                lines.append("")
                if requires_parent:
                    parent_desc = properties["parent_uuid"].get("description", "UUID of the parent item")
                    lines.append(f"- Requires `parent_uuid`: {parent_desc}")
                    lines.append("")
        else:
            lines.append("## Tools")
            lines.append("")
            lines.append("The current phase exposes resources only. Tools will be")
            lines.append("added in a follow-up; the contract for arguments and")
            lines.append("return shape will be derived from the same catalog.")
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"Generated at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
        return "\n".join(lines)

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

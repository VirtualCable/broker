"""Hand-curated, purpose-specific MCP tools, grouped by domain.

The generic ``list_*`` generator only covers model collections. These tools
expose the high-value read-only surfaces that need a crafted schema and
description, grouped by the domain they serve:

* :mod:`servicepools` — fallback access policies, usage forecast and cache
  sizing recommendations;
* :mod:`servers` — server group aggregates and per-server usage series;
* :mod:`authenticators` — user/group search inside an authenticator;
* :mod:`logs` — the per-object log modifier (``<uuid>/log``), unified as a
  single ``get_item_logs`` tool, and the admin-only global log tool;
* :mod:`system` — platform-wide usage counters and the security
  self-assessment;
* :mod:`reports` — CSV analytical reports (failed logins, admin activity).

Every executor forwards the live request through :class:`RestProxy`, so the
REST permission checks of each target handler stay in force: a staff
identity without read permission on the target item gets an access-denied
error, and the admin-only surfaces (global log, security check, reports,
platform-wide stats) only work for administrators.
"""

from ..catalog import Catalog, ToolDefinition

from . import authenticators, logs, providers, reports, servers, servicepools, system, tunnels

__all__ = ["curated_tools", "register_curated_tools"]


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the hand-curated tool set for the default catalog."""
    return (
        *servicepools.curated_tools(),
        *servers.curated_tools(),
        *authenticators.curated_tools(),
        *providers.curated_tools(),
        *tunnels.curated_tools(),
        *logs.curated_tools(),
        *system.curated_tools(),
        *reports.curated_tools(),
    )


def register_curated_tools(catalog: Catalog) -> None:
    """Register every curated tool on the given catalog."""
    for tool in curated_tools():
        catalog.add_tool(tool)

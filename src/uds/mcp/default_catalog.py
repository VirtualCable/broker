"""Curated read-only MCP catalog backed by existing REST handlers.

The catalog is built from the same :mod:`uds.REST.inventory` walker that
``genapi`` uses, so resources and tools stay aligned with the REST surface.
The curated pieces here are only the *descriptions* that the generic
generator cannot infer (system overview, version); the capabilities
themselves come from the walker.

The REST handler tree is built once at process start, so the catalog is
built lazily once and cached for the lifetime of the process.
"""

import functools

from uds.REST.inventory import (
    HandlerInventoryEntry,
    walk_rest_handlers,
)
from uds.REST.methods.system import System
from uds.REST.methods.version import UDSVersion
from uds.core.util.config import GlobalConfig

from .catalog import Catalog, ResourceDefinition
from .curated import register_curated_tools
from .mutability import register_mutability_tools
from .rest_proxy import RestTarget
from .tools import generated_list_tools


def _curated_resource(entry: HandlerInventoryEntry) -> ResourceDefinition | None:
    """Return the curated ``ResourceDefinition`` for the inventory entry, if any."""
    if entry.name == "system":
        return ResourceDefinition(
            uri="uds://system/overview",
            name="system-overview",
            title="System overview",
            description="Aggregate counts and status information for the UDS platform.",
            access=("Available to authenticated administrators through the REST system overview permission."),
            returns="Bounded aggregate platform counters.",
            target=RestTarget(System, "system", args=("overview",)),
        )
    if entry.name == "version":
        return ResourceDefinition(
            uri="uds://version",
            name="version",
            title="UDS version",
            description="The running UDS version and build information.",
            access="Available to authenticated staff through the MCP endpoint (STAFF).",
            returns="The UDS version and build metadata.",
            target=RestTarget(UDSVersion, "version"),
        )
    return None


def _curated_resources(catalog: Catalog) -> None:
    """Register the curated resources from the walker's real tree nodes.

    ``System`` and ``UDSVersion`` are registered handlers, so they appear in
    :func:`walk_rest_handlers` as ordinary entries. We deduplicate by
    resource name so two handlers with the same ``NAME`` do not both produce
    a ``uds://version`` registration.
    """
    seen_names: set[str] = set()
    for entry in walk_rest_handlers():
        curated = _curated_resource(entry)
        if curated is None:
            continue
        if curated.name in seen_names:
            continue
        seen_names.add(curated.name)
        catalog.add_resource(curated)


def _curated_tools(catalog: Catalog) -> None:
    """Register hand-curated, purpose-specific MCP tools.

    This is the registration point for capabilities that need more than
    the generic ``list_*`` generator: GET custom methods chosen by hand
    (fallback access policies, forecasting, cache recommendations, server
    statistics, authenticator search), the unified per-object logs tool,
    the admin-only global log tool and the diagnostics dashboard. See
    :mod:`uds.mcp.curated`.

    REST surfaces deliberately NOT offered over MCP, so this decision is
    visible here and does not resurface as an accidental gap:

    - ``servicespools/{id}/actions/{aid}/execute`` (launch a scheduled
      action now): it exists for the administrator to test or fire an
      action manually; a proposal flow *is* a deferred, approved
      execution, so an immediate "execute now" would bypass the model.
    - ``accounts/{id}/timemark``: closing an accounting period is a
      human administrative act, not an agent operation.
    - The ``permissions`` endpoints: access grants stay with humans and
      the plain REST API, even for an administrator-role token. Not
      exposed to the agent by policy.
    - The user API-token issue/revoke endpoints (``users/{id}/token``):
      credential minting is a security gate reserved for humans.
    - ``cache/flush`` and the dashboard's ``flush=1``: pure performance
      knobs over data the agent can already read; refreshing a cache is
      not information.

    Under evaluation for the next cycle: tunnel server assignment
    (``tunnels/{id}/assign/{server}``) and provider/server maintenance
    toggles (currently live-action tools outside ``MCP_MUTATIONS``). The
    user ``clean_related`` verb (the MFA data reset) already travels the
    supervised surface as the ``user.custom`` proposal; arbitrary report
    generation (needs a richer report descriptor) and the accounts
    ``clear`` button (it drops every account of a provider, an
    operation no agent should ever be offered) stay out of it.
    """
    register_curated_tools(catalog)


def _mutability_tools(catalog: Catalog) -> None:
    """Register the supervised mutability tools when the admin enables them.

    Gated by ``GlobalConfig.MCP_MUTATIONS`` (default off). The proposal
    tools never apply a change: they queue proposals for administrator
    approval. The flag is read when the catalog is built (once per
    process), so toggling it requires a worker restart, like the rest of
    the MCP surface.
    """
    if GlobalConfig.MCP_MUTATIONS.as_bool():
        register_mutability_tools(catalog)


def build_catalog() -> Catalog:
    """Build the read-only MCP catalog from the REST handler inventory.

    The catalog is built from the same inventory the generator and
    ``genapi`` use, so the resources and tools stay aligned with the REST
    surface.
    """
    catalog = Catalog()

    _curated_resources(catalog)
    _curated_tools(catalog)
    _mutability_tools(catalog)

    # Generic list tools for every model collection handler (master and
    # detail). The generator derives names from the handler's full path,
    # so service pools surface as ``list_servicespools`` like every other
    # collection.
    for tool in generated_list_tools():
        catalog.add_tool(tool)

    return catalog


@functools.cache
def get_catalog() -> Catalog:
    """Return the process-wide default catalog, building it on first use.

    The REST handler tree is registered once at import time, so the
    catalog cannot change within a running process. Tool executors
    receive the live HTTP request on every call, so no per-request
    rebuild is needed.
    """
    return build_catalog()

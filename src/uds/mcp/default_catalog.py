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

from .catalog import Catalog, ResourceDefinition
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
    the generic ``list_*`` generator: surfaces that are not
    ``ModelHandler`` collections (reports being the canonical candidate)
    or crafted answers for support and diagnostics. Curated tools land
    here with their own name, description and executor — concrete, fast,
    high-value answers instead of a generic listing.

    Nothing is registered yet by design: reports and similar surfaces
    are deliberately left out until their tools are defined.
    """


def build_catalog() -> Catalog:
    """Build the read-only MCP catalog from the REST handler inventory.

    The catalog is built from the same inventory the generator and
    ``genapi`` use, so the resources and tools stay aligned with the REST
    surface.
    """
    catalog = Catalog()

    _curated_resources(catalog)
    _curated_tools(catalog)

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

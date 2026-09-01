"""Curated read-only MCP catalog backed by existing REST handlers."""

from uds.REST.methods.system import System
from uds.REST.methods.version import UDSVersion

from .catalog import Catalog, ResourceDefinition
from .rest_proxy import RestTarget


def build_catalog() -> Catalog:
    """Build the initial read-only resource catalog from REST handlers."""
    catalog = Catalog()
    resources = (
        ResourceDefinition(
            uri="uds://system/overview",
            name="system-overview",
            title="System overview",
            description="Aggregate counts and status information for the UDS platform.",
            access="Available to authenticated administrators through the REST system overview permission.",
            returns="Bounded aggregate platform counters.",
            target=RestTarget(System, "system", args=("overview",)),
        ),
        ResourceDefinition(
            uri="uds://version",
            name="version",
            title="UDS version",
            description="The running UDS version and build information.",
            access="Available to an authenticated UDS user.",
            returns="The UDS version and build metadata.",
            target=RestTarget(UDSVersion, "version"),
        ),
    )
    for resource in resources:
        catalog.add_resource(resource)
    return catalog

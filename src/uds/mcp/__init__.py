"""Model Context Protocol integration for the UDS REST API."""

from .catalog import Catalog, ResourceDefinition, ToolDefinition
from .default_catalog import build_catalog
from .redaction import REDACTED, SENSITIVE_FIELDS, redact
from .rest_proxy import RestProxy, RestTarget
from .server import MCPServerCore

__all__ = [
    "REDACTED",
    "SENSITIVE_FIELDS",
    "Catalog",
    "build_catalog",
    "MCPServerCore",
    "ResourceDefinition",
    "RestProxy",
    "RestTarget",
    "ToolDefinition",
    "redact",
]

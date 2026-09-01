"""Model Context Protocol integration for the UDS REST API."""

from .catalog import Catalog, ResourceDefinition, ToolDefinition
from .default_catalog import build_catalog, default_catalog_for_request
from .redaction import REDACTED, SENSITIVE_FIELDS, redact
from .rest_proxy import RestProxy, RestTarget
from .server import MCPServerCore
from .tools import generated_list_tools

__all__ = [
    "REDACTED",
    "SENSITIVE_FIELDS",
    "Catalog",
    "MCPServerCore",
    "ResourceDefinition",
    "RestProxy",
    "RestTarget",
    "ToolDefinition",
    "build_catalog",
    "default_catalog_for_request",
    "generated_list_tools",
    "redact",
]

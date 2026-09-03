"""Model Context Protocol integration for the UDS REST API."""

from .catalog import Catalog, ResourceDefinition, ToolDefinition
from .default_catalog import build_catalog, get_catalog
from .limits import allow_request
from .redaction import REDACTED, SENSITIVE_FIELDS, redact
from .rest_proxy import RestProxy, RestTarget
from .server import MCPServerCore
from .tools import generated_list_tools
from .validation import validate_arguments

__all__ = [
    "REDACTED",
    "SENSITIVE_FIELDS",
    "Catalog",
    "MCPServerCore",
    "ResourceDefinition",
    "RestProxy",
    "RestTarget",
    "ToolDefinition",
    "allow_request",
    "build_catalog",
    "generated_list_tools",
    "get_catalog",
    "redact",
    "validate_arguments",
]

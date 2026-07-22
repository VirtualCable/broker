import typing
from uds.core import types

if typing.TYPE_CHECKING:
    from uds import models


def get_default_script(transport: "models.Transport") -> types.transports.TransportScript:
    """
    Returns the default script for the transport
    """
    return types.transports.TransportScript(
        script='throw new Error("The selected transport is not supported on your platform: " + (data.transport || "" ) );',
        script_type=types.transports.ScriptType.JAVASCRIPT,
        signature_algorithm=types.transports.SignatureAlgorithm.MLDSA65,
        signature_b64="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",
        parameters={"transport": transport.name},
        # Default log settings
    )

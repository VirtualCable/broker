#
# Copyright (c) 2012-2024 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#    * Neither the name of Virtual Cable S.L. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import argparse
import json
import logging
import tempfile
import typing

import yaml

from django.core.management.base import BaseCommand

from uds.core import consts
from uds.core import types
from uds.REST.inventory import HandlerInventoryEntry, walk_rest_handlers
from uds.REST.model import base as model_base

logger = logging.getLogger(__name__)

SECURITY_NAME: typing.Final[str] = "udsApiAuth"
BEARER_SECURITY_NAME: typing.Final[str] = "udsApiAuthBearer"
DEFAULT_OUTPUT: typing.Final[str] = f"{tempfile.gettempdir()}/uds-api"


def _generate_api() -> types.rest.api.OpenAPI:
    comps = model_base.BaseModelHandler.common_components()
    paths = model_base.BaseModelHandler.common_paths()

    # The inventory walker mirrors the tree traversal the dispatcher uses:
    # every registered handler at its tree path, plus each detail handler
    # simulated at ``{path}/{uuid}/{name}``. It already produces the same
    # ``(handler, path)`` pairs this generator used to discover by hand.
    def process_entry(entry: HandlerInventoryEntry) -> None:
        nonlocal comps

        full_path = entry.full_path
        tags = [full_path.split("/")[1].capitalize()] if len(full_path.split("/")) > 1 else []
        # Prefer the bearer scheme for new integrations.  Legacy
        # ``X-Auth-Token`` clients keep working because the server
        # still accepts it; the deprecation note lives in the
        # security scheme description above.
        security = BEARER_SECURITY_NAME if entry.handler.ROLE != consts.Role.ANONYMOUS else ""

        components = entry.handler.api_components()
        comps = comps.union(components)
        paths.update(entry.handler.api_paths(full_path, tags, security))

    for entry in walk_rest_handlers():
        process_entry(entry)

    # Ensure all paths with {uuid} declare the uuid parameter in every operation
    UUID_PARAM = types.rest.api.Parameter(
        name="uuid",
        in_="path",
        required=True,
        description="The UUID of the item",
        schema=types.rest.api.Schema(type="string", format="uuid"),
    )

    for path, path_item in paths.items():
        if "{uuid}" not in path:
            continue
        # NOTE: ``path_item.query`` (RFC 10008) is iterated historically but
        # the field is no longer serialised (see PathItem.as_dict and
        # api_helpers.py).  We keep iterating it for symmetry with the other
        # methods, in case a future OpenAPI 3.x version or extension re-enables
        # it.  When ``query`` is None, the body is a no-op.
        for operation in (path_item.get, path_item.post, path_item.put, path_item.delete, path_item.query):
            if operation and not any(p.name == "uuid" for p in operation.parameters):
                operation.parameters.append(UUID_PARAM)

    comps.securitySchemes = {
        # Legacy scheme.  Kept for backward compatibility with clients
        # still sending ``X-Auth-Token``.  Will be removed in a future
        # release; new integrations must use ``udsApiAuthBearer``.
        SECURITY_NAME: {
            "type": "apiKey",
            "in": "header",
            "name": consts.auth.AUTH_TOKEN_HEADER,
            "description": (
                "DEPRECATED.  Use ``Authorization: Bearer <token>`` "
                "(see ``udsApiAuthBearer``).  This scheme still works "
                "today but will be removed in a future major release."
            ),
        },
        # Modern scheme.  Single header, opaque token.  RFC 6750 §2.1.
        # The internal prefix scheme (``ses-``, ``sk-``, ...) is an
        # implementation detail and may evolve; clients should treat
        # the token as opaque.
        BEARER_SECURITY_NAME: {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "Bearer token in the ``Authorization`` header. "
                "Returned by ``/auth/login`` (see ``/auth/login`` "
                "response).  Treat the token as opaque."
            ),
        },
    }

    return types.rest.api.OpenAPI(paths=paths, components=comps)


class Command(BaseCommand):
    help = "Generates the OpenAPI specification file(s) for the UDS REST API"

    @typing.override
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-o",
            "--output",
            type=str,
            dest="output",
            default=DEFAULT_OUTPUT,
            help=f"Output file path (without extension). Defaults to {DEFAULT_OUTPUT}",
        )
        parser.add_argument(
            "-f",
            "--format",
            type=str,
            dest="formats",
            default=[],
            action="append",
            choices=["json", "yaml"],
            help="Output format. Can be specified multiple times. Defaults to both json and yaml",
        )

    @typing.override
    def handle(self, *args: typing.Any, **options: typing.Any) -> None:
        output: str = options.get("output", DEFAULT_OUTPUT)
        formats: list[str] = options.get("formats", [])

        if not formats:
            formats = ["json", "yaml"]

        api = _generate_api()
        api_dict = api.as_dict()

        for fmt in formats:
            file_path = f"{output}.{fmt}"
            if fmt == "json":
                with open(file_path, "w", encoding="utf8") as f:
                    json.dump(api_dict, f, indent=4)
            elif fmt == "yaml":
                with open(file_path, "w", encoding="utf8") as f:
                    yaml.dump(api_dict, f)

            self.stdout.write(f"API specification generated: {file_path}")

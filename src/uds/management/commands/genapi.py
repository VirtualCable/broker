# -*- coding: utf-8 -*-
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
from uds.REST import dispatcher
from uds.REST.model import base as model_base
from uds.REST.model.master import ModelHandler

logger = logging.getLogger(__name__)

SECURITY_NAME: typing.Final[str] = "udsApiAuth"
DEFAULT_OUTPUT: typing.Final[str] = f"{tempfile.gettempdir()}/uds-api"


def _generate_api() -> types.rest.api.OpenAPI:
    root_node = dispatcher.Dispatcher.root_node

    comps = model_base.BaseModelHandler.common_components()
    paths = model_base.BaseModelHandler.common_paths()

    def process_node(node: types.rest.HandlerNode, path: str | None = None) -> None:
        nonlocal comps

        if handler := node.handler:
            full_path = path or ("/" + node.full_path().lstrip("/"))
            tags = [full_path.split("/")[1].capitalize()] if len(full_path.split("/")) > 1 else []
            security = SECURITY_NAME if handler.ROLE != consts.UserRole.ANONYMOUS else ""

            components = handler.api_components()
            comps = comps.union(components)
            paths.update(handler.api_paths(full_path, tags, security))

            if issubclass(handler, ModelHandler) and handler.DETAIL:
                for name, detail_cls in handler.DETAIL.items():
                    # Details are always under /{path}/{uuid}/{detail_name}
                    detail_path = f"{full_path}/{{uuid}}/{name}"
                    # We process detail_cls as a "node" but it's not in the tree as a node
                    # So we simulate it
                    process_node(types.rest.HandlerNode(name, detail_cls, node, {}), path=detail_path)

        for child in node.children.values():
            process_node(child)

    process_node(root_node)

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
        for operation in (path_item.get, path_item.post, path_item.put, path_item.delete, path_item.query):
            if operation and not any(p.name == "uuid" for p in operation.parameters):
                operation.parameters.append(UUID_PARAM)

    comps.securitySchemes = {
        SECURITY_NAME: {
            "type": "apiKey",
            "in": "header",
            "name": consts.auth.AUTH_TOKEN_HEADER,
        }
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

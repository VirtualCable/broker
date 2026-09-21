"""The MCP surface must materialize lazy REST items off the event loop.

``MCPServerCore`` is awaited from a sync REST handler through
``async_to_sync``, so anything left running on the event loop that reaches the
ORM raises ``SynchronousOnlyOperation``. ``ManagedObjectItem.as_dict()``
resolves the module instance, and a module whose UI declares a database-backed
choices field queries at exactly that point.

The ``list_*`` tools are safe by accident: their collections answer with item
summaries, which never build the instance. ``get_provider_allservices`` is not
— it yields full items — and the rest of the MCP suite still misses it because
its fixture services have no database-backed field. This test exists to fail
when the materialization runs in the wrong place.
"""

import json
import typing

from uds.core import environment
from uds.core.util.config import GlobalConfig

from tests.fixtures.modules.service.service import TestServiceWithServerGroup
from tests.utils import rest


class LazyMaterializationTest(rest.test.RESTTestCase):
    """A tool result that resolves its module instance must still answer 200."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.MCP_ENABLED.set(True)
        self.login_with_api_token()

    def _call_tool(self, name: str) -> dict[str, typing.Any]:
        response = self.client.rest_post(
            "mcp",
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
            ).encode("utf-8"),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = typing.cast("dict[str, typing.Any]", json.loads(response.content))
        self.assertNotIn("error", body, body)
        return body

    def test_allservices_resolves_a_database_backed_choices_field(self) -> None:
        provider = self.provider
        service = provider.services.create(
            name="Service with server group",
            data_type=TestServiceWithServerGroup.type_type,
            data=TestServiceWithServerGroup(
                environment.Environment.testing_environment(), provider.get_instance()
            ).serialize(),
            token="mcp-lazy-materialization-token",
        )

        body = self._call_tool("get_provider_allservices")

        payload = typing.cast("list[dict[str, typing.Any]]", json.loads(str(body["result"]["content"][0]["text"])))
        self.assertIn(service.name, {str(item["name"]) for item in payload})

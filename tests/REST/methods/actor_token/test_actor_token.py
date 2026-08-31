"""REST contract tests for registered actor token administration."""

import json
import typing

from tests.fixtures import servers as servers_fixtures
from tests.utils import rest
from uds import models
from uds.core import types


class ActorTokensRestTest(rest.test.RESTTestCase):
    """Ensure actor token administration never exposes bearer secrets."""

    def test_list_redacts_token_and_delete_uses_uuid(self) -> None:
        """The list contains a public UUID and deletion accepts that UUID."""
        self.login()
        server = servers_fixtures.create_server(type=types.servers.ServerType.ACTOR)
        raw_token = servers_fixtures.raw_token(server)

        response = self.client.rest_get("actortokens")

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        serialized = json.dumps(payload)
        self.assertNotIn(raw_token, serialized)
        items = payload if isinstance(payload, list) else payload.get("items", payload.get("result", []))
        item = next(item for item in typing.cast(list[dict[str, typing.Any]], items) if item["id"] == server.uuid)
        self.assertEqual(item["id"], server.uuid)

        response = self.client.rest_delete(f"actortokens/{server.uuid}")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(models.Server.objects.filter(uuid=server.uuid).exists())

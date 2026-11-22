#
# Copyright (c) 2026 Virtual Cable S.L.
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

"""Discovery authorization (Strix pentest_4494: vuln-0010 / vuln-0011).

``get_mutable_fields`` must inherit the object-level authorization of the
equivalent REST surfaces:

* a configuration section is admin-only — exactly what
  ``GET /uds/rest/config`` requires — while its definitions disclose the
  LIVE values of every key;
* the inline choices of a relation field only enumerate objects the
  caller may READ, as the REST collection does.
"""

import json
import typing

from uds import models
from uds.core import types
from uds.core.util import permissions
from uds.mcp.default_catalog import get_catalog

from tests.fixtures.services import create_db_servicepool, create_db_transport
from tests.utils import rest

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class DiscoveryAuthorizationTest(rest.test.RESTTestCase):
    """``get_mutable_fields`` is scoped to the caller's permissions."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        from uds.core.util.config import GlobalConfig

        GlobalConfig.MCP_ENABLED.set(True)
        GlobalConfig.MCP_MUTATIONS.set(True)
        get_catalog.cache_clear()

    @typing.override
    def tearDown(self) -> None:
        get_catalog.cache_clear()
        super().tearDown()

    def _call(self, name: str, arguments: dict[str, typing.Any]) -> dict[str, typing.Any]:
        response = self.client.rest_post(
            "mcp",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                }
            ).encode("utf-8"),
        )
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", json.loads(response.content))

    def _result_json(self, body: dict[str, typing.Any]) -> dict[str, typing.Any]:
        self.assertNotIn("error", body, body)
        return typing.cast("dict[str, typing.Any]", json.loads(str(body["result"]["content"][0]["text"])))

    # ------------------------------------------------- vuln-0010 (config)

    def test_config_section_discovery_is_denied_to_staff(self) -> None:
        """A staff user gets denied; GET /uds/rest/config answers 403 too."""
        self.login_with_api_token(self.staffs[0], as_admin=False)
        body = self._call("get_mutable_fields", {"action_type": "config.update", "for_type": "Security"})
        self.assertIn("error", body, body)

    def test_config_section_discovery_is_denied_to_staff_on_any_section(self) -> None:
        self.login_with_api_token(self.staffs[0], as_admin=False)
        for section in ("Security", "UDS", "MCP", "Admin", "Custom"):
            body = self._call("get_mutable_fields", {"action_type": "config.update", "for_type": section})
            self.assertIn("error", body, f"{section}: {body}")

    def test_config_section_values_reach_the_admin(self) -> None:
        """The admin keeps the full discovery (values included)."""
        self.login_with_api_token()
        result = self._result_json(
            self._call("get_mutable_fields", {"action_type": "config.update", "for_type": "MCP"})
        )
        names = [d["name"] for d in result["fields"]]
        self.assertTrue(names)
        self.assertTrue(all(name.startswith("MCP.") for name in names))
        # The live values are disclosed to the admin (the whole point of
        # the tool), one "current" per field
        self.assertTrue(all("current" in field for field in result["fields"]))

    # ------------------------------------------------- vuln-0011 (choices)

    def _seed_pools_and_transports(self) -> tuple[models.ServicePool, models.Transport, models.Transport]:
        service = self.user_service_managed.deployed_service.service
        pool = create_db_servicepool(service)
        attached = create_db_transport()
        attached.name = "Attached transport"
        attached.save()
        secret = create_db_transport()
        secret.name = "Secret unattached transport"
        secret.save()
        pool.transports.add(attached)
        permissions.add_user_permission(self.staffs[0], pool, types.permissions.PermissionType.READ)
        return pool, attached, secret

    def test_relation_choices_scoped_to_caller_permissions(self) -> None:
        """A staff user only sees the transports they may READ."""
        pool, attached, secret = self._seed_pools_and_transports()
        self.login_with_api_token(self.staffs[0], as_admin=False)
        result = self._result_json(
            self._call(
                "get_mutable_fields",
                {"action_type": "servicepool.transport.add", "target_uuid": pool.uuid},
            )
        )
        choices = [str(choice["value"]) for choice in result["fields"][0]["choices"]]
        self.assertIn(str(attached.uuid), choices)
        self.assertNotIn(str(secret.uuid), choices)

    def test_relation_choices_full_for_admin(self) -> None:
        pool, _attached, secret = self._seed_pools_and_transports()
        self.login_with_api_token()
        result = self._result_json(
            self._call(
                "get_mutable_fields",
                {"action_type": "servicepool.transport.add", "target_uuid": pool.uuid},
            )
        )
        choices = [str(choice["value"]) for choice in result["fields"][0]["choices"]]
        self.assertIn(str(secret.uuid), choices)

    def test_read_tool_choices_scoped_too(self) -> None:
        """The generated read view (get_servicepool_transport) inherits the
        same filter: same caller, same boundary."""
        pool, attached, secret = self._seed_pools_and_transports()
        self.login_with_api_token(self.staffs[0], as_admin=False)
        result = self._result_json(self._call("get_servicepool_transport", {"target_uuid": pool.uuid}))
        choices = [str(choice["value"]) for field in result["fields"] for choice in field.get("choices", [])]
        self.assertIn(str(attached.uuid), choices)
        self.assertNotIn(str(secret.uuid), choices)

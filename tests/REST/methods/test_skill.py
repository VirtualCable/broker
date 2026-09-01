"""Functional tests for the downloadable MCP skill bundle endpoint."""

import base64
import io
import tarfile
import typing

from tests.utils import rest


class SkillDownloadTest(rest.test.RESTTestCase):
    """The staff user can fetch the MCP skill bundle via REST."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login_with_api_token()

    def test_skill_endpoint_returns_a_tar_gz_payload(self) -> None:
        """``/uds/rest/skill/mcp`` returns a JSON envelope with a valid tar.gz."""
        response = self.client.rest_get("skill/mcp")
        self.assertEqual(response.status_code, 200, response.content)

        body = response.json()
        self.assertEqual(body["mime_type"], "application/gzip")
        self.assertEqual(body["encoding"], "base64")
        self.assertGreater(body["size"], 0)

        payload = base64.b64decode(body["data"])
        with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
            names = sorted(member.name for member in tar.getmembers())
        self.assertIn("uds-mcp/SKILL.md", names)
        self.assertIn("uds-mcp/mcp_config.json", names)
        self.assertIn("uds-mcp/README.md", names)

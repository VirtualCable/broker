"""Functional tests for the downloadable MCP skill bundle endpoint."""

import base64
import io
import json
import tarfile
import typing

from uds.core.util.config import GlobalConfig

from tests.utils import rest


class SkillDownloadTest(rest.test.RESTTestCase):
    """The staff user can fetch the MCP skill bundle via REST."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.MCP_ENABLED.set(True)
        self.login_with_api_token()

    def _bundle_files(self) -> dict[str, bytes]:
        response = self.client.rest_get("skill/mcp")
        self.assertEqual(response.status_code, 200, response.content)

        body = response.json()
        self.assertEqual(body["mime_type"], "application/gzip")
        self.assertEqual(body["encoding"], "base64")
        self.assertGreater(body["size"], 0)

        payload = base64.b64decode(body["data"])
        files: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
            for member in tar.getmembers():
                extracted = tar.extractfile(member)
                if extracted is not None:
                    files[member.name] = extracted.read()
        return files

    def test_skill_endpoint_returns_a_tar_gz_payload(self) -> None:
        """``/uds/rest/skill/mcp`` returns a JSON envelope with a valid tar.gz."""
        files = self._bundle_files()
        self.assertIn("uds-mcp/SKILL.md", files)
        self.assertIn("uds-mcp/mcp_config.json", files)
        self.assertIn("uds-mcp/README.md", files)

    def test_skill_endpoint_disabled_with_mcp(self) -> None:
        """The skill download shares the MCP config gate."""
        GlobalConfig.MCP_ENABLED.set(False)
        response = self.client.rest_get("skill/mcp")
        self.assertEqual(response.status_code, 404, response.content)

    def test_skill_endpoint_denies_untrusted_sources(self) -> None:
        """The skill download shares the admin trusted-host policy."""
        GlobalConfig.ADMIN_TRUSTED_SOURCES.set("10.0.0.0/8")
        response = self.client.rest_get("skill/mcp")
        self.assertEqual(response.status_code, 403, response.content)

    def test_bundle_is_server_specific(self) -> None:
        """The bundled config carries this server's absolute MCP endpoint."""
        files = self._bundle_files()

        config = json.loads(files["uds-mcp/mcp_config.json"])
        server = config["mcpServers"]["uds"]
        self.assertEqual(server["url"], "http://testserver/uds/rest/mcp")
        self.assertEqual(server["headers"]["Authorization"], "Bearer ${UDS_TOKEN}")
        self.assertNotIn("UDS_MCP_URL", files["uds-mcp/README.md"].decode("utf-8"))

        readme = files["uds-mcp/README.md"].decode("utf-8")
        self.assertIn("http://testserver/uds/rest/mcp", readme)
        self.assertIn("UDS_TOKEN", readme)

        skill_md = files["uds-mcp/SKILL.md"].decode("utf-8")
        self.assertIn("http://testserver/uds/rest/mcp", skill_md)

    def test_skill_md_is_a_compact_operating_guide(self) -> None:
        """``SKILL.md`` guides the surface instead of repeating tool descriptions.

        The per-tool contracts travel in ``tools/list``; the guide only
        teaches how the pieces fit, and stays small enough to read whole.
        """
        from uds.mcp import build_catalog

        skill_md = self._bundle_files()["uds-mcp/SKILL.md"].decode("utf-8")

        self.assertLess(len(skill_md), 8000)

        for section in (
            "## Naming",
            "## Proposals and flows",
            "## Listing",
            "## Permissions",
        ):
            self.assertIn(section, skill_md)
        self.assertIn("create_flow", skill_md)
        self.assertIn("get_mutable_fields", skill_md)

        # The guide never inlines the tool descriptions it replaced.
        catalog = build_catalog()
        descriptions = [tool.description for tool in catalog.tools() if tool.description]
        self.assertTrue(descriptions)
        self.assertFalse(any(desc in skill_md for desc in descriptions))

    def test_skill_md_derives_gallery_kinds_from_the_registry(self) -> None:
        """The gallery list names exactly the creatable kinds with a gallery."""
        from uds.mcp.skill import SkillBuilder
        from uds.mutability import registry

        gallery_roots = {f.type_id for f in registry.all_families() if f.create_has_gallery}
        expected = {
            entry.full_id.rpartition(".")[0]
            for entry in registry.all_bindings()
            if entry.full_id.endswith(".create") and entry.full_id.rpartition(".")[0] in gallery_roots
        }
        self.assertTrue(expected)

        skill_md = self._bundle_files()["uds-mcp/SKILL.md"].decode("utf-8")
        listed = set(SkillBuilder._gallery_kinds())
        self.assertEqual(listed, expected)
        for kind in expected:
            self.assertIn(kind, skill_md)

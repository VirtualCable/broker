"""End-to-end MCP test driving a real opencode agent against a live broker.

This is the "real client" companion of ``test_live_client.py``: instead of
the official SDK client alone, a full opencode session (LLM included) talks
to the MCP endpoint of an in-process broker loaded with controlled data.

Like the provider integration tests, it is **gated by ``test-vars.ini``**
and skipped unless the ``[opencode]`` section is present and enabled::

    [opencode]
    enabled=false
    model=anthropic/claude-sonnet-4-5

``model`` is any opencode model id with tool-calling support; credentials
come from the invoking user's opencode login, never from this file. The
broker, its data and the ``uat-`` token are created by the test itself, so
nothing in the section is secret.

All scenarios run inside a single test method, sequentially by design: the
agent spends real API quota per scenario and shares opencode's global
state, so parallel execution would only add cost and flakiness.
"""

import json
import os
import shutil
import subprocess
import tempfile
import typing
from django.test import LiveServerTestCase

from uds.auths.InternalDB.authenticator import InternalDBAuth
from uds.core.util.config import GlobalConfig
from uds import models
from uds.models.user import create_api_token, hash_api_token

from tests.utils import rest, vars as test_vars


class McpOpencodeLiveTest(rest.test.RESTTestCase, LiveServerTestCase):  # pyright: ignore[reportIncompatibleVariableOverride]  # client is UDSClient
    """One real opencode session per scenario, sequentially."""

    vars: dict[str, str]
    opencode_bin: str
    model: str
    token: str
    marker_auth_name: str

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.vars = test_vars.get_vars("opencode")
        if not self.vars:
            self.skipTest("No opencode vars (enable the [opencode] section in test-vars.ini)")
        self.opencode_bin = shutil.which(self.vars.get("binary", "opencode")) or ""
        if not self.opencode_bin:
            self.skipTest("opencode binary not found in PATH")
        self.model = self.vars.get("model", "")
        if not self.model:
            self.skipTest("No model set in the [opencode] section of test-vars.ini")

        GlobalConfig.MCP_ENABLED.set(True)

        # Distinctive fixture data the agent must echo back verbatim.
        # Created directly (instead of via fixtures) so the name is unique
        # and stable across runs, which keeps the assertion robust.
        self.marker_auth_name = "mcp-opencode-live-auth-7f3a"
        marker = models.Authenticator()
        marker.name = self.marker_auth_name
        marker.comments = "Marker authenticator for the opencode live test"
        marker.data_type = InternalDBAuth.type_type
        marker.save()
        marker.data = marker.get_instance().serialize()
        marker.save()

        user = self.admins[0]
        self.token = create_api_token()
        user.token_hash = hash_api_token(self.token)
        user.save(update_fields=["token_hash"])

    def _write_opencode_config(self, workspace: str) -> None:
        """Drop an ``opencode.json`` pointing at the live broker."""
        config = {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "uds": {
                    "type": "remote",
                    "url": f"{self.live_server_url}/uds/rest/mcp",
                    "enabled": True,
                    "oauth": False,
                    # Generous on purpose: the broker shares the machine with
                    # the whole test suite, and opencode drops MCP servers
                    # that are not ready when the timeout expires.
                    "timeout": 60000,
                    "headers": {"Authorization": f"Bearer {self.token}"},
                }
            },
        }
        with open(os.path.join(workspace, "opencode.json"), "w", encoding="utf-8") as file:
            json.dump(config, file, indent=2)

    def _run_agent(self, prompt: str) -> subprocess.CompletedProcess[str]:
        """Run one non-interactive opencode session in a fresh workspace."""
        command = [self.opencode_bin, "run", "--model", self.model]
        if self.vars.get("debug", "false") == "true":
            command += ["--print-logs", "--log-level", "DEBUG"]
        command.append(prompt)
        with tempfile.TemporaryDirectory(prefix="uds-mcp-opencode-") as workspace:
            self._write_opencode_config(workspace)
            return subprocess.run(
                command,
                cwd=workspace,
                # ``cwd`` alone is not enough: opencode resolves its project
                # directory from the PWD environment variable, which would
                # otherwise still point at the repository running the tests
                # (its own MCP servers would load instead of ours).
                env=dict(os.environ, PWD=workspace),
                capture_output=True,
                text=True,
                timeout=int(self.vars.get("timeout", "240")),
                check=False,
            )

    def _mcp_diagnosis(self) -> str:
        """Run ``opencode mcp list`` in a fresh workspace, for failure messages."""
        with tempfile.TemporaryDirectory(prefix="uds-mcp-opencode-") as workspace:
            self._write_opencode_config(workspace)
            result = subprocess.run(
                [self.opencode_bin, "mcp", "list"],
                cwd=workspace,
                env=dict(os.environ, PWD=workspace),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            return result.stdout + result.stderr

    def test_opencode_drives_uds_mcp(self) -> None:
        """The agent uses the ``uds`` MCP tools and reports real data."""
        scenarios: list[tuple[str, str, str]] = [
            (
                "list_authenticators",
                (
                    "Use the `uds` MCP server tool `list_authenticators` with no arguments. "
                    "Reply ONLY with the exact authenticator names it returns, one per line."
                ),
                self.marker_auth_name,
            ),
            (
                "list_providers",
                (
                    "Use the `uds` MCP server tool `list_providers` with no arguments. "
                    "Reply ONLY with the exact provider names it returns, one per line."
                ),
                self.provider.name,
            ),
        ]
        for scenario, prompt, expected in scenarios:
            with self.subTest(scenario=scenario):
                result = self._run_agent(prompt)
                self.assertEqual(
                    result.returncode,
                    0,
                    f"opencode failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
                )
                self.assertIn(
                    expected,
                    result.stdout,
                    f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}\nmcp list:\n{self._mcp_diagnosis()}",
                )

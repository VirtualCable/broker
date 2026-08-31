# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false
"""Contract tests for REST authentication resolution."""

import types as stdlib_types
import typing

from django.test import RequestFactory

from tests.utils import rest
from uds import models
from uds.REST.authentication import AuthenticationResolver
from uds.REST.methods.servers import ServerTest
from uds.core import consts, types
from uds.core.util.model import sql_now


class AuthenticationResolverTest(rest.test.RESTTestCase):
    """Verify principals for current session and registered-server paths."""

    def _request(self, headers: dict[str, str]) -> typing.Any:
        return stdlib_types.SimpleNamespace(headers=headers)

    def test_session_header_resolves_user_principal(self) -> None:
        self.login()

        result = AuthenticationResolver.resolve(
            self._request({consts.auth.AUTH_TOKEN_HEADER: self.auth_token}),
            consts.Role.USER,
            None,
        )

        self.assertEqual(result.principal.principal_kind, types.auth.PrincipalKind.USER)
        self.assertEqual(result.principal.credential_kind, types.auth.CredentialKind.SESSION)
        self.assertEqual(result.principal.user, self.admins[0])
        self.assertIsNotNone(result.session)

    def test_bearer_session_resolves_user_principal(self) -> None:
        self.login()

        result = AuthenticationResolver.resolve(
            self._request({consts.auth.AUTHORIZATION_HEADER: f"Bearer {self.auth_token}"}),
            consts.Role.USER,
            None,
        )

        self.assertEqual(result.principal.principal_kind, types.auth.PrincipalKind.USER)
        self.assertEqual(result.principal.credential_kind, types.auth.CredentialKind.SESSION)
        self.assertEqual(result.principal.user, self.admins[0])

    def test_registered_server_resolves_server_principal(self) -> None:
        raw_token = "registered-server-token"
        server = models.Server.objects.create(  # pyrefly: ignore[missing-attribute]
            register_username="tester",
            register_ip="127.0.0.1",
            ip="127.0.0.1",
            hostname="server.example.test",
            type=types.servers.ServerType.TUNNEL.value,
            stamp=sql_now(),
            token_hash=models.Server.hash_token(raw_token),
        )

        result = AuthenticationResolver.resolve(
            self._request({consts.auth.AUTHORIZATION_HEADER: f"Bearer sk-{raw_token}"}),
            consts.Role.ANONYMOUS,
            types.servers.ServerType.TUNNEL,
        )

        self.assertEqual(result.principal.principal_kind, types.auth.PrincipalKind.REGISTERED_SERVER)
        self.assertEqual(result.principal.credential_kind, types.auth.CredentialKind.REGISTERED_SERVER)
        self.assertEqual(result.principal.server, server)
        self.assertEqual(result.principal.credential_id, server.uuid)

    def test_anonymous_request_resolves_anonymous_principal(self) -> None:
        result = AuthenticationResolver.resolve(
            self._request({}),
            consts.Role.ANONYMOUS,
            None,
        )

        self.assertEqual(result.principal.principal_kind, types.auth.PrincipalKind.ANONYMOUS)
        self.assertEqual(result.principal.credential_kind, types.auth.CredentialKind.ANONYMOUS)
        self.assertIsNone(result.principal.user)
        self.assertIsNone(result.principal.server)

    def test_handler_attaches_principal_to_request(self) -> None:
        request = typing.cast(typing.Any, RequestFactory().post("/uds/rest/servers/test", data={}))
        request.ip = "127.0.0.1"
        request.user = None

        handler = ServerTest(request, "servers/test", "post", {})

        self.assertIs(request.principal, handler.principal)
        self.assertEqual(handler.principal.principal_kind, types.auth.PrincipalKind.ANONYMOUS)

    def test_handler_keeps_request_user_in_sync(self) -> None:
        # With a session principal, ``Handler`` must keep the legacy
        # ``request.user`` attribute pointing to the same User.
        from uds.REST import handlers as rest_handlers  # late import to avoid circular issues

        self.login()
        captured: dict[str, typing.Any] = {}

        original_init = rest_handlers.Handler.__init__

        def capture_init(self: typing.Any, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
            original_init(self, *args, **kwargs)
            captured["user"] = self._user
            captured["request_user"] = self._request.user
            captured["principal"] = self._principal

        rest_handlers.Handler.__init__ = capture_init  # type: ignore[assignment,arg-type]
        try:
            response = self.client.rest_get("providers/overview")
        finally:
            rest_handlers.Handler.__init__ = original_init  # type: ignore[assignment,arg-type]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["user"].name, self.admins[0].name)
        self.assertEqual(captured["user"].uuid, self.admins[0].uuid)
        self.assertEqual(captured["request_user"].name, self.admins[0].name)
        self.assertEqual(captured["request_user"].uuid, self.admins[0].uuid)
        principal = captured["principal"]
        self.assertEqual(principal.principal_kind, types.auth.PrincipalKind.USER)
        self.assertEqual(principal.credential_kind, types.auth.CredentialKind.SESSION)
        self.assertEqual(principal.user.uuid, self.admins[0].uuid)

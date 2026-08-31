"""Authentication resolution for REST handlers."""

import dataclasses
import collections.abc
import logging
import typing

from django.contrib.sessions.backends.db import SessionStore

from uds.core import consts, types
from uds.core.auths.auth import root_user
from uds.core.exceptions.rest import AccessDenied
from uds.core.util.config import GlobalConfig
from uds.models import Authenticator, Server, User
from uds.models.user import hash_api_token

if typing.TYPE_CHECKING:
    from uds.core.types.requests import ExtendedHttpRequest


logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class AuthenticationResult:
    """Resolved identity plus temporary compatibility state for ``Handler``."""

    principal: types.auth.AuthenticatedPrincipal
    session: SessionStore | None = None
    auth_token: str | None = dataclasses.field(default=None, repr=False)
    secret_token: str | None = dataclasses.field(default=None, repr=False)
    legacy_session: bool = False


class AuthenticationResolver:
    """Resolve current REST credentials without changing their semantics."""

    @staticmethod
    def resolve(
        request: "ExtendedHttpRequest",
        role: consts.Role,
        server_type: types.servers.ServerType | None,
    ) -> AuthenticationResult:
        """Resolve request credentials for a handler's current requirements."""
        auth_token, secret_token, user_api_token = AuthenticationResolver._extract_bearer(request)
        headers = typing.cast(collections.abc.Mapping[str, str], request.headers)
        legacy_token = headers.get(consts.auth.AUTH_TOKEN_HEADER, "")

        if role.needs_authentication:
            return AuthenticationResolver._resolve_user_session(
                role,
                auth_token,
                secret_token,
                legacy_token,
                user_api_token,
            )

        principal = types.auth.AuthenticatedPrincipal.anonymous()
        if server_type and secret_token:
            if not Server.validate_token(secret_token, server_type=server_type):
                raise AccessDenied()
            server = AuthenticationResolver._get_server(secret_token, server_type)
            if server is not None:
                principal = types.auth.AuthenticatedPrincipal.registered_server(
                    server,
                    credential_id=typing.cast(str, typing.cast(typing.Any, server).uuid),
                )

        return AuthenticationResult(principal=principal, secret_token=secret_token)

    @staticmethod
    def _extract_bearer(request: "ExtendedHttpRequest") -> tuple[str | None, str | None, str | None]:
        """Extract session, registered-server, and user API credentials."""
        auth_token: str | None = None
        secret_token: str | None = None
        user_api_token: str | None = None
        headers = typing.cast(collections.abc.Mapping[str, str], request.headers)
        auth_header = headers.get(consts.auth.AUTHORIZATION_HEADER, "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer ") :]
            if token.startswith(consts.auth.SECRET_KEY_PREFIX):
                secret_token = token.removeprefix(consts.auth.SECRET_KEY_PREFIX)
            elif token.startswith(consts.auth.USER_API_TOKEN_PREFIX):
                user_api_token = token
            elif token.startswith(consts.auth.SESSION_KEY_PREFIX):
                auth_token = token.removeprefix(consts.auth.SESSION_KEY_PREFIX)
            else:
                auth_token = token
        return auth_token, secret_token, user_api_token

    @staticmethod
    def _resolve_user_session(
        role: consts.Role,
        auth_token: str | None,
        secret_token: str | None,
        legacy_token: str,
        user_api_token: str | None,
    ) -> AuthenticationResult:
        """Resolve the session path used by authenticated handlers."""
        if user_api_token is not None:
            try:
                user = User.objects.select_related("manager").get(token_hash=hash_api_token(user_api_token))
            except User.DoesNotExist:
                raise AccessDenied() from None
            if not user.can_access(role):
                raise AccessDenied()
            return AuthenticationResult(
                principal=types.auth.AuthenticatedPrincipal.user_api_token(user),
                auth_token=user_api_token,
            )

        legacy_session = False
        auth_token = auth_token or legacy_token.removeprefix(consts.auth.SESSION_KEY_PREFIX)
        legacy_session = bool(auth_token and legacy_token and not secret_token)

        if secret_token is not None:
            # Preserve the current behavior: a secret key does not replace a
            # legacy session credential when both are sent to a user handler.
            auth_token = auth_token or legacy_token.removeprefix(consts.auth.SESSION_KEY_PREFIX)

        if secret_token is not None:
            logger.warning(
                "Both secret key and legacy auth token present in request; ignoring secret key for role %s",
                role,
            )

        try:
            session = SessionStore(session_key=auth_token)
            if "REST" not in session:
                raise AccessDenied()
        except Exception as exc:
            raise AccessDenied() from exc

        try:
            user = AuthenticationResolver._get_session_user(session)
        except Exception as exc:
            raise AccessDenied() from exc

        if not user.can_access(role):
            raise AccessDenied()

        principal = types.auth.AuthenticatedPrincipal.user_session(user)
        return AuthenticationResult(
            principal=principal,
            session=session,
            auth_token=auth_token,
            secret_token=secret_token,
            legacy_session=legacy_session,
        )

    @staticmethod
    def _get_session_user(session: SessionStore) -> User:
        """Load the user represented by the REST session payload."""
        rest_data = session["REST"]
        auth_id = rest_data.get("auth")
        username = rest_data.get("username")
        if (
            GlobalConfig.SUPER_USER_ALLOW_WEBACCESS.as_bool(True)
            and username == GlobalConfig.SUPER_USER_LOGIN.get(True)
            and auth_id == -1
        ):
            return root_user()
        authenticator = typing.cast(typing.Any, Authenticator).objects.get(pk=auth_id)
        return typing.cast(User, authenticator.users.get(name=username))

    @staticmethod
    def _get_server(token: str, server_type: types.servers.ServerType) -> Server | None:
        """Load the validated server for principal metadata."""
        try:
            return typing.cast(typing.Any, Server).objects.get(
                token_hash=Server.hash_token(token), type=server_type.value
            )
        except typing.cast(typing.Any, Server).DoesNotExist:
            return None

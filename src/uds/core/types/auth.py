#
# Copyright (c) 2023 Virtual Cable S.L.
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

import dataclasses
import enum
import typing

from django.urls import reverse

if typing.TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http.request import QueryDict

    from uds.models import Server, User


class AuthenticationState(enum.IntEnum):
    """
    Enumeration for authentication success
    """

    FAIL = 0
    SUCCESS = 1
    REDIRECT = 2


class AuthenticationInternalUrl(enum.StrEnum):
    """
    Enumeration for authentication success
    """

    LOGIN = "page.login"
    LOGIN_LABEL = "page.login.tag"
    LOGOUT = "page.logout"

    def get_url(self) -> str:
        """
        Returns the url for the given internal url
        """
        return reverse(self.value)


class AuthTypeGroup(enum.IntEnum):
    """
    Identifies the 'family' of authenticator for routing purposes.
    """

    GENERIC = 0
    CERTIFICATE = 1


@dataclasses.dataclass(frozen=True)
class AuthenticationResult:
    success: AuthenticationState
    url: str | None = None
    username: str | None = None


# Comodity values
FAILED_AUTH = AuthenticationResult(success=AuthenticationState.FAIL)
SUCCESS_AUTH = AuthenticationResult(success=AuthenticationState.SUCCESS)


@dataclasses.dataclass
class AuthCallbackParams:
    """Parameters passed to auth callback stage2

    This are the parameters that will be passes to the authenticator callback
    """

    https: bool
    host: str
    path: str
    port: str
    get_params: "QueryDict"
    post_params: "QueryDict"
    query_string: str
    binary_params: bytes | None = None

    @staticmethod
    def from_request(request: "HttpRequest", binary_data: bytes | None = None) -> "AuthCallbackParams":
        return AuthCallbackParams(
            https=request.is_secure(),
            host=request.META["HTTP_HOST"],
            path=request.META["PATH_INFO"],
            port=request.META["SERVER_PORT"],
            get_params=request.GET.copy(),
            post_params=request.POST.copy(),
            query_string=request.META["QUERY_STRING"],
            binary_params=binary_data,
        )


@dataclasses.dataclass
class LoginResult:
    user: "User | None" = None
    password: str = ""
    errstr: str | None = None
    errid: int = 0
    url: str | None = None


class PrincipalKind(enum.Enum):
    """Identity represented by an authenticated request."""

    USER = "USER"
    REGISTERED_SERVER = "REGISTERED_SERVER"
    ANONYMOUS = "ANONYMOUS"


class CredentialKind(enum.Enum):
    """Credential used to establish an authenticated identity."""

    SESSION = "SESSION"
    USER_API_TOKEN = "USER_API_TOKEN"
    CLIENT_TICKET = "CLIENT_TICKET"
    REGISTERED_SERVER = "REGISTERED_SERVER"
    ANONYMOUS = "ANONYMOUS"


@dataclasses.dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Authenticated identity without bearer secret material."""

    principal_kind: PrincipalKind
    credential_kind: CredentialKind
    user: "User | None" = None
    server: "Server | None" = None
    credential_id: str | None = None

    @staticmethod
    def anonymous() -> "AuthenticatedPrincipal":
        """Return the canonical ``ANONYMOUS`` principal."""
        return AuthenticatedPrincipal(
            principal_kind=PrincipalKind.ANONYMOUS,
            credential_kind=CredentialKind.ANONYMOUS,
        )

    @staticmethod
    def user_session(user: "User") -> "AuthenticatedPrincipal":
        """Return a principal for a user authenticated through a REST or web session."""
        return AuthenticatedPrincipal(
            principal_kind=PrincipalKind.USER,
            credential_kind=CredentialKind.SESSION,
            user=user,
        )

    @staticmethod
    def user_client_ticket(user: "User") -> "AuthenticatedPrincipal":
        """Return a principal for a user authenticated via a client ticket."""
        return AuthenticatedPrincipal(
            principal_kind=PrincipalKind.USER,
            credential_kind=CredentialKind.CLIENT_TICKET,
            user=user,
        )

    @staticmethod
    def registered_server(server: "Server", credential_id: str | None = None) -> "AuthenticatedPrincipal":
        """Return a principal for a registered M2M server credential."""
        return AuthenticatedPrincipal(
            principal_kind=PrincipalKind.REGISTERED_SERVER,
            credential_kind=CredentialKind.REGISTERED_SERVER,
            server=server,
            credential_id=credential_id,
        )


@dataclasses.dataclass
class SearchResultItem:
    class ItemDict(typing.TypedDict):
        id: str
        name: str

    id: str
    name: str

    def as_dict(self) -> "SearchResultItem.ItemDict":
        return typing.cast(SearchResultItem.ItemDict, dataclasses.asdict(self))

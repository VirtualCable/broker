#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
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
# FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
# OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

"""
Tests for the IPAuth/InternalDB 'Accept proxy' gating: the forwarded value
(request.ip_proxy) is only used for identity when the direct peer address
belongs to the trusted proxy networks configured on the authenticator
(default '*' keeps the pre-existing behavior).
"""

import typing
from types import SimpleNamespace

from uds import models
from uds.auths.InternalDB.authenticator import InternalDBAuth
from uds.auths.IP.authenticator import IPAuth
from uds.core import auths, types as core_types

from ...fixtures import authenticators as fixtures_authenticators
from ...utils.test import UDSTestCase

PEER = "1.2.3.4"
PROXY_REPORTED = "10.0.0.5"


def _request(ip: str, ip_proxy: str) -> "core_types.requests.ExtendedHttpRequest":
    # Minimal stand-in for the request attributes the identity-ip helper reads
    return typing.cast(
        "core_types.requests.ExtendedHttpRequest", SimpleNamespace(ip=ip, ip_proxy=ip_proxy)
    )


class IPAuthProxyGatingTest(UDSTestCase):
    peer: str = PEER
    proxy_reported: str = PROXY_REPORTED

    def _ip_auth(
        self,
        accepts_proxy: bool,
        trusted_proxies: str,
    ) -> IPAuth:
        authenticator = models.Authenticator()
        authenticator.name = "IP testing authenticator"
        authenticator.data_type = IPAuth.type_type
        authenticator.save()
        instance = typing.cast("IPAuth", authenticator.get_instance())
        instance.accepts_proxy.value = accepts_proxy
        instance.trusted_proxies.value = trusted_proxies
        authenticator.data = instance.serialize()
        authenticator.save()
        return typing.cast("IPAuth", authenticator.get_instance())

    def test_default_star_keeps_forwarded_value(self) -> None:
        # Backwards compatibility: default '*' trusts any peer
        auth = self._ip_auth(accepts_proxy=True, trusted_proxies="*")
        self.assertEqual(auth.get_ip(_request(PEER, PROXY_REPORTED)), PROXY_REPORTED)

    def test_restricted_networks_reject_foreign_peer(self) -> None:
        auth = self._ip_auth(accepts_proxy=True, trusted_proxies="10.0.0.0/8")
        # Peer is not a configured trusted proxy: use the peer address
        self.assertEqual(auth.get_ip(_request(PEER, PROXY_REPORTED)), PEER)

    def test_restricted_networks_accept_trusted_peer(self) -> None:
        auth = self._ip_auth(accepts_proxy=True, trusted_proxies="10.0.0.0/8")
        # Peer belongs to the trusted networks: forwarded value is used
        self.assertEqual(auth.get_ip(_request("10.9.9.9", PROXY_REPORTED)), PROXY_REPORTED)

    def test_accepts_proxy_disabled_uses_peer(self) -> None:
        auth = self._ip_auth(accepts_proxy=False, trusted_proxies="*")
        self.assertEqual(auth.get_ip(_request(PEER, PROXY_REPORTED)), PEER)

    def test_empty_trusted_networks_keep_forwarded_value(self) -> None:
        # Empty behaves like the old 'accept everything' mode
        auth = self._ip_auth(accepts_proxy=True, trusted_proxies="")
        self.assertEqual(auth.get_ip(_request(PEER, PROXY_REPORTED)), PROXY_REPORTED)

    def test_spoofed_identity_is_rejected_on_authenticate(self) -> None:
        # End to end: with restricted proxies, the XFF-supplied value can no
        # longer be used as the authenticating username
        auth = self._ip_auth(accepts_proxy=True, trusted_proxies="10.0.0.0/8")
        request = _request(PEER, PROXY_REPORTED)
        groups_manager = auths.GroupsManager(auth.db_obj())
        self.assertEqual(
            auth.authenticate(PROXY_REPORTED, "", groups_manager, request),
            core_types.auth.FAILED_AUTH,
        )
        self.assertEqual(
            auth.authenticate(PEER, "", groups_manager, request),
            core_types.auth.SUCCESS_AUTH,
        )


class InternalDBProxyGatingTest(UDSTestCase):
    def _internaldb_auth(
        self,
        accepts_proxy: bool,
        trusted_proxies: str,
        unique_by_host: bool,
    ) -> InternalDBAuth:
        authenticator = fixtures_authenticators.create_db_authenticator()
        instance = typing.cast("InternalDBAuth", authenticator.get_instance())
        instance.accepts_proxy.value = accepts_proxy
        instance.trusted_proxies.value = trusted_proxies
        instance.unique_by_host.value = unique_by_host
        authenticator.data = instance.serialize()
        authenticator.save()
        return typing.cast("InternalDBAuth", authenticator.get_instance())

    def test_transformed_username_uses_peer_when_peer_not_trusted(self) -> None:
        authenticator = self._internaldb_auth(
            accepts_proxy=True, trusted_proxies="10.0.0.0/8", unique_by_host=True
        )
        users = fixtures_authenticators.create_db_users(authenticator.db_obj(), number_of_users=1)
        username = users[0].name
        result = authenticator.transformed_username(username, _request(PEER, PROXY_REPORTED))
        self.assertEqual(result, f"{PEER}-{username}")

    def test_transformed_username_uses_forwarded_when_peer_trusted(self) -> None:
        authenticator = self._internaldb_auth(
            accepts_proxy=True, trusted_proxies="10.0.0.0/8", unique_by_host=True
        )
        users = fixtures_authenticators.create_db_users(authenticator.db_obj(), number_of_users=1)
        username = users[0].name
        result = authenticator.transformed_username(username, _request("10.9.9.9", PROXY_REPORTED))
        self.assertEqual(result, f"{PROXY_REPORTED}-{username}")

    def test_get_ip_forwarding_disabled(self) -> None:
        authenticator = self._internaldb_auth(accepts_proxy=False, trusted_proxies="*", unique_by_host=False)
        self.assertEqual(authenticator.get_ip(_request(PEER, PROXY_REPORTED)), PEER)

    def test_get_ip_forwarding_default_star(self) -> None:
        authenticator = self._internaldb_auth(accepts_proxy=True, trusted_proxies="*", unique_by_host=False)
        self.assertEqual(authenticator.get_ip(_request(PEER, PROXY_REPORTED)), PROXY_REPORTED)

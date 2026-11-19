#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Author: Andres Schumann, aschumann at virtualcable dot es

When the trusted sources list is empty or has no valid network, no request is from a trusted source,
and a check that cannot be evaluated denies the request.
"""

from unittest import mock

from django.http import HttpResponseForbidden, HttpResponse

from uds.core.auths import auth
from uds.core.util.config import GlobalConfig

from tests.utils.test import UDSTestCase

CLIENT_IP: str = "10.1.2.3"


class _Request:
    def __init__(self, ip: str) -> None:
        self.ip = ip


class TrustedSourcesTest(UDSTestCase):
    def tearDown(self) -> None:
        GlobalConfig.TRUSTED_SOURCES.set("*")
        super().tearDown()

    def test_star_trusts_everyone(self) -> None:
        GlobalConfig.TRUSTED_SOURCES.set("*")
        self.assertTrue(auth.is_trusted_source(CLIENT_IP))

    def test_empty_list_trusts_nobody(self) -> None:
        GlobalConfig.TRUSTED_SOURCES.set("")
        self.assertFalse(auth.is_trusted_source(CLIENT_IP))

    def test_list_without_valid_network_trusts_nobody(self) -> None:
        GlobalConfig.TRUSTED_SOURCES.set("not-a-network, 10.0.0.0/99")
        self.assertFalse(auth.is_trusted_source(CLIENT_IP))

    def test_list_with_the_client_network_trusts_it(self) -> None:
        GlobalConfig.TRUSTED_SOURCES.set("10.0.0.0/8")
        self.assertTrue(auth.is_trusted_source(CLIENT_IP))

    def test_decorator_denies_when_the_check_cannot_be_evaluated(self) -> None:
        view = mock.MagicMock(return_value=HttpResponse("served"))
        wrapped = auth.needs_trusted_source(view)
        with mock.patch.object(auth, "is_trusted_source", side_effect=RuntimeError("broken list")):
            response = wrapped(_Request(CLIENT_IP))  # type: ignore[arg-type]
        self.assertIsInstance(response, HttpResponseForbidden)
        view.assert_not_called()

    def test_decorator_serves_trusted_clients(self) -> None:
        GlobalConfig.TRUSTED_SOURCES.set("10.0.0.0/8")
        view = mock.MagicMock(return_value=HttpResponse("served"))
        wrapped = auth.needs_trusted_source(view)
        response = wrapped(_Request(CLIENT_IP))  # type: ignore[arg-type]
        self.assertEqual(response.content, b"served")
        view.assert_called_once()

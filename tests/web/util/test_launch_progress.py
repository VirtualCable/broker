#
# Copyright (c) 2026 Virtual Cable S.L.U.
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
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
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
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import json
import typing
from unittest import mock

from tests.utils.test import UDSTransactionTestCase
from uds.core import types
from uds.core.exceptions.services import MaxServicesReachedError, ServiceNotReadyError
from uds.web.util import services as services_util
from uds.web.views import service as service_views


def _make_request() -> mock.Mock:
    request = mock.Mock()
    request.method = "POST"
    request.user = mock.Mock()
    request.os = mock.Mock()
    request.ip = "127.0.0.1"
    return request


def _not_ready_error(code: types.services.ReadyStatus) -> ServiceNotReadyError:
    return ServiceNotReadyError(code=code)


class TestNotReadyPercent(UDSTransactionTestCase):
    """Percent and message extracted from each readiness stage."""

    def test_codes_map_to_their_percent(self) -> None:
        self.assertEqual(
            service_views._service_not_ready_percent(types.services.ReadyStatus.USERSERVICE_NOT_READY)[0], 25
        )
        self.assertEqual(
            service_views._service_not_ready_percent(types.services.ReadyStatus.USERSERVICE_NO_IP)[0], 50
        )
        self.assertEqual(
            service_views._service_not_ready_percent(types.services.ReadyStatus.TRANSPORT_NOT_READY)[0], 75
        )

    def test_stages_never_finished_have_message(self) -> None:
        for code in types.services.ReadyStatus:
            _percent, message = service_views._service_not_ready_percent(code)
            self.assertTrue(message, f"{code} must report a message")

    def test_invalid_uuid_is_not_reported_as_ready(self) -> None:
        # 100 would mean "service ready" to the progress bar, impossible for
        # a not-ready launch, so it must fall back to 0.
        percent, _message = service_views._service_not_ready_percent(
            types.services.ReadyStatus.USERSERVICE_INVALID_UUID
        )
        self.assertEqual(percent, 0)


class TestEnableServiceRetryable(UDSTransactionTestCase):
    """The enabler must flag which "not ready" errors can be retried."""

    def test_not_ready_is_retryable(self) -> None:
        for code in (
            types.services.ReadyStatus.USERSERVICE_NOT_READY,
            types.services.ReadyStatus.USERSERVICE_NO_IP,
            types.services.ReadyStatus.TRANSPORT_NOT_READY,
        ):
            with mock.patch("uds.web.util.services.UserServiceManager") as manager:
                manager.manager.return_value.get_user_service_info.side_effect = _not_ready_error(code)
                data = services_util.enable_service(_make_request(), "A00000000", "T00000000")
                self.assertTrue(data["retryable"], code)
                self.assertEqual(data["percent"], code.as_percent())
                self.assertEqual(data["url"], "")
                self.assertNotEqual(data["error"], "")

    def test_invalid_uuid_is_not_retryable(self) -> None:
        with mock.patch("uds.web.util.services.UserServiceManager") as manager:
            manager.manager.return_value.get_user_service_info.side_effect = _not_ready_error(
                types.services.ReadyStatus.USERSERVICE_INVALID_UUID
            )
            data = services_util.enable_service(_make_request(), "A00000000", "T00000000")
            self.assertFalse(data["retryable"])

    def test_other_errors_are_not_retryable(self) -> None:
        with mock.patch("uds.web.util.services.UserServiceManager") as manager:
            manager.manager.return_value.get_user_service_info.side_effect = MaxServicesReachedError()
            data = services_util.enable_service(_make_request(), "A00000000", "T00000000")
            self.assertFalse(data["retryable"])
            self.assertEqual(data["percent"], 0)


class TestTransportOwnLinkProgress(UDSTransactionTestCase):
    """Percent for the HTML/external transport polling loop."""

    def test_not_ready_percent_is_reported_not_ready_status_code(self) -> None:
        with mock.patch("uds.web.views.service.UserServiceManager") as manager:
            manager.manager.return_value.get_user_service_info.side_effect = _not_ready_error(
                types.services.ReadyStatus.USERSERVICE_NO_IP
            )
            response = service_views.transport_own_link(_make_request(), "A00000000", "T00000000")
        data = typing.cast(dict[str, typing.Any], json.loads(typing.cast(bytes, response.content)))
        # Percent must be 0-100, not the raw ReadyStatus code (2)
        self.assertEqual(data["running"], 50)
        self.assertEqual(data["url"], "")
        self.assertEqual(data["error"], "")
        self.assertTrue(data["message"])

    def test_ready_reports_100(self) -> None:
        with mock.patch("uds.web.views.service.UserServiceManager") as manager:
            manager.manager.return_value.get_user_service_info.return_value.ip = "192.168.1.5"
            manager.manager.return_value.get_user_service_info.return_value.transport.get_instance.return_value.get_link.return_value = "https://www.example.net/some/link"
            response = service_views.transport_own_link(_make_request(), "A00000000", "T00000000")
        data = typing.cast(dict[str, typing.Any], json.loads(typing.cast(bytes, response.content)))
        self.assertEqual(data["running"], 100)
        self.assertEqual(data["url"], "https://www.example.net/some/link")
        self.assertEqual(data["error"], "")


class TestServiceStatusProgress(UDSTransactionTestCase):
    """The status polling endpoint answers percent and message."""

    @staticmethod
    def _view(userservice: mock.Mock | None) -> dict[str, typing.Any]:
        request = mock.Mock()
        request.user = mock.Mock()
        request.ip = "127.0.0.1"
        with mock.patch("uds.web.views.service.UserServiceManager") as manager:
            manager.manager.return_value.is_meta_service.return_value = False
            manager.manager.return_value.locate_user_service.return_value = userservice
            response = service_views.user_service_status(request, "A00000000", "T00000000")
        return typing.cast(dict[str, typing.Any], json.loads(typing.cast(bytes, response.content)))

    def test_no_user_service_still_reports_being_created(self) -> None:
        data = self._view(None)
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["percent"], 0)
        self.assertTrue(data["message"])

    def test_not_ready_reports_stage_percent(self) -> None:
        userservice = mock.Mock()
        userservice.get_instance.return_value.get_ip.side_effect = _not_ready_error(
            types.services.ReadyStatus.TRANSPORT_NOT_READY
        )
        userservice.properties.get.return_value = False
        data = self._view(userservice)
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["percent"], 75)
        self.assertTrue(data["message"])

    def test_ready_reports_100(self) -> None:
        userservice = mock.Mock()
        userservice.get_instance.return_value.get_ip.return_value = "192.168.1.5"
        userservice.properties.get.return_value = False
        data = self._view(userservice)
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["percent"], 100)

    def test_accessed_reports_100(self) -> None:
        userservice = mock.Mock()
        userservice.get_instance.return_value.get_ip.return_value = "192.168.1.5"
        userservice.properties.get.return_value = True
        data = self._view(userservice)
        self.assertEqual(data["status"], "accessed")
        self.assertEqual(data["percent"], 100)

    def test_failing_service_reports_error(self) -> None:
        userservice = mock.Mock()
        userservice.get_instance.return_value.get_ip.side_effect = ValueError("no ip, just boom")
        userservice.properties.get.return_value = False
        data = self._view(userservice)
        self.assertEqual(data["status"], "error")

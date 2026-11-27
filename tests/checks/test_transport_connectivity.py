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

"""
Author: Andres Schumann, aschumann at virtualcable dot es

Tests for the transport connectivity check disabled check (uds.checks.models).
"""

import typing
from unittest import mock

from uds import models
from uds.checks.models import TransportConnectivityCheckDisabledCheck
from uds.core import transports, types
from uds.core.checks import runner as runner_module
from uds.core.ui import UserInterface, gui
from uds.transports.HTML5RDP.html5rdp import HTML5RDPTransport
from uds.transports.HTML5SSH.html5ssh import HTML5SSHTransport
from uds.transports.RDP.rdp import RDPTransport
from uds.transports.RDP.rdptunnel import TRDPTransport
from uds.transports.RDPEmbedded.direct import RDPEmbeddedTransport
from uds.transports.RDPEmbedded.tunnel import TRDPEmbeddedTransport
from uds.transports.SPICE.spice import SPICETransport
from uds.transports.SPICE.spicetunnel import TSPICETransport
from uds.transports.UdsRP.direct import UdsRPTransport
from uds.transports.UdsRP.tunnel import TUdsRPTransport
from uds.transports.URL.url_custom import URLCustomTransport
from uds.transports.X2GO.x2go import X2GOTransport
from uds.transports.X2GO.x2gotunnel import TX2GOTransport

from ..utils.test import UDSTransactionTestCase

CHECK_ID: typing.Final[str] = "transport-connectivity-check-disabled"

# Every concrete transport that exposes the connectivity switch. Each one must
# re-export the field, or the check cannot see its value.
SWITCH_TRANSPORTS: typing.Final[tuple[type[transports.Transport], ...]] = (
    HTML5RDPTransport,
    HTML5SSHTransport,
    RDPTransport,
    TRDPTransport,
    RDPEmbeddedTransport,
    TRDPEmbeddedTransport,
    SPICETransport,
    TSPICETransport,
    UdsRPTransport,
    TUdsRPTransport,
    X2GOTransport,
    TX2GOTransport,
)


class TransportConnectivityCheckDisabledTest(UDSTransactionTestCase):
    def _run_check(self) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)}
        return results[CHECK_ID]

    def _create(self, name: str, transport_type: type[transports.Transport], check_disabled: bool) -> str:
        transport = models.Transport(name=name, data_type=transport_type.type_type)
        transport.save()
        instance = transport.get_instance()
        if check_disabled:
            switch = instance.allow_ip_check
            self.assertIsInstance(switch, gui.CheckBoxField)
            typing.cast(gui.CheckBoxField, switch).value = False
        transport.data = instance.serialize()
        transport.save()
        return str(transport.uuid)

    def test_passes_without_transports(self) -> None:
        result = self._run_check()
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.severity, types.checks.CheckSeverity.MEDIUM)
        self.assertEqual(result.category, types.checks.CheckCategory.SECURITY)

    def test_passes_when_the_check_is_enabled(self) -> None:
        self._create("Checked RDP", RDPTransport, check_disabled=False)
        self.assertTrue(self._run_check().ok)

    def test_medium_when_a_transport_has_the_check_disabled(self) -> None:
        uuid = self._create("Unchecked RDP", RDPTransport, check_disabled=True)
        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.checks.CheckSeverity.MEDIUM)
        self.assertIn("Unchecked RDP", result.message)
        self.assertEqual(result.details, (f"Unchecked RDP ({uuid})",))

    def test_lists_only_the_transports_with_the_check_disabled(self) -> None:
        self._create("Checked RDP", RDPTransport, check_disabled=False)
        uuid = self._create("Unchecked RDP", RDPTransport, check_disabled=True)
        self.assertEqual(self._run_check().details, (f"Unchecked RDP ({uuid})",))

    def test_every_transport_family_with_the_switch_is_flagged(self) -> None:
        expected: set[str] = set()
        for transport_type in SWITCH_TRANSPORTS:
            uuid = self._create(transport_type.__name__, transport_type, check_disabled=True)
            expected.add(f"{transport_type.__name__} ({uuid})")
        self.assertEqual(set(self._run_check().details), expected)

    def test_transports_without_the_switch_are_not_flagged(self) -> None:
        self._create("URL", URLCustomTransport, check_disabled=False)
        self.assertTrue(self._run_check().ok)

    def test_the_check_never_saves_the_transports(self) -> None:
        self._create("Unchecked RDP", RDPTransport, check_disabled=True)
        with (
            mock.patch.object(UserInterface, "deserialize_fields", return_value=True),
            mock.patch.object(models.Transport, "save") as save,
        ):
            TransportConnectivityCheckDisabledCheck().run()
        save.assert_not_called()

    def test_transports_whose_type_is_missing_are_not_saved(self) -> None:
        self._create("Orphan", RDPTransport, check_disabled=True)
        models.Transport.objects.filter(name="Orphan").update(data_type="NoSuchTransport")
        with (
            mock.patch.object(UserInterface, "deserialize_fields", return_value=True),
            mock.patch.object(models.Transport, "save") as save,
        ):
            outcome = TransportConnectivityCheckDisabledCheck().run()
        save.assert_not_called()
        self.assertTrue(outcome[1], outcome[2])

    def test_every_transport_family_warns_about_degraded_mode_in_the_switch_tooltip(self) -> None:
        for transport_type in SWITCH_TRANSPORTS:
            with self.subTest(transport=transport_type.__name__):
                switch = transport_type._gui_fields_template["allow_ip_check"]
                self.assertIn("degrades the system", str(switch._field_info.tooltip))

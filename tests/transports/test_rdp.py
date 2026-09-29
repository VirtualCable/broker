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
"""

from tests.utils.test import UDSTestCase
from uds.core import types
from uds.transports.RDP.rdp import RDPTransport
from uds.transports.RDP.rdp_file import RDPFile
from uds.transports.RDP.rdptunnel import TRDPTransport


class RDPTransportAudioMicTest(UDSTestCase):
    def test_default_fields(self) -> None:
        direct = RDPTransport(self.create_environment(), None)
        tunnel = TRDPTransport(self.create_environment(), None)

        self.assertTrue(direct.allow_audio.as_bool())
        self.assertFalse(direct.allow_microphone.as_bool())

        self.assertTrue(tunnel.allow_audio.as_bool())
        self.assertFalse(tunnel.allow_microphone.as_bool())

    def test_rdp_file_audio_on_mic_off(self) -> None:
        rdp = RDPFile(fullscreen=False, width=1024, height=768, bpp="24", target=types.os.KnownOS.WINDOWS)
        rdp.redir_audio = True
        rdp.redir_microphone = False

        mstsc = rdp.as_mstsc_file
        self.assertIn("audiomode:i:0", mstsc)
        self.assertNotIn("audiocapturemode", mstsc)

        rdp_linux = RDPFile(fullscreen=False, width=1024, height=768, bpp="24", target=types.os.KnownOS.LINUX)
        rdp_linux.redir_audio = True
        rdp_linux.redir_microphone = False
        rdp_linux.alsa = True
        params = rdp_linux.freerdp_params
        self.assertTrue(any(p.startswith("/sound") for p in params))
        self.assertFalse(any(p.startswith("/microphone") for p in params))

        url = rdp.as_rdp_url
        self.assertIn("audiomode=i:0", url)
        self.assertNotIn("audiocapturemode", url)

    def test_rdp_file_audio_on_mic_on(self) -> None:
        rdp = RDPFile(fullscreen=False, width=1024, height=768, bpp="24", target=types.os.KnownOS.WINDOWS)
        rdp.redir_audio = True
        rdp.redir_microphone = True

        mstsc = rdp.as_mstsc_file
        self.assertIn("audiomode:i:0", mstsc)
        self.assertIn("audiocapturemode:i:1", mstsc)

        rdp_linux = RDPFile(fullscreen=False, width=1024, height=768, bpp="24", target=types.os.KnownOS.LINUX)
        rdp_linux.redir_audio = True
        rdp_linux.redir_microphone = True
        rdp_linux.alsa = True
        params = rdp_linux.freerdp_params
        self.assertTrue(any(p.startswith("/sound") for p in params))
        self.assertTrue(any(p.startswith("/microphone") for p in params))

        url = rdp.as_rdp_url
        self.assertIn("audiomode=i:0", url)
        self.assertIn("audiocapturemode=i:1", url)

    def test_rdp_file_audio_off_mic_on(self) -> None:
        rdp = RDPFile(fullscreen=False, width=1024, height=768, bpp="24", target=types.os.KnownOS.WINDOWS)
        rdp.redir_audio = False
        rdp.redir_microphone = True

        mstsc = rdp.as_mstsc_file
        self.assertIn("audiomode:i:2", mstsc)
        self.assertIn("audiocapturemode:i:1", mstsc)

        rdp_linux = RDPFile(fullscreen=False, width=1024, height=768, bpp="24", target=types.os.KnownOS.LINUX)
        rdp_linux.redir_audio = False
        rdp_linux.redir_microphone = True
        rdp_linux.alsa = True
        params = rdp_linux.freerdp_params
        self.assertFalse(any(p.startswith("/sound") for p in params))
        self.assertTrue(any(p.startswith("/microphone") for p in params))

    def test_rdp_file_audio_off_mic_off(self) -> None:
        rdp = RDPFile(fullscreen=False, width=1024, height=768, bpp="24", target=types.os.KnownOS.WINDOWS)
        rdp.redir_audio = False
        rdp.redir_microphone = False

        mstsc = rdp.as_mstsc_file
        self.assertIn("audiomode:i:2", mstsc)
        self.assertNotIn("audiocapturemode", mstsc)

        rdp_linux = RDPFile(fullscreen=False, width=1024, height=768, bpp="24", target=types.os.KnownOS.LINUX)
        rdp_linux.redir_audio = False
        rdp_linux.redir_microphone = False
        params = rdp_linux.freerdp_params
        self.assertFalse(any(p.startswith("/sound") for p in params))
        self.assertFalse(any(p.startswith("/microphone") for p in params))

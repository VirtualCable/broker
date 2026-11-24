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

import importlib
from unittest import mock

from django.conf import LazySettings, global_settings, settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory

from uds.core.util import session_samesite
from uds.core.util.config import SESSION_COOKIE_SAMESITE_DEFAULT, GlobalConfig
from uds.models.config import Config as DBConfig

from ...utils.test import UDSTestCase


class SessionSameSiteTest(UDSTestCase):
    def setUp(self) -> None:
        super().setUp()
        GlobalConfig.SESSION_COOKIE_SAMESITE.get(force=True)
        session_samesite.install()

    def tearDown(self) -> None:
        session_samesite.install()
        super().tearDown()

    def session_cookie_samesite(self) -> str:
        request = RequestFactory().get("/")
        engine = importlib.import_module(settings.SESSION_ENGINE)
        request.session = engine.SessionStore()
        request.session["probe"] = "value"
        middleware = SessionMiddleware(lambda _: HttpResponse())
        response = middleware.process_response(request, HttpResponse())
        return str(response.cookies[settings.SESSION_COOKIE_NAME]["samesite"])

    def test_default_is_lax_when_not_saved(self) -> None:
        self.assertEqual(SESSION_COOKIE_SAMESITE_DEFAULT, "Lax")
        DBConfig.objects.filter(
            section=GlobalConfig.SESSION_COOKIE_SAMESITE.section(),
            key=GlobalConfig.SESSION_COOKIE_SAMESITE.key(),
        ).delete()

        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(self.session_cookie_samesite(), "Lax")

    def test_value_saved_from_web_is_applied_to_session_cookie(self) -> None:
        for value in ("Lax", "Strict"):
            with self.subTest(value=value):
                GlobalConfig.SESSION_COOKIE_SAMESITE.set(value)

                self.assertEqual(settings.SESSION_COOKIE_SAMESITE, value)
                self.assertEqual(self.session_cookie_samesite(), value)

    def test_value_is_cached_until_restart(self) -> None:
        GlobalConfig.SESSION_COOKIE_SAMESITE.set("Strict")
        DBConfig.objects.filter(
            section=GlobalConfig.SESSION_COOKIE_SAMESITE.section(),
            key=GlobalConfig.SESSION_COOKIE_SAMESITE.key(),
        ).update(value="Lax")

        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Strict")
        GlobalConfig.SESSION_COOKIE_SAMESITE.get(force=True)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")

    def test_invalid_value_falls_back_to_default(self) -> None:
        GlobalConfig.SESSION_COOKIE_SAMESITE.set("None")

        with self.assertLogs(session_samesite.logger, level="WARNING"):
            value = settings.SESSION_COOKIE_SAMESITE

        self.assertEqual(value, SESSION_COOKIE_SAMESITE_DEFAULT)

    def test_verification_detects_setting_shadowed_in_cache(self) -> None:
        GlobalConfig.SESSION_COOKIE_SAMESITE.set("Strict")
        self.assertTrue(session_samesite._verify())

        settings.__dict__[session_samesite.SETTING_NAME] = "Lax"
        try:
            self.assertFalse(session_samesite._verify())
        finally:
            del settings.__dict__[session_samesite.SETTING_NAME]

    def test_failed_verification_reverts_to_django_default(self) -> None:
        django_getattr = session_samesite._django_getattr
        self.assertIsNotNone(django_getattr)
        session_samesite.uninstall()
        try:
            with mock.patch.object(session_samesite, "_verify", return_value=False):
                session_samesite.install()

            self.assertIs(LazySettings.__getattr__, django_getattr)
            self.assertEqual(settings.SESSION_COOKIE_SAMESITE, global_settings.SESSION_COOKIE_SAMESITE)
        finally:
            session_samesite.install()

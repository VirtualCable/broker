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
Contracts for uds.web.views.custom.

The view serves two distinct resources:

1. ``styles.css`` — the global SITE_CSS from config, applied to every login page.
2. ``<tag>.css`` — the per-authenticator override, looked up from
   ``settings.STATICFILES_DIRS``. Missing file returns an empty 200, never a 404,
   so the browser drops the stylesheet silently and the bundle defaults win.
"""

import os
import tempfile

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from uds.web.views import custom as custom_views


def _client():
    from django.test import Client

    return Client(enforce_csrf_checks=False, secure=True)


class _BaseCustomViewTest(SimpleTestCase):
    databases = {'default'}

    def setUp(self) -> None:
        super().setUp()
        self.old_middleware = settings.MIDDLEWARE[:]
        settings.MIDDLEWARE = [
            m for m in settings.MIDDLEWARE if 'RedirectMiddleware' not in m
        ]

    def tearDown(self) -> None:
        settings.MIDDLEWARE = self.old_middleware
        super().tearDown()


class GlobalStylesCssTest(_BaseCustomViewTest):
    def test_returns_empty_when_site_css_is_unset(self) -> None:
        with override_settings(GLOBAL_CONFIG={}, APPEND_SLASH=False):
            response = _client().get('/uds/custom/styles.css')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'text/css')
            self.assertEqual(response.content, b'')


class PerTagCssTest(_BaseCustomViewTest):
    def test_returns_file_contents_from_staticfiles_dirs(self) -> None:
        tag_dir = tempfile.mkdtemp()
        tag_file = os.path.join(tag_dir, 'int_ad.css')
        with open(tag_file, 'w', encoding='utf-8') as f:
            f.write('.login-card { background: red; }')

        try:
            with override_settings(
                STATICFILES_DIRS=[tag_dir],
                APPEND_SLASH=False,
            ):
                response = _client().get('/uds/custom/int_ad.css')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'text/css')
                self.assertEqual(response.content, b'.login-card { background: red; }')
        finally:
            os.unlink(tag_file)
            os.rmdir(tag_dir)

    def test_uds_res_tag_css_url_resolves_and_serves(self) -> None:
        # The frontend (gui/user index.html) loads /uds/res/<tag>.css directly,
        # so that URL must route to custom.custom just like /uds/custom/<tag>.css.
        tag_dir = tempfile.mkdtemp()
        tag_file = os.path.join(tag_dir, 'int-azure_ad.css')
        with open(tag_file, 'w', encoding='utf-8') as f:
            f.write('.login-brand { opacity: 0.8; }')

        try:
            with override_settings(
                STATICFILES_DIRS=[tag_dir],
                APPEND_SLASH=False,
            ):
                response = _client().get('/uds/res/int-azure_ad.css')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'text/css')
                self.assertEqual(response.content, b'.login-brand { opacity: 0.8; }')
        finally:
            os.unlink(tag_file)
            os.rmdir(tag_dir)

    def test_missing_file_returns_empty_200_not_404(self) -> None:
        empty_dir = tempfile.mkdtemp()
        try:
            with override_settings(
                STATICFILES_DIRS=[empty_dir],
                APPEND_SLASH=False,
            ):
                response = _client().get('/uds/custom/int.css')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'text/css')
                self.assertEqual(response.content, b'')
        finally:
            os.rmdir(empty_dir)

    def test_rejects_path_traversal_attempt(self) -> None:
        for bad in ('../etc/passwd', 'a/b.css', 'a.b.c.css', '.hidden.css', ''):
            with self.subTest(component=bad):
                if bad == '':
                    self.assertFalse(custom_views._TAG_CSS_NAME_RE.match(bad))
                    continue
                self.assertIsNone(custom_views._TAG_CSS_NAME_RE.match(bad))

    def test_rejects_components_with_uppercase_or_extension_tricks(self) -> None:
        self.assertIsNotNone(custom_views._TAG_CSS_NAME_RE.match('int.css'))
        self.assertIsNotNone(custom_views._TAG_CSS_NAME_RE.match('int-2.css'))
        self.assertIsNotNone(custom_views._TAG_CSS_NAME_RE.match('int_ad.css'))
        self.assertIsNotNone(custom_views._TAG_CSS_NAME_RE.match('client1.css'))

        self.assertIsNone(custom_views._TAG_CSS_NAME_RE.match('int.CSS'))
        self.assertIsNone(custom_views._TAG_CSS_NAME_RE.match('.css'))
        self.assertIsNone(custom_views._TAG_CSS_NAME_RE.match('int.css.bak'))
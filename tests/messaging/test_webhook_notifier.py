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
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import dataclasses
import hashlib
import hmac as hmac_module
import http.server
import json
import threading
import time
import typing
from unittest import mock

from uds.core import exceptions, messaging
from uds.core.environment import Environment
from uds.core.types.notifiers import NotificationGroup
from uds.core.util.backoff import Backoff
from uds.models import Notifier
from uds.notifiers.webhook import queue
from uds.notifiers.webhook.jobs import WebhookDispatcherJob
from uds.notifiers.webhook.notifier import WebhookNotifier

from ..fixtures import notifiers as notifiers_fixtures
from ..utils.test import UDSTestCase


def _instance(notifier: Notifier) -> WebhookNotifier:
    return typing.cast(WebhookNotifier, notifier.get_instance())


class _Handler(http.server.BaseHTTPRequestHandler):
    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        server: _TestHttpServer = typing.cast("_TestHttpServer", self.server)
        status = server.respond.pop(0) if server.respond else 200
        server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body,
            }
        )
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def log_message(self, format: str, *args: typing.Any) -> None:
        pass  # Silence request logging


class _TestHttpServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[dict[str, typing.Any]] = []
        self.respond: list[int] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


class WebhookRenderingTest(UDSTestCase):
    """
    Tests for template rendering, HMAC computation and validations
    """

    def test_default_body_is_valid_json(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(url="http://example.com/hook")
        instance = _instance(notifier)
        element = instance._build_request(
            NotificationGroup.EVENT, "ID1", messaging.LogLevel.ERROR, 'He said "hi"\nbye'
        )
        payload = json.loads(element.body)
        self.assertEqual(payload["group"], "event")
        self.assertEqual(payload["identificator"], "ID1")
        self.assertEqual(payload["level"], "ERROR")
        self.assertEqual(payload["message"], 'He said "hi"\nbye')
        self.assertIn("timestamp", payload)
        self.assertEqual(element.method, "POST")
        self.assertEqual(element.headers["Content-Type"], "application/json")

    def test_template_substitution_is_json_safe(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(
            url="http://example.com/hook",
            dataTemplate='{"msg": "{{message}}", "id": "{{identificator}}"}',
        )
        instance = _instance(notifier)
        element = instance._build_request(
            NotificationGroup.EVENT, "ID1", messaging.LogLevel.INFO, 'value with "quotes" and\nnewlines'
        )
        payload = json.loads(element.body)
        self.assertEqual(payload["msg"], 'value with "quotes" and\nnewlines')
        self.assertEqual(payload["id"], "ID1")

    def test_content_mode_hmac_and_auto_signature(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(url="http://example.com/hook", hmacSecret="secret")
        instance = _instance(notifier)
        element = instance._build_request(NotificationGroup.EVENT, "ID1", messaging.LogLevel.ERROR, "hello")
        payload = json.loads(element.body)
        values = {
            "group": payload["group"],
            "identificator": payload["identificator"],
            "level": payload["level"],
            "message": payload["message"],
            "timestamp": payload["timestamp"],
        }
        expected = hmac_module.new(
            b"secret",
            "\n".join(f"{k}={v}" for k, v in sorted(values.items())).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(element.headers["X-UDS-Signature"], expected)

    def test_hmac_on_headers_disables_auto_signature(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(
            url="http://example.com/hook",
            hmacSecret="secret",
            headers="X-My-Signature: v1={{hmac}}\nX-Static: ok",
        )
        instance = _instance(notifier)
        element = instance._build_request(NotificationGroup.EVENT, "ID1", messaging.LogLevel.ERROR, "hello")
        self.assertNotIn("X-UDS-Signature", element.headers)
        self.assertIn("X-My-Signature", element.headers)
        self.assertTrue(element.headers["X-My-Signature"].startswith("v1="))
        self.assertEqual(len(element.headers["X-My-Signature"]), 3 + 64)
        self.assertEqual(element.headers["X-Static"], "ok")

    def test_no_secret_hmac_resolves_empty(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(
            url="http://example.com/hook", dataTemplate="{{hmac}}"
        )
        instance = _instance(notifier)
        element = instance._build_request(NotificationGroup.EVENT, "ID1", messaging.LogLevel.INFO, "hello")
        self.assertEqual(element.body, "")

    def test_body_mode_hmac_signs_body(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(
            url="http://example.com/hook",
            hmacSecret="secret",
            hmacMode="body",
            headers="X-Sig: {{hmac}}",
        )
        instance = _instance(notifier)
        element = instance._build_request(NotificationGroup.EVENT, "ID1", messaging.LogLevel.INFO, "hello")
        expected = hmac_module.new(b"secret", element.body.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(element.headers["X-Sig"], expected)

    def test_get_joins_lines(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(
            url="http://example.com/hook",
            verb="GET",
            dataTemplate="line1\n{{message}}\nline3",
        )
        instance = _instance(notifier)
        element = instance._build_request(NotificationGroup.EVENT, "ID1", messaging.LogLevel.INFO, "hello")
        self.assertNotIn("\n", element.body)
        self.assertEqual(element.body, "line1 hello line3")
        self.assertEqual(element.method, "GET")

    def test_body_mode_with_hmac_on_template_is_invalid(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(
            url="http://example.com/hook", hmacSecret="secret", hmacMode="body"
        )
        instance = _instance(notifier)
        instance.data_template.value = "{{hmac}}"
        with self.assertRaises(exceptions.ui.ValidationError):
            instance.initialize({"any": "value"})

    def test_header_line_without_separator_is_invalid(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(url="http://example.com/hook")
        instance = _instance(notifier)
        instance.headers.value = "This line has no separator"
        with self.assertRaises(exceptions.ui.ValidationError):
            instance.initialize({"any": "value"})

    def test_invalid_url_is_invalid(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(url="http://example.com/hook")
        instance = _instance(notifier)
        instance.url.value = "not an url at all"
        with self.assertRaises(Exception):
            instance.initialize({"any": "value"})


class WebhookDispatcherJobTest(UDSTestCase):
    """
    Tests for the dispatcher job against a local HTTP server
    """

    def setUp(self) -> None:
        self.server = _TestHttpServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.job = WebhookDispatcherJob(Environment.testing_environment())

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _backoff(self, notifier_uuid: str, cap: int = 300) -> Backoff:
        return Backoff(owner=queue.QUEUE_OWNER, fail_time=5, max_time=cap)

    def test_delivery_in_fifo_order(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(url=f"{self.server.base_url}/hook")
        instance = _instance(notifier)
        instance.notify(NotificationGroup.EVENT, "ID1", messaging.LogLevel.INFO, "msg1")
        instance.notify(NotificationGroup.EVENT, "ID2", messaging.LogLevel.INFO, "msg2")
        self.assertEqual(queue.pending_count(), 2)

        self.job.run()

        self.assertEqual(queue.pending_count(), 0)
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(self.server.requests[0]["method"], "POST")
        self.assertEqual(self.server.requests[0]["path"], "/hook")
        bodies = [json.loads(r["body"]) for r in self.server.requests]
        self.assertEqual(bodies[0]["identificator"], "ID1")
        self.assertEqual(bodies[1]["identificator"], "ID2")

    def test_strict_fifo_blocks_on_http_error(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(url=f"{self.server.base_url}/hook")
        instance = _instance(notifier)
        instance.notify(NotificationGroup.EVENT, "ID1", messaging.LogLevel.INFO, "msg1")
        instance.notify(NotificationGroup.EVENT, "ID2", messaging.LogLevel.INFO, "msg2")
        self.server.respond = [500, 200, 200]

        self.job.run()

        # First delivery failed: element stays and stream is blocked
        self.assertEqual(queue.pending_count(), 2)
        self.assertEqual(len(self.server.requests), 1)
        self.assertTrue(self._backoff(notifier.uuid).is_bad(notifier.uuid))

        # Backoff cleared: next run delivers both, in order
        self._backoff(notifier.uuid).clear_bad(notifier.uuid)
        self.job.run()
        self.assertEqual(queue.pending_count(), 0)
        bodies = [json.loads(r["body"]) for r in self.server.requests]
        self.assertEqual(bodies[1]["identificator"], "ID1")
        self.assertEqual(bodies[2]["identificator"], "ID2")

    def test_strict_fifo_blocks_on_connection_error(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(url="http://127.0.0.1:1/hook")
        instance = _instance(notifier)
        instance.notify(NotificationGroup.EVENT, "ID1", messaging.LogLevel.INFO, "msg1")

        self.job.run()

        self.assertEqual(queue.pending_count(), 1)
        self.assertTrue(self._backoff(notifier.uuid).is_bad(notifier.uuid))

    def test_expired_and_orphaned_elements_are_purged(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(url=f"{self.server.base_url}/hook")
        base = queue.QueuedRequest(
            notifier_uuid=notifier.uuid,
            created=time.time(),
            attempts=0,
            method="POST",
            url=f"{self.server.base_url}/hook",
            headers={},
            body="{}",
            timeout=5,
            verify_ssl=True,
            retention_seconds=24 * 3600,
            backoff_cap_seconds=300,
        )
        queue.enqueue(dataclasses.replace(base, created=time.time() - 25 * 3600))  # Expired
        queue.enqueue(dataclasses.replace(base, notifier_uuid="nonexistent-uuid"))  # Orphaned
        queue.enqueue(base)  # Valid
        self.assertEqual(queue.pending_count(), 3)

        self.job.run()

        self.assertEqual(queue.pending_count(), 0)
        self.assertEqual(len(self.server.requests), 1)

    def test_backoff_skips_stream(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(url=f"{self.server.base_url}/hook")
        instance = _instance(notifier)
        instance.notify(NotificationGroup.EVENT, "ID1", messaging.LogLevel.INFO, "msg1")
        self._backoff(notifier.uuid).mark_bad(notifier.uuid)

        self.job.run()

        self.assertEqual(queue.pending_count(), 1)
        self.assertEqual(len(self.server.requests), 0)

    def test_scan_limit_defers_rest_to_next_run(self) -> None:
        notifier = notifiers_fixtures.createWebhookNotifier(url=f"{self.server.base_url}/hook")
        instance = _instance(notifier)
        for i in range(3):
            instance.notify(NotificationGroup.EVENT, f"ID{i}", messaging.LogLevel.INFO, "msg")

        with mock.patch.object(queue, "MAX_SCAN_PER_RUN", 2):
            self.job.run()

        # Only the scanned window was processed, the rest stays queued
        self.assertEqual(queue.pending_count(), 1)
        self.assertEqual(len(self.server.requests), 2)

        self.job.run()

        self.assertEqual(queue.pending_count(), 0)
        bodies = [json.loads(r["body"]) for r in self.server.requests]
        self.assertEqual([b["identificator"] for b in bodies], ["ID0", "ID1", "ID2"])

"""Tests for the admin-only global log endpoint exposed at ``/uds/rest/logs``."""

import datetime
import json
import typing

from django.utils import timezone

from uds.core.types.log import LogObjectType
from uds.core.util.log import LogLevel, LogSource, log
from uds.models.log import Log

from tests.utils import rest
from tests.utils.test import UDSHttpResponse

_MARKER_INFO: typing.Final[str] = "global-log-info-marker-for-test"
_MARKER_ERROR: typing.Final[str] = "global-log-error-marker-for-test"
_MARKER_OLD: typing.Final[str] = "global-log-old-marker-for-test"

_Body = dict[str, typing.Any]


def _seed_old_entry(hours_back: float, message: str) -> None:
    """Insert a global log line with a controlled timestamp."""
    Log.objects.create(
        owner_type=LogObjectType.SYSLOG.value,
        owner_id=-1,
        created=timezone.now() - datetime.timedelta(hours=hours_back),
        source=LogSource.INTERNAL.value,
        level=LogLevel.INFO.value,
        data=message,
        name="",
    )


class LogsTest(rest.test.RESTTestCase):
    """The global log surface is readable by admins only, with filters."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        log(None, LogLevel.INFO, _MARKER_INFO, source=LogSource.INTERNAL)
        log(None, LogLevel.ERROR, _MARKER_ERROR, source=LogSource.INTERNAL)

    def _get(self, query: dict[str, str] | None = None) -> tuple[int, _Body | None]:
        response = self.client.rest_get("logs", data=query or {})
        body: _Body | None = None
        if response.status_code == 200:
            body = typing.cast(_Body, json.loads(response.content))
        return response.status_code, body

    def _messages(self, body: _Body) -> list[str]:
        return [str(entry["message"]) for entry in typing.cast("list[_Body]", body["entries"])]

    def test_admin_reads_the_global_log(self) -> None:
        self.login()
        status, body = self._get()
        self.assertEqual(status, 200)
        assert body is not None
        messages = self._messages(body)
        self.assertIn(_MARKER_INFO, messages)
        self.assertIn(_MARKER_ERROR, messages)
        for entry in typing.cast("list[_Body]", body["entries"]):
            self.assertIn("level_name", entry)
            self.assertIn("date", entry)

    def test_level_filter_keeps_errors_only(self) -> None:
        self.login()
        status, body = self._get({"level": "ERROR"})
        self.assertEqual(status, 200)
        assert body is not None
        messages = self._messages(body)
        self.assertNotIn(_MARKER_INFO, messages)
        self.assertIn(_MARKER_ERROR, messages)

    def test_source_filter_matches(self) -> None:
        self.login()
        status, body = self._get({"source": "internal"})
        self.assertEqual(status, 200)
        assert body is not None
        self.assertIn(_MARKER_INFO, self._messages(body))

    def test_invalid_level_is_rejected(self) -> None:
        self.login()
        status, _body = self._get({"level": "NOT_A_LEVEL"})
        self.assertEqual(status, 400)

    def test_since_excludes_older_entries(self) -> None:
        _seed_old_entry(3, _MARKER_OLD)
        self.login()
        boundary = (timezone.now() - datetime.timedelta(hours=1)).isoformat()
        status, body = self._get({"since": boundary})
        self.assertEqual(status, 200)
        assert body is not None
        messages = self._messages(body)
        self.assertNotIn(_MARKER_OLD, messages)
        self.assertIn(_MARKER_INFO, messages)

    def test_until_excludes_newer_entries(self) -> None:
        _seed_old_entry(3, _MARKER_OLD)
        self.login()
        boundary = (timezone.now() - datetime.timedelta(hours=1)).isoformat()
        status, body = self._get({"until": boundary})
        self.assertEqual(status, 200)
        assert body is not None
        messages = self._messages(body)
        self.assertIn(_MARKER_OLD, messages)
        self.assertNotIn(_MARKER_INFO, messages)

    def test_plain_dates_cover_whole_days(self) -> None:
        _seed_old_entry(3, _MARKER_OLD)
        self.login()
        # The 3-hours-old entry is always at or after the start of yesterday,
        # and always before the end of today, so both assertions below hold
        # at any hour of the day.
        yesterday = (timezone.now() - datetime.timedelta(days=1)).date().isoformat()
        status, body = self._get({"since": yesterday})
        self.assertEqual(status, 200)
        assert body is not None
        self.assertIn(_MARKER_OLD, self._messages(body))

        status, body = self._get({"until": yesterday})
        self.assertEqual(status, 200)
        assert body is not None
        self.assertNotIn(_MARKER_INFO, self._messages(body))

    def test_invalid_date_is_rejected(self) -> None:
        self.login()
        status, _body = self._get({"since": "not-a-date"})
        self.assertEqual(status, 400)

    def test_truncation_is_reported_with_a_hint(self) -> None:
        self.login()
        status, body = self._get({"limit": "1"})
        self.assertEqual(status, 200)
        assert body is not None
        self.assertTrue(body["truncated"])
        self.assertEqual(body["limit"], 1)
        self.assertEqual(len(typing.cast("list[_Body]", body["entries"])), 1)
        self.assertIn("$skip/$top", str(body["hint"]))

    def test_no_truncation_means_complete_window(self) -> None:
        self.login()
        status, body = self._get()
        self.assertEqual(status, 200)
        assert body is not None
        self.assertFalse(body["truncated"])
        self.assertNotIn("hint", body)

    def test_odata_filter_applies_before_paging(self) -> None:
        self.login()
        status, body = self._get({"$filter": "source eq 'internal'"})
        self.assertEqual(status, 200)
        assert body is not None
        entries = typing.cast("list[_Body]", body["entries"])
        self.assertTrue(entries)
        for entry in entries:
            self.assertEqual(str(entry["source"]).lower(), "internal")

    def test_odata_orderby_oldest_first(self) -> None:
        self.login()
        status, body = self._get({"$orderby": "created asc"})
        self.assertEqual(status, 200)
        assert body is not None
        entries = typing.cast("list[_Body]", body["entries"])
        dates = [str(entry["date"]) for entry in entries]
        self.assertEqual(dates, sorted(dates))

    def test_odata_paging_with_total_count_header(self) -> None:
        self.login()
        response = self.client.rest_get("logs", data={"$top": "1", "$filter": "source eq 'internal'"})
        self.assertEqual(response.status_code, 200)
        total = int(response.headers["X-Total-Count"])
        self.assertGreaterEqual(total, 2)  # both setUp markers are internal
        body = typing.cast(_Body, json.loads(response.content))
        self.assertEqual(len(typing.cast("list[_Body]", body["entries"])), 1)
        self.assertTrue(body["truncated"])
        self.assertEqual(body["limit"], 1)

        # Skipping past everything matching must report a non-truncated page.
        response = self.client.rest_get(
            "logs", data={"$top": "1", "$skip": str(total), "$filter": "source eq 'internal'"}
        )
        self.assertEqual(response.status_code, 200)
        body = typing.cast(_Body, json.loads(response.content))
        self.assertEqual(len(typing.cast("list[_Body]", body["entries"])), 0)
        self.assertFalse(body["truncated"])

    def test_odata_rejects_unknown_fields(self) -> None:
        self.login()
        status, _body = self._get({"$filter": "no_such_field eq 1"})
        self.assertEqual(status, 400)

    def test_query_verb_with_body_parameters(self) -> None:
        self.login()
        # ``generic`` is not overridden by UDSClient, so the return type
        # needs a cast to reach the wrapped response attributes.
        response = typing.cast(
            UDSHttpResponse,
            self.client.generic(
                "QUERY",
                "/uds/rest/logs",
                data=json.dumps({"$filter": "source eq 'internal'", "$top": 5}),
                content_type="application/json",
                headers=self.client.uds_headers,
            ),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = typing.cast(_Body, json.loads(response.content))
        entries = typing.cast("list[_Body]", body["entries"])
        self.assertTrue(entries)
        for entry in entries:
            self.assertEqual(str(entry["source"]).lower(), "internal")

    def test_staff_is_forbidden(self) -> None:
        self.login(as_admin=False)
        status, _body = self._get()
        self.assertEqual(status, 403)

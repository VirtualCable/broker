import datetime
import typing

from tests.utils import rest

UTC = datetime.timezone.utc


class CalendarTimeZoneRestTest(rest.test.RESTTestCase):
    """The calendar zone travels through REST; rule times stay UTC instants."""

    BASE: typing.ClassVar[str] = "calendars"

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _create(self, **extra: typing.Any) -> str:
        resp = self.client.rest_post(self.BASE, {"name": "tz-calendar", "comments": "", "tags": [], **extra})
        self.assertEqual(resp.status_code, 200, resp.content.decode(errors="replace"))
        return resp.json()["id"]

    def test_zone_is_stored_and_returned(self) -> None:
        cal_uuid = self._create(tz="Europe/Madrid")
        self.assertEqual(self.client.rest_get(f"{self.BASE}/{cal_uuid}").json()["tz"], "Europe/Madrid")

    def test_zone_defaults_to_utc(self) -> None:
        cal_uuid = self._create()
        self.assertEqual(self.client.rest_get(f"{self.BASE}/{cal_uuid}").json()["tz"], "UTC")

    def test_update_without_zone_keeps_it(self) -> None:
        cal_uuid = self._create(tz="Asia/Tokyo")
        put = self.client.rest_put(f"{self.BASE}/{cal_uuid}", {"name": "renamed", "comments": "", "tags": []})
        self.assertEqual(put.status_code, 200, put.content.decode(errors="replace"))
        item = self.client.rest_get(f"{self.BASE}/{cal_uuid}").json()
        self.assertEqual((item["name"], item["tz"]), ("renamed", "Asia/Tokyo"))

    def test_invalid_zone_is_rejected(self) -> None:
        resp = self.client.rest_post(self.BASE, {"name": "bad", "comments": "", "tags": [], "tz": "Mars/Base"})
        self.assertNotEqual(resp.status_code, 200)

    def test_rule_times_are_utc_instants(self) -> None:
        cal_uuid = self._create(tz="Europe/Madrid")
        start = int(datetime.datetime(2026, 1, 5, 7, 0, tzinfo=UTC).timestamp())
        end = int(datetime.datetime(2026, 1, 31, 22, 59, 59, tzinfo=UTC).timestamp())
        created = self.client.rest_post(
            f"{self.BASE}/{cal_uuid}/rules",
            {
                "name": "office",
                "comments": "",
                "frequency": "DAILY",
                "start": start,
                "end": end,
                "interval": 1,
                "duration": 1,
                "duration_unit": "HOURS",
            },
        )
        self.assertEqual(created.status_code, 200, created.content.decode(errors="replace"))
        rule = self.client.rest_get(f"{self.BASE}/{cal_uuid}/rules/{created.json()['id']}").json()
        self.assertEqual((rule["start"], rule["end"]), (start, end))

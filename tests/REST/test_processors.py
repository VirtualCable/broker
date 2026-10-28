import datetime
import unittest

from uds.REST.processors import ContentProcessor


def _render(obj: object) -> object:
    return ContentProcessor.process_for_render(obj, lambda d: d)


class DatetimeRenderTest(unittest.TestCase):
    def test_aware_datetime_keeps_its_offset(self) -> None:
        tz = datetime.timezone(datetime.timedelta(hours=2))
        value = datetime.datetime(2026, 10, 27, 15, 30, 0, tzinfo=tz)
        self.assertEqual(_render(value), "2026-10-27T15:30:00+02:00")

    def test_naive_datetime_is_taken_as_utc(self) -> None:
        value = datetime.datetime(2026, 10, 27, 15, 30, 0)
        self.assertEqual(_render(value), "2026-10-27T15:30:00+00:00")

    def test_rendered_datetime_round_trips(self) -> None:
        value = datetime.datetime(2026, 10, 27, 15, 30, 0, 123456, tzinfo=datetime.timezone.utc)
        rendered = _render({"when": value})
        assert isinstance(rendered, dict)
        self.assertEqual(datetime.datetime.fromisoformat(rendered["when"]), value)

    def test_date_is_unchanged(self) -> None:
        self.assertEqual(_render(datetime.date(2026, 10, 7)), "2026-10-07")

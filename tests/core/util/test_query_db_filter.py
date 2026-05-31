from datetime import datetime
import logging
import typing

from django.utils import timezone

from uds.models import Log
from uds.core.util.query_db_filter import exec_query

from tests.utils.test import UDSTestCase

logger = logging.getLogger(__name__)

# Pre-filter to isolate test data from fixture/setup logs
_TEST_MARKER = '_testq_'
# All queries must include this to avoid matching pre-existing Log rows
_FLTR = f"data eq '{_TEST_MARKER}' and "


class DBQueryTests(UDSTestCase):

    def setUp(self) -> None:
        Log.objects.create(
            name='daily_job',
            level=86400,
            data=_TEST_MARKER,
            created=timezone.make_aware(datetime(2025, 8, 13, 12, 0)),
            source='server1',
        )

        Log.objects.create(
            name='hourly_job',
            level=3600,
            data=_TEST_MARKER,
            created=timezone.make_aware(datetime(2025, 8, 13, 19, 0)),
            source='server2',
        )

        Log.objects.create(
            name='weekly_job',
            level=604800,
            data=_TEST_MARKER,
            created=timezone.make_aware(datetime(2025, 8, 7, 12, 0)),
            source='server1',
        )

        Log.objects.create(
            name='long_job',
            level=2592000,
            data=_TEST_MARKER,
            created=timezone.make_aware(datetime(2025, 7, 12, 12, 0)),
            source='server3',
        )

    def test_eq_query(self) -> None:
        result = exec_query(_FLTR + "name eq 'hourly_job'", Log.objects)
        self.assertEqual(result.count(), 1)
        first = result.first()
        assert first is not None
        self.assertEqual(first.name, 'hourly_job')

    def test_and_query(self) -> None:
        result = exec_query(_FLTR + "source eq 'server1' and level gt 3600", Log.objects)
        self.assertEqual(result.count(), 2)  # daily_job and weekly_job

    def test_or_query(self) -> None:
        result = exec_query(_FLTR + "source eq 'server2' or level lt 3600", Log.objects)
        self.assertEqual(result.count(), 1)
        first = result.first()
        assert first is not None
        self.assertEqual(first.name, 'hourly_job')

    def test_func_startswith(self) -> None:
        results = exec_query(_FLTR + "startswith(name, 'week')", Log.objects)
        self.assertEqual(results.count(), 1)
        first = results.first()
        assert first is not None
        self.assertEqual(first.name, 'weekly_job')

    def test_not_query(self) -> None:
        results = exec_query(_FLTR + "not(source eq 'server3')", Log.objects)
        self.assertEqual(results.count(), 3)
        self.assertFalse(results.filter(source='server3').exists())

    def test_complex_and_or_combination(self) -> None:
        results = exec_query(
            _FLTR + "(source eq 'server1' and level lt 86400) or source eq 'server3'",
            Log.objects,
        )
        self.assertEqual(results.count(), 1)
        first = results.first()
        assert first is not None
        self.assertEqual(first.name, 'long_job')

        results = exec_query(_FLTR + "endswith(source, '1')", Log.objects)
        self.assertEqual(results.count(), 2)  # daily_job & weekly_job

    def test_nested_not_and(self) -> None:
        result = exec_query(
            _FLTR + "not(source eq 'server1')", Log.objects
        )
        self.assertEqual(result.count(), 2)
        names = {r.name for r in result}
        self.assertFalse('daily_job' in names and 'weekly_job' in names)

    def test_invalid_query_returns_value_error(self) -> None:
        with self.assertRaises(ValueError):
            exec_query(_FLTR + "level >> 1000", Log.objects)  # invalid syntax

    def test_field_comparison(self) -> None:
        results = exec_query(_FLTR + "level gt 3600", Log.objects)
        self.assertEqual(results.count(), 3)  # all except hourly_job

    def test_func_length(self) -> None:
        results = exec_query(_FLTR + "length(name) eq 10", Log.objects)
        self.assertEqual(results.count(), 2)
        names = {r.name for r in results}
        assert names == {'hourly_job', 'weekly_job'}

    def test_func_tolower(self) -> None:
        Log.objects.filter(name='daily_job', data=_TEST_MARKER).update(name='DAILY_JOB')
        results = exec_query(_FLTR + "tolower(name) eq 'daily_job'", Log.objects)
        self.assertEqual(results.count(), 1)

    def test_func_toupper(self) -> None:
        results = exec_query(_FLTR + "toupper(name) eq 'DAILY_JOB'", Log.objects)
        self.assertEqual(results.count(), 1)

    def test_func_year(self) -> None:
        results = exec_query(_FLTR + "year(created) eq 2025", Log.objects)
        self.assertEqual(results.count(), 4)

    def test_func_month(self) -> None:
        results = exec_query(_FLTR + "month(created) eq 8", Log.objects)
        self.assertEqual(results.count(), 3)

    def test_func_day(self) -> None:
        results = exec_query(_FLTR + "day(created) eq 13", Log.objects)
        self.assertEqual(results.count(), 2)  # daily_job & hourly_job

    def test_func_concat(self) -> None:
        results = exec_query(
            _FLTR + "concat(name, ' - ', source) eq 'daily_job - server1'", Log.objects
        )
        res = list(results)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].name, 'daily_job')

    def test_func_substring(self) -> None:
        results = exec_query(_FLTR + "substring(name, 1, 4) eq 'aily'", Log.objects)
        res = list(results)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].name, 'daily_job')

    def test_func_floor(self) -> None:
        result = exec_query(_FLTR + "floor(level) eq 3600", Log.objects)
        res = list(result)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].name, 'hourly_job')

    def test_func_round(self) -> None:
        result = exec_query(_FLTR + "round(level) eq 604800", Log.objects)
        res = list(result)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].name, 'weekly_job')

    def test_func_ceiling(self) -> None:
        result = exec_query(_FLTR + "ceiling(level) eq 2592000", Log.objects)
        res = list(result)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].name, 'long_job')

    def test_func_trim(self) -> None:
        Log.objects.create(
            name='  hourly_job  ',
            level=3600,
            data=_TEST_MARKER,
            created=timezone.make_aware(datetime(2025, 8, 13, 21, 0)),
            source='server4',
        )

        result = exec_query(_FLTR + "trim(name) eq 'hourly_job'", Log.objects)
        res = list(result)
        self.assertEqual(len(res), 2)
        self.assertIn('server4', {r.source for r in res})

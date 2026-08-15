#
# Copyright (c) 2022 Virtual Cable S.L.
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

import datetime
import zoneinfo

from django.utils import timezone

from uds.core.util import calendar
from uds.models import Calendar

from ...fixtures.calendars import createCalendars
from ...utils.test import UDSTestCase


class CalendarTest(UDSTestCase):
    def setUp(self) -> None:
        createCalendars()

    def test_calendar_dayly(self) -> None:
        cal = Calendar.objects.get(uuid="2cf6846b-d889-57ce-bb35-e647040a95b6")
        chk = calendar.CalendarChecker(cal)
        calendar.CalendarChecker.updates = 0

        # Force UTC timezone for tests
        timezone.activate(datetime.timezone.utc)

        # Rule with end

        # update 1
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2014, 9, 1, 21, 0, 0))))

        # update 2
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 9, 1, 20, 59, 0))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 9, 1, 21, 0, 0))))

        # update 3
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 10, 1, 0, 0, 0))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 10, 1, 1, 59, 0))))
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 10, 1, 2, 0, 0))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 10, 1, 21, 0, 0))))

        # update 4
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 10, 2, 21, 0, 0))))

        # Rule without end, but with beginning

        # update 5
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2014, 9, 1, 8, 0, 0))))

        # update 6
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 9, 1, 7, 59, 0))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 9, 1, 8, 0, 0))))

        # updates... (total is 366, because previous updates has been cached)
        for day in range(365):
            date = datetime.date(2015, 1, 1) + datetime.timedelta(days=day)

            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(7, 59, 0)))))
            fnc = self.assertTrue if date >= datetime.date(2015, 9, 1) else self.assertFalse
            fnc(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(8, 0, 0)))))
            fnc(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(19, 59, 0)))))
            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(20, 0, 0)))))

        self.assertEqual(chk.updates, 366)

    def test_calendar_weekly(self) -> None:
        cal = Calendar.objects.get(uuid="c1221a6d-3848-5fa3-ae98-172662c0f554")
        chk = calendar.CalendarChecker(cal)
        calendar.CalendarChecker.updates = 0

        valid_days = [1, 8, 15, 22, 29]

        # Rule with end
        for day in range(30):
            date = datetime.date(2015, 9, day + 1)
            fnc = self.assertTrue if (day + 1) in valid_days else self.assertFalse
            fnc(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(10, 0, 0)))))
            fnc(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(11, 59, 0)))))

            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(9, 59, 0)))))
            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(12, 0, 0)))))

        # update 31
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 8, 25, 10, 0, 0))))

        # update 32
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 10, 6, 10, 0, 0))))

        # Rule without end

        # updates... (total is 365, because previous updates has been cached)
        for day in range(365):
            date = datetime.date(2015, 1, 1) + datetime.timedelta(days=day)

            fnc = self.assertTrue if date >= datetime.date(2015, 9, 1) and date.isoweekday() == 2 else self.assertFalse

            fnc(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(7, 0, 0)))))
            fnc(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(8, 59, 0)))))
            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(6, 59, 0)))))
            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(9, 0, 0)))))

        self.assertEqual(chk.updates, 365)

    def test_calendar_monthly(self) -> None:
        cal = Calendar.objects.get(uuid="353c4cb8-e02d-5387-a18f-f634729fde81")
        chk = calendar.CalendarChecker(cal)
        calendar.CalendarChecker.updates = 0

        # Updates 1..730
        for day in range(730):
            date = datetime.date(2015, 1, 1) + datetime.timedelta(days=day)

            fnc = (
                self.assertTrue
                if date.day == 1 and datetime.date(2015, 9, 1) <= date <= datetime.date(2015, 11, 1)
                else self.assertFalse
            )
            fnc2 = self.assertTrue if date.day == 1 and date >= datetime.date(2015, 9, 1) else self.assertFalse

            fnc(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(10, 0, 0)))))
            fnc(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(11, 59, 0)))))

            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(9, 59, 0)))))
            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(12, 0, 0)))))

            fnc2(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(7, 0, 0)))))
            fnc2(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(8, 59, 0)))))
            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(6, 59, 0)))))
            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(9, 0, 0)))))

        self.assertEqual(chk.updates, 730)

    def test_calendar_weekdays(self) -> None:
        cal = Calendar.objects.get(uuid="bccfd011-605b-565f-a08e-80bf75114dce")
        chk = calendar.CalendarChecker(cal)
        calendar.CalendarChecker.updates = 0

        valid_days = [1, 3, 5]

        for day in range(730):
            date = datetime.date(2015, 1, 1) + datetime.timedelta(days=day)

            fnc = (
                self.assertTrue
                if date.isoweekday() in valid_days and datetime.date(2015, 9, 1) <= date <= datetime.date(2015, 10, 1)
                else self.assertFalse
            )
            fnc2 = (
                self.assertTrue
                if date.isoweekday() in valid_days and date >= datetime.date(2015, 9, 1)
                else self.assertFalse
            )

            fnc(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(10, 0, 0)))))
            fnc(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(11, 59, 0)))))

            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(9, 59, 0)))))
            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(12, 0, 0)))))

            fnc2(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(7, 0, 0)))))
            fnc2(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(8, 59, 0)))))
            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(6, 59, 0)))))
            self.assertFalse(chk.check(timezone.make_aware(datetime.datetime.combine(date, datetime.time(9, 0, 0)))))

        self.assertEqual(chk.updates, 730)

    def test_calendar_durations(self) -> None:
        cal = Calendar.objects.get(uuid="60160f94-c8fe-5fdc-bbbe-325010980106")
        chk = calendar.CalendarChecker(cal)

        # Force UTC timezone for tests
        timezone.activate(datetime.timezone.utc)

        # Minutes
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2014, 12, 31, 23, 59, 59))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 1, 1, 0, 0, 0))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 1, 1, 0, 1, 59))))
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 1, 1, 0, 2, 0))))

        # Hours
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 1, 31, 23, 59, 59))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 2, 1, 0, 0, 0))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 2, 1, 1, 59, 59))))
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 2, 1, 2, 0, 0))))

        # Days
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 2, 28, 23, 59, 59))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 3, 1, 0, 0, 0))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 3, 2, 23, 59, 59))))
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 3, 3, 0, 0, 0))))

        # Weeks
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 3, 31, 23, 59, 59))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 4, 1, 8, 0, 0))))
        self.assertTrue(chk.check(timezone.make_aware(datetime.datetime(2015, 4, 15, 7, 59, 59))))
        self.assertFalse(chk.check(timezone.make_aware(datetime.datetime(2015, 4, 15, 8, 0, 0))))

    def test_check_uses_user_tz_for_day_key_when_db_is_utc(self) -> None:
        """The cache key is the user's wall-clock day, not the database day.

        Setup: Django is configured for ``Europe/Madrid`` (UTC+1 in
        February).  The fixture's "Test Hours" rule lives on
        ``2015-02-01T00:00:00+00:00..02:00:00+00:00`` — so it is active
        only on 2015-02-01 UTC.  In Madrid local time that maps to
        ``2015-02-01T01:00:00..03:00:00`` (still the same day).

        A check at 00:30 UTC on 2015-02-01 is 01:30 Madrid on the same
        local day: must be inside the rule window (True).

        A check at 23:30 UTC on 2015-01-31 is 00:30 Madrid on 2015-02-01:
        must also be inside the rule window (True).

        Before the ``astimezone`` fix, the bitarray key was the UTC day,
        so the 23:30 UTC lookup would key on 2015-01-31 (no rule there)
        and incorrectly return False.
        """
        madrid = zoneinfo.ZoneInfo("Europe/Madrid")
        # Pin the operator's tz to Madrid for the duration of the test.
        timezone.activate(madrid)
        try:
            # The ``CalendarChecker`` cache is a process-wide ``ClassVar``,
            # so a previous test that ran under UTC may have stored a
            # stale bitarray for the same calendar+day.  Drop every entry
            # so this test exercises the Madrid path cleanly.
            calendar.CalendarChecker.cache.purge()
            cal = Calendar.objects.get(uuid="60160f94-c8fe-5fdc-bbbe-325010980106")
            chk = calendar.CalendarChecker(cal)
            calendar.CalendarChecker.updates = 0

            # 00:30 UTC on 2015-02-01 is 01:30 Madrid on 2015-02-01.
            # Inside the rule window (00:00..02:00 UTC): True.
            self.assertTrue(chk.check(datetime.datetime(2015, 2, 1, 0, 30, 0, tzinfo=datetime.timezone.utc)))
            # 23:30 UTC on 2015-01-31 is 00:30 Madrid on 2015-02-01.
            # Before the fix this keyed on 2015-01-31 and returned False;
            # after the fix it keys on 2015-02-01 and returns True.
            self.assertTrue(chk.check(datetime.datetime(2015, 1, 31, 23, 30, 0, tzinfo=datetime.timezone.utc)))
            # 03:00 UTC on 2015-02-01 is 04:00 Madrid on 2015-02-01.
            # Past the 2h rule window: False.
            self.assertFalse(chk.check(datetime.datetime(2015, 2, 1, 3, 0, 0, tzinfo=datetime.timezone.utc)))
            # 22:30 UTC on 2015-01-31 is 23:30 Madrid on 2015-01-31.
            # One day BEFORE the rule.  Must be False.
            self.assertFalse(chk.check(datetime.datetime(2015, 1, 31, 22, 30, 0, tzinfo=datetime.timezone.utc)))
        finally:
            # Restore UTC so subsequent tests (which assume UTC) are not
            # affected by our tz override.  ``deactivate`` would leave the
            # current tz as ``None``, which would make
            # ``get_current_timezone()`` fall back to settings — equally
            # fine, but explicit is safer here.
            timezone.activate(datetime.timezone.utc)

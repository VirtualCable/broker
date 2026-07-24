# -*- coding: utf-8 -*-
#
# Copyright (c) 2025 Virtual Cable S.L.U.
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
Regression tests for ``DetailHandler.calc_item_position`` ordering stability.

The detail handler feeds a related queryset to ``calc_item_position``. When the
detail model declares no ``Meta.ordering`` the DB is free to return rows in any
order, and a second query (the one the position lookup issues) may order them
differently from the overview -- so the reported position pointed at the wrong
row. ``CalendarRule`` is used as the concrete detail model because it declares
*no* ``Meta.ordering``, exercising exactly that path.
"""

from uds import models
from uds.REST.methods.calendarrules import CalendarRules

from ...utils.test import UDSTestCase


class DetailPositionOrderingTest(UDSTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.calendar = models.Calendar.objects.create(name="position-test-calendar")
        # Insert rules whose *names* are deliberately out of pk order, so a
        # name-based or accidental ordering would diverge from insertion/pk order.
        names = ["zulu", "alpha", "mike", "bravo", "yankee", "charlie", "november"]
        self.rules: list[models.CalendarRule] = [
            self.calendar.rules.create(
                name=name,
                start="2025-01-01T08:00:00+00:00",
                end=None,
                frequency="DAILY",
                interval=1,
                duration=1,
                duration_unit="HOURS",
            )
            for name in names
        ]
        # The handler holds no state used by ``calc_item_position``; build a bare
        # instance so we can call the helper without the full request machinery.
        self.handler: CalendarRules = object.__new__(CalendarRules)

    def test_model_declares_no_ordering(self) -> None:
        """Because CalendarRule declares no Meta.ordering, the model must not
        silently gain one (that would change what 'position' means)."""
        self.assertFalse(
            models.CalendarRule._meta.ordering,
            "CalendarRule is expected to declare no Meta.ordering for this test to be meaningful",
        )

    def test_position_matches_pk_order(self) -> None:
        """Each rule's reported position must equal its index in pk order."""
        for expected_pos, rule in enumerate(sorted(self.rules, key=lambda r: r.pk)):
            got = self.handler.calc_item_position(rule.uuid, self.calendar.rules.all())
            self.assertEqual(
                got,
                expected_pos,
                f"rule {rule.name!r} (uuid {rule.uuid}) expected pos {expected_pos}, got {got}",
            )

    def test_position_is_stable_across_calls(self) -> None:
        """Repeated lookups (fresh queries each time) must return the same
        position -- the property that was flaky before the ordering guard."""
        rule = self.rules[3]
        positions = {
            self.handler.calc_item_position(rule.uuid, self.calendar.rules.all())
            for _ in range(8)
        }
        self.assertEqual(
            len(positions),
            1,
            f"position for {rule.name!r} was not stable across calls: {positions}",
        )
        self.assertNotEqual(positions.pop(), -1)

    def test_unknown_uuid_returns_minus_one(self) -> None:
        """A uuid that is not in the queryset must yield -1, not a stray index."""
        got = self.handler.calc_item_position(
            "00000000-0000-0000-0000-000000000000", self.calendar.rules.all()
        )
        self.assertEqual(got, -1)

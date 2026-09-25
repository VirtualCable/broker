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

Tests for the automatic/manual check kind filtering of the runner.
"""

import typing
from unittest import mock

from uds.core import types
from uds.core.checks import AutomaticCheck, Check, ManualCheck
from uds.core.checks import runner as runner_module

from ..utils.test import UDSTransactionTestCase


_PRODUCTION_MANUAL_CHECKS: typing.Final[set[str]] = {
    "authenticators-health",
    "duplicate-users-in-authenticator",
}


class DummyAutomaticCheck(AutomaticCheck):
    id: typing.ClassVar[str] = "dummy-automatic"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = "Dummy automatic check."

    @typing.override
    def run(self) -> types.checks.CheckOutcome:
        return (types.checks.CheckSeverity.INFO, True, "automatic ok")


class DummyManualBase(ManualCheck):
    """Intermediate class: no id, so it would never register on its own."""


class DummyManualCheck(DummyManualBase):
    # Derives from ManualCheck through an intermediate class: still manual
    id: typing.ClassVar[str] = "dummy-manual"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = "Dummy manual check."

    @typing.override
    def run(self) -> types.checks.CheckOutcome:
        return (types.checks.CheckSeverity.MEDIUM, False, "manual failing")


class CheckKindFilterTest(UDSTransactionTestCase):
    def _patched_collect(self) -> list[tuple[str, type[Check]]]:
        return [
            ("dummy-automatic", DummyAutomaticCheck),
            ("dummy-manual", DummyManualCheck),
        ]

    def test_automatic_runs_only_automatic_checks(self) -> None:
        with mock.patch.object(runner_module, "_collect_checks", new=self._patched_collect):
            results = runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)
        self.assertEqual([result.id for result in results], ["dummy-automatic"])

    def test_manual_runs_only_manual_checks(self) -> None:
        with mock.patch.object(runner_module, "_collect_checks", new=self._patched_collect):
            results = runner_module.run_checks(types.checks.CheckKind.MANUAL)
        self.assertEqual([result.id for result in results], ["dummy-manual"])
        self.assertFalse(results[0].ok, results[0].message)

    def test_manual_respects_category_filter(self) -> None:
        with mock.patch.object(runner_module, "_collect_checks", new=self._patched_collect):
            results = runner_module.run_checks(
                types.checks.CheckKind.MANUAL, types.checks.CheckCategory.SECURITY
            )
        self.assertEqual(results, [])

    def test_regular_checks_are_automatic(self) -> None:
        for check_id, check_class in runner_module._collect_checks():
            if check_id in _PRODUCTION_MANUAL_CHECKS:
                continue
            self.assertTrue(issubclass(check_class, AutomaticCheck), check_class.id)
            self.assertFalse(issubclass(check_class, ManualCheck), check_class.id)

    def test_only_the_known_production_checks_are_manual(self) -> None:
        manual = {
            check_id
            for check_id, check_class in runner_module._collect_checks()
            if issubclass(check_class, ManualCheck)
        }
        self.assertEqual(manual, _PRODUCTION_MANUAL_CHECKS)

    def test_factory_rejects_checks_without_kind_marker(self) -> None:
        from uds.core.checks import ChecksFactory

        class NoKindCheck(Check):
            # Derives from Check directly: no kind, must not register
            id: typing.ClassVar[str] = "no-kind"
            category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
            description: typing.ClassVar[str] = "Never registered."

        factory = ChecksFactory()
        factory.insert(NoKindCheck)
        self.assertNotIn("no-kind", factory)

    def test_has_health_check_detects_real_implementations(self) -> None:
        from uds.core.module import Module

        # Module itself and subclasses without override: no real health check
        self.assertFalse(Module.has_health_check())

        class PlainModule(Module):  # abstract, but classmethods still work
            pass

        self.assertFalse(PlainModule.has_health_check())

        class ModuleWithHealthCheck(PlainModule):
            @typing.override
            def health_check(self) -> list[types.checks.CheckOutcome]:
                return []

        self.assertTrue(ModuleWithHealthCheck.has_health_check())


class DummyDetailedCheck(AutomaticCheck):
    id: typing.ClassVar[str] = "dummy-detailed"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = "Dummy check with details."
    affected: typing.ClassVar[list[str]] = []

    @typing.override
    def run(self) -> types.checks.CheckOutcome:
        return (types.checks.CheckSeverity.MEDIUM, False, "some elements failed", self.affected)


class CheckDescriptionAndDetailsTest(UDSTransactionTestCase):
    def _run_detailed(self, affected: list[str]) -> types.checks.CheckResult:
        DummyDetailedCheck.affected = affected
        with mock.patch.object(
            runner_module, "_collect_checks", new=lambda: [("dummy-detailed", DummyDetailedCheck)]
        ):
            return runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)[0]

    def test_every_registered_check_has_a_description(self) -> None:
        for check_id, check_class in runner_module._collect_checks():
            self.assertIsInstance(check_class.description, str, check_id)
            self.assertGreater(len(check_class.description.strip()), 20, check_id)

    def test_description_and_details_reach_the_report(self) -> None:
        result = self._run_detailed(["first", "second"])

        self.assertEqual(result.description, "Dummy check with details.")
        self.assertEqual(result.details, ("first", "second"))
        report = runner_module.build_report([result])
        self.assertEqual(report["checks"][0]["description"], "Dummy check with details.")
        self.assertEqual(report["checks"][0]["details"], ["first", "second"])

    def test_checks_without_details_report_an_empty_list(self) -> None:
        with mock.patch.object(
            runner_module, "_collect_checks", new=lambda: [("dummy-automatic", DummyAutomaticCheck)]
        ):
            result = runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)[0]

        self.assertEqual(result.details, ())
        self.assertEqual(runner_module.build_report([result])["checks"][0]["details"], [])

    def test_details_are_capped(self) -> None:
        affected = [f"element {i}" for i in range(runner_module.MAX_DETAILS + 5)]

        result = self._run_detailed(affected)

        self.assertEqual(len(result.details), runner_module.MAX_DETAILS + 1)
        self.assertEqual(
            result.details[: runner_module.MAX_DETAILS], tuple(affected[: runner_module.MAX_DETAILS])
        )
        self.assertIn("5", result.details[-1])


class CheckWithoutDescription(AutomaticCheck):
    id: typing.ClassVar[str] = "dummy-without-description"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH

    @typing.override
    def run(self) -> types.checks.CheckOutcome:
        return (types.checks.CheckSeverity.INFO, True, "never reached")


class MissingDescriptionTest(UDSTransactionTestCase):
    def test_a_check_without_description_fails_alone(self) -> None:
        with mock.patch.object(
            runner_module,
            "_collect_checks",
            new=lambda: [
                ("dummy-without-description", CheckWithoutDescription),
                ("dummy-automatic", DummyAutomaticCheck),
            ],
        ):
            results = runner_module.run_checks(types.checks.CheckKind.AUTOMATIC)

        by_id = {result.id: result for result in results}
        self.assertFalse(by_id["dummy-without-description"].ok)
        self.assertIn("could not be evaluated", by_id["dummy-without-description"].message)
        self.assertTrue(by_id["dummy-automatic"].ok)

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

Runner that executes the self-assessment checks.

Separated from :mod:`uds.core.checks.__init__` so the package can expose a flat
namespace (factory, groups, ...) while keeping the actual ``run_checks`` /
``build_report`` entry points in their own module.
"""

import logging
import typing

from django.utils.translation import gettext as _

from uds.core import types

from .factory import CheckEntry, ChecksFactory

logger: logging.Logger = logging.getLogger(__name__)


def _collect_checks() -> list[tuple[str, CheckEntry]]:
    """Returns the current registered checks as ``[(id, entry), ...]``.

    Extracted so tests can monkey-patch the iteration source instead of
    mutating the singleton factory.
    """
    return list(typing.cast("dict[str, CheckEntry]", ChecksFactory().objects()).items())


def run_checks(category: types.checks.CheckCategory | None = None) -> list[types.checks.CheckResult]:
    """
    Runs all the registered checks (or just the ones belonging to ``category``)
    and returns their results.

    A check that raises is reported as a failed ``INFO`` result instead of
    aborting the whole scan, so a single broken check never hides the rest.
    """
    results: list[types.checks.CheckResult] = []
    for check_id, entry in _collect_checks():
        if category is not None and entry.category is not category:
            continue
        try:
            severity, ok, message = entry.fn()
        except Exception as e:
            logger.exception("Check %s could not be evaluated", check_id)
            results.append(
                types.checks.CheckResult(
                    id=check_id,
                    severity=types.checks.CheckSeverity.INFO,
                    ok=False,
                    message=_("Check could not be evaluated: {error}").format(error=e),
                    category=entry.category,
                )
            )
            continue
        results.append(
            types.checks.CheckResult(
                id=check_id, severity=severity, ok=ok, message=message, category=entry.category
            )
        )

    return results


def build_report(
    results: list[types.checks.CheckResult] | None = None,
) -> dict[str, typing.Any]:
    """
    Aggregates the check results into the JSON report returned by the
    ``/system/checks`` endpoint: a summary with the number of failed checks per
    severity, a per-category breakdown of the same, and the full check list.

    If ``results`` is ``None`` all the checks are run first. Passing a list
    produced by :func:`run_checks` with a category filter yields the report for
    that category only.
    """
    if results is None:
        results = run_checks()

    report: dict[str, typing.Any] = {severity.value: 0 for severity in types.checks.CheckSeverity}
    categories: dict[str, int] = {category.value: 0 for category in types.checks.CheckCategory}
    for result in results:
        if not result.ok:
            report[result.severity.value] += 1
            categories[result.category.value] += 1
    report["categories"] = categories
    report["checks"] = [result.as_dict() for result in results]

    return report

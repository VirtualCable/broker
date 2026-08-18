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

Runner that executes the security self-assessment checks.

Separated from :mod:`uds.core.security.checks.__init__` so the package can
expose a flat namespace (factory, groups, ...) while keeping the actual
``run_security_checks`` / ``build_report`` entry points in their own module.
"""

import logging
import typing

from django.utils.translation import gettext as _

from uds.core import types

from . import global_config, logs, models, settings
from .factory import CheckFn, SecurityChecksFactory

logger = logging.getLogger(__name__)


# All groups are registered exactly once, when this module is first imported.
# Subsequent imports are no-ops because the factory ignores duplicate ids.
_bootstrap_done = False


def _bootstrap() -> None:
    global _bootstrap_done
    if _bootstrap_done:
        return
    fact = SecurityChecksFactory()
    for group in (settings, global_config, models, logs):
        group.register_checks(fact)
    _bootstrap_done = True


def _collect_checks() -> list[tuple[str, CheckFn]]:
    """Returns the current registered checks as ``[(id, fn), ...]``.

    Extracted so tests can monkey-patch the iteration source instead of
    mutating the singleton factory.
    """
    _bootstrap()
    return list(SecurityChecksFactory().objects().items())


def run_security_checks() -> list[types.security.SecurityCheckResult]:
    """
    Runs all the registered security checks and returns their results.

    A check that raises is reported as a failed ``INFO`` result instead of
    aborting the whole scan, so a single broken check never hides the rest.
    """
    results: list[types.security.SecurityCheckResult] = []
    for check_id, check in _collect_checks():
        try:
            severity, ok, message = check()
        except Exception as e:
            logger.exception("Security check %s could not be evaluated", check_id)
            results.append(
                types.security.SecurityCheckResult(
                    id=check_id,
                    severity=types.security.SecurityCheckSeverity.INFO,
                    ok=False,
                    message=_("Check could not be evaluated: {error}").format(error=e),
                )
            )
            continue
        results.append(types.security.SecurityCheckResult(id=check_id, severity=severity, ok=ok, message=message))

    return results


def build_report(
    results: list[types.security.SecurityCheckResult] | None = None,
) -> dict[str, typing.Any]:
    """
    Aggregates the check results into the JSON report returned by the
    ``/system/security_check`` endpoint: a summary with the number of failed
    checks per severity plus the full check list.

    If ``results`` is ``None`` the checks are run first.
    """
    if results is None:
        results = run_security_checks()

    report: dict[str, typing.Any] = {severity.value: 0 for severity in types.security.SecurityCheckSeverity}
    for result in results:
        if not result.ok:
            report[result.severity.value] += 1
    report["checks"] = [result.as_dict() for result in results]

    return report

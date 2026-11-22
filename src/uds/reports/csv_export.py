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

"""Spreadsheet-safe CSV export for UDS reports.

Every exported CSV cell carries data that may originate outside the
administrator's control (identity provider attributes, user names,
pooled service descriptions, ...). A cell whose text starts with ``=``,
``+``, ``-``, ``@``, a tab or a carriage return is evaluated as a
formula by spreadsheet clients (data exfiltration through
``HYPERLINK``/``WEBSERVICE``, command execution through DDE on old
configurations). Exported cells are therefore neutralized with a
leading apostrophe, the standard text marker every spreadsheet client
respects (CWE-1236). Use :func:`safe_csv_writer` in every report CSV
writer instead of the bare ``csv.writer``.
"""

import collections.abc
import csv
import typing

_FORMULA_TRIGGERS: typing.Final[frozenset[str]] = frozenset({"=", "+", "-", "@", "\t", "\r"})


def csv_safe(value: typing.Any) -> typing.Any:
    """Neutralize a cell whose text could be interpreted as a formula.

    Non-string cells (numbers, ``None``, dates already formatted by the
    report) are returned untouched.
    """
    if not isinstance(value, str):
        return value
    return f"'{value}" if value[:1] in _FORMULA_TRIGGERS else value


class _SafeCsvWriter:
    """``csv.writer`` wrapper that neutralizes every exported string cell.

    Note: the wrapped writer is typed ``Any`` because ``csv.writer``
    returns the private ``csv._writer`` class, which has no public type;
    this seam is safe because we only delegate ``writerow``/``writerows``
    to it.
    """

    def __init__(self, writer: typing.Any) -> None:
        self._writer = writer

    def writerow(self, row: collections.abc.Iterable[typing.Any]) -> typing.Any:
        return self._writer.writerow([csv_safe(cell) for cell in row])

    def writerows(self, rows: collections.abc.Iterable[collections.abc.Iterable[typing.Any]]) -> typing.Any:
        return self._writer.writerows([[csv_safe(cell) for cell in row] for row in rows])


def safe_csv_writer(output: typing.TextIO, **kwargs: typing.Any) -> _SafeCsvWriter:
    """A ``csv.writer`` over ``output`` whose every string cell is neutralized."""
    return _SafeCsvWriter(csv.writer(output, **kwargs))

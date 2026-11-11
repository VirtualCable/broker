
#
# Copyright (c) 2026 Virtual Cable S.L.U.
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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import pathlib
import subprocess
import sys
import typing

from ..utils.test import UDSTestCase

SETTINGS_TEMPLATE: typing.Final = (
    pathlib.Path(__file__).parents[2] / 'src' / 'server' / 'settings.py.sample'
)


def _mysql_driver_after_loading(settings_file: pathlib.Path) -> str:
    script = (
        'import runpy, sys; '
        'runpy.run_path(sys.argv[1]); '
        'import django.db.backends.mysql.base as backend; '
        'print(backend.Database.__name__)'
    )
    result = subprocess.run(
        [sys.executable, '-c', script, str(settings_file)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class MySQLDriverTest(UDSTestCase):
    def test_settings_template_makes_django_use_pymysql(self) -> None:
        self.assertEqual(_mysql_driver_after_loading(SETTINGS_TEMPLATE), 'pymysql')

# -*- coding: utf-8 -*-

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
Author: Andres Schumann, aschumann at virtualcable dot es
"""

from django.utils import translation

from uds.core.util.config import Config, GlobalConfig

from ...utils.test import UDSTestCase


class ConfigGroupsTest(UDSTestCase):
    def test_declared_group_reaches_overview(self) -> None:
        with translation.override('en'):
            values = Config.get_config_values()
        self.assertEqual(values['Security']['maxLoginTries']['group'], 'Login and passwords')
        self.assertEqual(values['UDS']['cacheCheckDelay']['group'], 'Publications and cache')

    def test_value_without_group_reports_empty_group(self) -> None:
        Config.section(Config.SectionType.OTHER).value(
            'ungroupedTestValue', '1', type=Config.FieldType.TEXT, help='test'
        ).get()
        values = Config.get_config_values()
        self.assertEqual(values['Other']['ungroupedTestValue']['group'], '')

    def test_every_visible_core_value_has_a_group(self) -> None:
        core_sections = {
            Config.SectionType.GLOBAL,
            Config.SectionType.SECURITY,
            Config.SectionType.CUSTOM,
            Config.SectionType.MCP,
        }
        GlobalConfig.initialize()
        ungrouped = [
            f'{cfg.section()}.{cfg.key()}'
            for cfg in Config.enumerate()
            if cfg.section() in core_sections and not cfg.get_group()
        ]
        self.assertEqual(ungrouped, [])

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
Author: Adolfo Gomez, dkmaster at dkmon dot com
"""
# pyright: reportUnusedImport=false
import typing

from .services import upgrade_all_user_services, upgrade_all_publications, upgrade_all_providers
from .auths import upgrade_authenticators
from .osmanagers import upgrade_osmanagers

if typing.TYPE_CHECKING:
    from django.apps.registry import Apps
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor

def perform_upgrade(apps: 'Apps', schema_editor: 'BaseDatabaseSchemaEditor') -> None:
    """
    Entry point for migration 0051 RunPython.

    Forces lazy upgrade of legacy UserService and Publication data
    (old format → AutoSerializable) for all registered service types.
    """
    upgrade_all_user_services()
    upgrade_all_publications()
    upgrade_all_providers()
    upgrade_authenticators()
    upgrade_osmanagers()


def noop_reverse(apps: 'Apps', schema_editor: 'BaseDatabaseSchemaEditor') -> None:
    """
    Dummy reverse operation for migration 0051.
    The conversion is forward-compatible (old unmarshal still exists in v5),
    so no data-invalidating reverse is needed.
    """
    pass

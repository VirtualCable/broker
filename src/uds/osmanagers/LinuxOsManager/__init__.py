# -*- coding: utf-8 -*-

#
# Copyright (c) 2012-2023 Virtual Cable S.L.
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
@Author: Adolfo Gómez, dkmaster at dkmon dot com
@Author: Alexander Burmatov,  thatman at altlinux dot org
"""

# pyright: reportUnusedImport=false
import os.path
import sys
import typing

from django.utils.translation import gettext_noop as _

from uds.core.consts.system import VERSION
from uds.core.managers import downloads_manager

from .linux_ad_osmanager import LinuxOsADManager
from .linux_osmanager import LinuxOsManager
from .linux_randompass_osmanager import LinuxRandomPassManager

_mypath: typing.Final[str] = os.path.dirname(__spec__.origin)  # type: ignore[type-var, assignment]  # mypy has some problem with dirname??
# Old version, using spec is better, but we can use __package__ as well
# _mypath = os.path.dirname(typing.cast(str, sys.modules[__package__].__file__))  # pyright: ignore

downloads_manager().register(
    f"udsactor_{VERSION}_amd64-debian12.deb",
    _("UDS Actor for Debian 12 / Ubuntu based Linux machines"),
    _mypath + f"/files/udsactor_{VERSION}_amd64-debian12.deb",
    mimetype="application/x-debian-package",
)

downloads_manager().register(
    f"udsactor_{VERSION}_amd64-debian13.deb",
    _("UDS Actor for Debian 13 / Ubuntu based Linux machines"),
    _mypath + f"/files/udsactor_{VERSION}_amd64-debian13.deb",
    mimetype="application/x-debian-package",
)

downloads_manager().register(
    f"udsactor-unmanaged_{VERSION}_amd64-debian12.deb",
    _("UDS Actor for Debian 12 / Ubuntu based Linux machines. Used ONLY for static machines."),
    _mypath + f"/files/udsactor-unmanaged_{VERSION}_amd64-debian12.deb",
    mimetype="application/x-debian-package",
)

downloads_manager().register(
    f"udsactor-unmanaged_{VERSION}_amd64-debian13.deb",
    _("UDS Actor for Debian 13 / Ubuntu based Linux machines. Used ONLY for static machines."),
    _mypath + f"/files/udsactor-unmanaged_{VERSION}_amd64-debian13.deb",
    mimetype="application/x-debian-package",
)

downloads_manager().register(
    f"udsactor-{VERSION}.x86_64-fedora.rpm",
    _("UDS Actor for Fedora / RHEL based Linux machines"),
    _mypath + f"/files/udsactor-{VERSION}.x86_64-fedora.rpm",
    mimetype="application/x-redhat-package-manager",
)

downloads_manager().register(
    f"udsactor-{VERSION}.x86_64-opensuse.rpm",
    _("UDS Actor for openSUSE / SLES based Linux machines"),
    _mypath + f"/files/udsactor-{VERSION}.x86_64-opensuse.rpm",
    mimetype="application/x-redhat-package-manager",
)

downloads_manager().register(
    f"udsactor-unmanaged-{VERSION}.x86_64-fedora.rpm",
    _("UDS Actor for Fedora / RHEL based Linux machines. Used ONLY for static machines."),
    _mypath + f"/files/udsactor-unmanaged-{VERSION}.x86_64-fedora.rpm",
    mimetype="application/x-redhat-package-manager",
)

downloads_manager().register(
    f"udsactor-unmanaged-{VERSION}.x86_64-opensuse.rpm",
    _("UDS Actor for openSUSE / SLES based Linux machines. Used ONLY for static machines."),
    _mypath + f"/files/udsactor-unmanaged-{VERSION}.x86_64-opensuse.rpm",
    mimetype="application/x-redhat-package-manager",
)

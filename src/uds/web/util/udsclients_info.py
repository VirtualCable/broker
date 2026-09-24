import typing

from django.utils.translation import gettext
from django.templatetags.static import static
from uds.REST.methods.client import CLIENT_VERSION
from uds.core import types


# all plugins are under url clients...
PLUGINS: typing.Final[list[types.plugins.UDSClientPlugin]] = [
    types.plugins.UDSClientPlugin(
        url=static("clients/" + url.format(version=CLIENT_VERSION)),
        description=description,
        name=name,
        legacy=legacy,
    )
    for url, description, name, legacy in (
        (
            "UDSLauncherInstaller-{version}.exe",
            gettext("Windows launcher"),
            "Windows",
            False,
        ),
        (
            "UDSLauncher-{version}.pkg",
            gettext("macOS launcher (Apple Silicon)"),
            "MacOS",
            False,
        ),
        (
            "UDSLauncher-{version}-intel.pkg",
            gettext("macOS launcher (Intel)"),
            "MacOS",
            False,
        ),
        (
            "udslauncher_{version}_amd64-debian12.deb",
            gettext("Debian 12 Linux launcher"),
            "Linux",
            False,
        ),
        (
            "udslauncher_{version}_amd64-debian13.deb",
            gettext("Debian 13 / Ubuntu 24.04+ Linux launcher"),
            "Linux",
            False,
        ),
        (
            "udslauncher-{version}.x86_64-fedora.rpm",
            gettext("Fedora 41+ / RHEL 10 Linux launcher (RPM)"),
            "Linux",
            False,
        ),
        (
            "udslauncher-{version}.x86_64-opensuse.rpm",
            gettext("openSUSE Leap 15.6 / SLES 15 Linux launcher (RPM)"),
            "Linux",
            False,
        ),
        (
            "udslauncher-{version}-x86_64-appimage.AppImage",
            gettext("Portable AppImage Linux launcher (x86_64)"),
            "Linux",
            False,
        ),
    )
]

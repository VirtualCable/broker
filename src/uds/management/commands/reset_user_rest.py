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
Recovery command for REST path scopes (``allowed_paths``).

Removes the path scopes of one or more users (or all of them with
``--all``). Intended as the escape hatch when an administrator gets
locked out of the REST API by its own scopes, from the server shell:

    python manage.py reset_user_rest <username|uuid> ...
    python manage.py reset_user_rest --all
"""

import argparse
import logging
import typing

from django.core.management.base import BaseCommand, CommandError

from uds import models

logger: logging.Logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Removes REST path scopes (allowed_paths) from users"

    @typing.override
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "users",
            nargs="*",
            type=str,
            help="User names or uuids to clear (omit when using --all)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Clear REST path scopes from every user",
        )

    @typing.override
    def handle(self, *args: typing.Any, **options: typing.Any) -> None:
        users_arg: list[str] = options["users"]
        clear_all: bool = options["all"]

        if clear_all == bool(users_arg):
            raise CommandError("Specify either some user names/uuids or --all, but not both")

        if clear_all:
            queryset = models.User.objects.all()
        else:
            queryset = models.User.objects.filter(name__in=users_arg) | models.User.objects.filter(
                uuid__in=users_arg
            )
            found = {value for values in queryset.values_list("name", "uuid") for value in values}
            missing = set(users_arg) - found
            if missing:
                raise CommandError(f"No such users: {', '.join(sorted(missing))}")

        cleared = 0
        for user in queryset:
            if user.rest_allowed_paths:
                user.rest_allowed_paths = None
                cleared += 1
                self.stdout.write(f"Cleared REST path scopes for user '{user.name}'")

        if cleared == 0:
            self.stdout.write("No user had REST path scopes")
        else:
            self.stdout.write(f"{cleared} user(s) updated")

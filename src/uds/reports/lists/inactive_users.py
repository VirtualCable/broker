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
import csv
import datetime
import io
import logging
import typing

from django.utils.translation import gettext, gettext_lazy as _
from django.utils import timezone

from uds.core import types
from uds.core.consts import NEVER
from uds.core.ui import gui
from uds.models import Authenticator

from .base import ListReport

logger = logging.getLogger(__name__)


class InactiveUsersReport(ListReport):
    filename = 'inactive_users.pdf'
    name = _('Inactive users')
    description = _('Lists users that have not accessed UDS in N days')
    uuid = '7e2566b3-beb7-45d6-b2c5-57e572884e32'

    authenticator = gui.ChoiceField(
        label=_('Authenticator'),
        order=1,
        tooltip=_('Authenticator from where to list users (or all)'),
        required=True,
    )

    days = gui.NumericField(
        order=2,
        label=_('Inactive days'),
        length=4,
        min_value=1,
        max_value=3650,
        default=90,
        tooltip=_('Threshold of days without access to consider a user inactive'),
        required=True,
    )

    include_never = gui.CheckBoxField(
        order=3,
        label=_('Include never-accessed'),
        tooltip=_('Include users that never accessed UDS'),
        default=True,
    )

    def initialize(self, values: 'types.core.ValuesType') -> None:
        if values:
            if self.authenticator.value == '0-0-0-0':
                self.filename = 'inactive_users.pdf'
            else:
                try:
                    auth = Authenticator.objects.get(uuid=self.authenticator.value)
                    self.filename = f'inactive_users_{auth.name}.pdf'
                except Authenticator.DoesNotExist:
                    pass

    def init_gui(self) -> None:
        vals = [gui.choice_item('0-0-0-0', gettext('ALL AUTHENTICATORS'))] + [
            gui.choice_item(v.uuid, v.name) for v in Authenticator.objects.all().order_by('name')
        ]
        self.authenticator.set_choices(vals)

    def get_data(self) -> tuple[list[dict[str, typing.Any]], str]:
        days = self.days.as_int()
        threshold = timezone.now() - datetime.timedelta(days=days)

        if self.authenticator.value == '0-0-0-0':
            auths = list(Authenticator.objects.all())
            auth_label = gettext('All')
        else:
            auths = list(Authenticator.objects.filter(uuid=self.authenticator.value))
            auth_label = auths[0].name if auths else ''

        now = timezone.now()
        rows: list[dict[str, typing.Any]] = []
        for a in auths:
            qs = a.users.filter(last_access__lt=threshold)
            if not self.include_never.as_bool():
                qs = qs.exclude(last_access=NEVER)
            for u in qs.order_by('last_access').values('name', 'real_name', 'last_access'):
                if u['last_access'] == NEVER:
                    inactive_days: typing.Any = gettext('Never')
                    last_access: typing.Any = gettext('Never')
                else:
                    inactive_days = (now - u['last_access']).days
                    last_access = u['last_access']
                rows.append(
                    {
                        'auth': a.name,
                        'name': u['name'],
                        'real_name': u['real_name'],
                        'last_access': last_access,
                        'inactive_days': inactive_days,
                    }
                )

        return rows, auth_label

    def generate(self) -> bytes:
        rows, auth_label = self.get_data()
        return self.template_as_pdf(
            'uds/reports/lists/inactive-users.html',
            dct={
                'data': rows,
                'auth': auth_label,
                'days': self.days.as_int(),
            },
            header=gettext('Inactive users (>{} days)').format(self.days.as_int()),
            water=gettext('UDS Report of inactive users'),
        )


class InactiveUsersReportCSV(InactiveUsersReport):
    filename = 'inactive_users.csv'
    mime_type = 'text/csv'
    encoded = False
    uuid = '07c6eb22-1f38-4ee2-9152-dd77a470388d'

    authenticator = InactiveUsersReport.authenticator
    days = InactiveUsersReport.days
    include_never = InactiveUsersReport.include_never

    def initialize(self, values: 'types.core.ValuesType') -> None:
        if values:
            if self.authenticator.value == '0-0-0-0':
                self.filename = 'inactive_users.csv'
            else:
                try:
                    auth = Authenticator.objects.get(uuid=self.authenticator.value)
                    self.filename = f'inactive_users_{auth.name}.csv'
                except Authenticator.DoesNotExist:
                    pass

    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext('Authenticator'),
                gettext('User ID'),
                gettext('Real Name'),
                gettext('Last access'),
                gettext('Inactive days'),
            ]
        )
        rows, _auth_label = self.get_data()
        for v in rows:
            writer.writerow([v['auth'], v['name'], v['real_name'], v['last_access'], v['inactive_days']])
        return output.getvalue().encode()

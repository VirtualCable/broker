
#
# Copyright (c) 2014-2019 Virtual Cable S.L.
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
"""

import logging

from uds import models
from uds.core import consts
from uds.core import exceptions
from uds.core import types
from uds.core.ui import gui
from uds.REST import Handler

logger = logging.getLogger(__name__)

# Enclosed methods under /auth path


class Callback(Handler):
    """
    API:
        Executes a callback from the GUI. Internal use, not intended to be called from outside.
    """

    PATH = "gui"

    ROLE = consts.Role.STAFF

    def get(self) -> types.ui.CallbackResultType:
        if len(self._args) != 1:
            raise exceptions.rest.RequestError("Invalid Request")

        if self._args[0] not in gui.callbacks:
            raise exceptions.rest.NotFound("callback {0} not found".format(self._args[0]))

        # Copy so we don't mutate the handler's params on subsequent calls.
        params = dict(self._params)
        cb_ticket = params.pop("cb_ticket", None)
        if cb_ticket:
            try:
                ticket_data = models.TicketStore.get(cb_ticket, invalidate=False)
            except models.TicketStore.DoesNotExist:
                raise exceptions.rest.RequestError("Invalid or expired cb_ticket")
            # Ticket data wins on collision with the query-string params.
            params.update(ticket_data)

        return gui.callbacks[self._args[0]](params)

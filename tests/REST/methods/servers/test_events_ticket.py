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
"""

import logging

from uds import models

from ....fixtures import servers as servers_fixtures
from ....utils import rest

logger = logging.getLogger(__name__)


class ServerEventsTicketTest(rest.test.RESTTestCase):
    """
    Test that the (removed) server "ticket" event is rejected
    """

    def test_event_ticket_is_rejected(self) -> None:
        """
        The "ticket" event was removed: a request with type=ticket must be
        rejected as an invalid event type and never touch the TicketStore.
        """
        server = servers_fixtures.create_server()
        ticket_id = models.TicketStore.create({"type": "transport", "secret": "s3cr3t"})

        response = self.client.rest_post(
            "/servers/event",
            data={
                "token": server.token,
                "type": "ticket",
                "ticket": ticket_id,
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["result"], "error")
        self.assertIn("error", data)
        self.assertIn("Invalid event type", data["error"])

        # The ticket must remain untouched (never consumed)
        self.assertIsNotNone(models.TicketStore.objects.get(uuid=ticket_id))

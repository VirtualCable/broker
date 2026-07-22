# -*- coding: utf-8 -*-
#
# Copyright (c) 2024 Virtual Cable S.L.
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
import typing

from uds import reports

from tests.utils.test import UDSTestCase


logger = logging.getLogger(__name__)

# UUid of the reports
# Here we only want to ensure the code has no errors, so we only check that they load correctly
MUST_HAVE: typing.Final[list[str]] = [
    "a5a43bc0-d543-11ea-af8f-af01fa65994e",
    "5da93a76-1849-11e5-ac1a-10feed05884b",
    "8cd1cfa6-ed48-11e4-83e5-10feed05884b",
    "b5f5ebc8-44e9-11ed-97a9-efa619da6a49",
    "765b5580-1840-11e5-8137-10feed05884b",
    "0f62f19a-f166-11e4-8f59-10feed05884b",
    "6445b526-24ce-11e5-b3cb-10feed05884b",
    "88932b48-1fd3-11e5-a776-10feed05884b",
    "1491148a-2fc6-11e7-a5ad-03d9a417561c",
    "0b429f70-2fc6-11e7-9a2a-8fc37101e66a",
    "5f7f0844-beb1-11e5-9a96-10feed05884b",
    "811b1261-82c4-524e-b1c7-a4b7fe70050f",
    "aba55fe5-c4df-5240-bbe6-36340220cb5d",
    "38ec12dc-beaf-11e5-bd0a-10feed05884b",
    "302e1e76-30a8-11e7-9d1e-6762bbf028ca",
    "202c6438-30a8-11e7-80e4-77c1e4cb9e09",
    "07c6eb22-1f38-4ee2-9152-dd77a470388d",
    "7e2566b3-beb7-45d6-b2c5-57e572884e32",
    "d4e24000-a281-4dba-9eb1-02e5e171897a",
    "34507a71-7d45-4f80-a2ca-b9acafe5aea6",
    "46d0befa-843c-495e-a97d-9e32f57a12bc",
    "51e81239-4fd8-4f0f-9f46-d2111295d978",
    "5022e2f4-866a-4a70-86bc-2ce2d9b6f6bb",
    "5aa4bcf9-cbff-4e9a-81a3-16915347a77a",
    "68cb4370-6ff2-4846-87a7-da59acbd89a2",
    "6bd74f52-7ce3-4877-88cd-153fe3781801",
    "7305fcca-41ce-45ce-bb3a-3579251fb34a",
    "78fe8f92-5fed-4f6b-8257-825df9d767a7",
    "7dc8eab2-4fae-454f-9dfe-8a4f4d96f106",
    "8ae87ec8-7fdd-4772-b86e-51bde4d61b80",
    "939c621e-6d9b-4177-8b62-982284e850a5",
    "b0e38c72-a135-4f92-8700-b00753598bb2",
    "b7797d95-548a-44bb-a061-4d1f9ad34eeb",
    "ca1a7f6d-a4f6-43e4-abc6-43692ba40190",
    "eb31b347-0f68-4509-b807-988815ae53ee",
    "ef4be537-f19b-44bc-bb9b-8fa279d2371f",
    "26638387-4d35-4dd1-a7d6-bd45d0c3dcf4",
    "315921b4-b838-4178-a312-7bb75a8d58c4",
    "318ad9e3-e5ed-404d-bd57-d3db2fc63556",
    "3948fd95-0117-41fe-a9ca-7c6efedd4f79",
]


class TestReports(UDSTestCase):
    """
    Test known transports are registered correctly
    """

    def test_reports_loads_correctly(self) -> None:
        # Reports loaded at top level

        for i in reports.available_reports:
            self.assertIn(i.uuid, MUST_HAVE)

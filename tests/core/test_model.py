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
Author: Adolfo Gomez, dkmaster at dkmon dot com
"""
from tests.utils.test import UDSTestCase
from uds.core.util.model import process_uuid


class TestProcessUuid(UDSTestCase):
    def test_valid_lowercase(self) -> None:
        uuid = '550e8400-e29b-41d4-a716-446655440000'
        self.assertEqual(process_uuid(uuid), uuid)

    def test_valid_uppercase(self) -> None:
        result = process_uuid('550E8400-E29B-41D4-A716-446655440000')
        self.assertEqual(result, '550e8400-e29b-41d4-a716-446655440000')

    def test_valid_bytes(self) -> None:
        result = process_uuid(b'550E8400-E29B-41D4-A716-446655440000')
        self.assertEqual(result, '550e8400-e29b-41d4-a716-446655440000')

    def test_invalid_chars(self) -> None:
        with self.assertRaises(ValueError):
            process_uuid('not-a-uuid')

    def test_invalid_no_hyphens(self) -> None:
        with self.assertRaises(ValueError):
            process_uuid('550e8400e29b41d4a716446655440000')

    def test_invalid_empty(self) -> None:
        with self.assertRaises(ValueError):
            process_uuid('')

    def test_invalid_short(self) -> None:
        with self.assertRaises(ValueError):
            process_uuid('550e8400')

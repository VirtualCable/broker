# -*- coding: utf-8 -*-
#
# Copyright (c) 2012-2025 Virtual Cable S.L.U.
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
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
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

import typing

from django.db import models
from django.core.exceptions import ValidationError

from uds.core.util.model import sql_now

HASH_SIZE = 32  # SHA-256


class ImmutableLog(models.Model):
    """
    Immutable audit log entry, linked via hash chain (blockchain-like).

    Each entry's ``entry_hash`` is computed as::

        SHA-256(previous_hash | stamp | sequence | data_length | data)

    Once created, entries cannot be updated or deleted — the chain
    guarantees tamper-evidence.
    """

    sequence = models.BigIntegerField(unique=True, editable=False)
    stamp = models.DateTimeField(default=sql_now, editable=False, db_index=True)
    anchor = models.BooleanField(default=False, editable=False)
    """True if this entry is a genesis or re-anchor point (contains a TSA token)."""
    previous_hash = models.BinaryField(max_length=HASH_SIZE, editable=False)
    data = models.BinaryField(editable=False)
    entry_hash = models.BinaryField(max_length=HASH_SIZE, editable=False, unique=True)

    class Meta:
        app_label = "uds"
        ordering = ["sequence"]
        get_latest_by = "sequence"
        db_table = "uds_immutable_log"
        verbose_name = "Immutable Log Entry"
        verbose_name_plural = "Immutable Log Entries"

    def __repr__(self) -> str:
        return f"<ImmutableLog #{self.sequence} hash={self.entry_hash.hex()[:16]}...>"

    @typing.override
    def save(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        if self.pk is not None:
            raise ValidationError("ImmutableLog entries cannot be updated.")
        super().save(*args, **kwargs)

    @typing.override
    def delete(self, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        raise ValidationError("ImmutableLog entries cannot be deleted.")

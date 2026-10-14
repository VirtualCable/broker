#
# Copyright (c) 2012-2026 Virtual Cable S.L.
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
WebAuthn credentials storage, used by the biometric login facility.

Each credential is a serializable ``StoredCredential`` value object
(uds.core.types.webauthn), kept as one encrypted property of the owning user
(see ``User.webauthn_credentials``):
* No database model/migration is needed.
* Data is deleted automatically when the user is deleted (user properties are
  cleared on ``pre_delete``, see ``PropertiesMixin.setup_signals``).

The vault is an opaque, client-encrypted blob: UDS can never decrypt it, so
leaking this data does not reveal any user password (and the PRF salt is
rotated on every successful unlocking login, invalidating leaked blobs).
"""

import collections.abc
import time
import typing

from uds.core.consts.webauthn import PROPERTY_PREFIX
from uds.core.types.webauthn import StoredCredential


def _key_for(credential_id: str) -> str:
    """Property key for a credential id."""
    return PROPERTY_PREFIX + credential_id


def _is_webauthn_key(key: str) -> bool:
    return key.startswith(PROPERTY_PREFIX)


def now_epoch() -> float:
    """Current time in epoch seconds (credentials timestamps)."""
    return time.time()


class StoredCredentialsAccessor:
    """
    Bound accessor to the WebAuthn credentials of an user, stored as that
    user's encrypted properties (serialized ``StoredCredential`` values).
    """

    def __init__(self, user: "models.User") -> None:
        self._user = user

    # -- queries ------------------------------------------------------------

    def all(
        self,
        rp_id: str | None = None,
        *,
        enabled_only: bool = False,
    ) -> list[StoredCredential]:
        """All credentials of the user, latest created first (rp filtered if given)."""
        credentials: list[StoredCredential] = []
        properties = self._user.encrypted_properties
        for key in list(properties.keys()):
            if not _is_webauthn_key(key):
                continue
            credential = StoredCredential.from_dict(typing.cast("dict[str, typing.Any]", properties[key]))
            if credential is None:
                continue
            credential.credential_id = key.removeprefix(PROPERTY_PREFIX)
            if rp_id is not None and credential.rp_id != rp_id:
                continue
            if enabled_only and not credential.enabled:
                continue
            credentials.append(credential)
        credentials.sort(key=lambda c: c.created, reverse=True)
        return credentials

    def __iter__(self) -> collections.abc.Iterator[StoredCredential]:
        return iter(self.all())

    def __len__(self) -> int:
        return len(self.all())

    def get(self, credential_id: str) -> StoredCredential | None:
        """Returns the credential with that id, or None."""
        data = self._user.encrypted_properties.get(_key_for(credential_id))
        if data is None:
            return None
        credential = StoredCredential.from_dict(typing.cast("dict[str, typing.Any]", data))
        if credential is not None:
            credential.credential_id = credential_id  # ensure consistency with the key
        return credential

    def latest(self, rp_id: str) -> StoredCredential | None:
        """Latest usable credential of the user for the given rp (or None)."""
        usable = self.all(rp_id=rp_id, enabled_only=True)
        return usable[0] if usable else None

    # -- mutations ----------------------------------------------------------

    def add(self, credential: StoredCredential) -> None:
        """Creates or updates a credential (upsert by credential id)."""
        self._user.encrypted_properties[_key_for(credential.credential_id)] = credential.to_dict()

    def remove(self, credential_id: str) -> None:
        """Removes a credential (if present)."""
        try:
            del self._user.encrypted_properties[_key_for(credential_id)]
        except KeyError:
            pass

    def remove_all(self, rp_id: str | None = None) -> int:
        """Removes all credentials (rp filtered if given). Returns removed count."""
        removed = 0
        for credential in self.all(rp_id=rp_id):
            self.remove(credential.credential_id)
            removed += 1
        return removed


# Only for type checking (avoid runtime circular imports)
if typing.TYPE_CHECKING:
    from uds import models

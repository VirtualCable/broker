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
WebAuthn (biometric login) views for the UDS web interface.

These endpoints run "beside" the login page (they are all pre authentication)
and are protected by the standard Django CSRF middleware (the Angular user
interface sends the ``X-CSRFToken`` header automatically).

Flow overview:

Registration (saving the biometric unlock for a user):

1. While the user fills the login form, the client asks ``save_start`` for a
   challenge and a pair of PRF salts (active + next, rotation ready).

2. The client registers the WebAuthn credential (fingerprint touch #1),
   evaluates the PRF salts (touch #2) and derives the vault key, encrypting
   the packet with the unified ``salt`` & freshness.  Everything RESTs in the
   session, waiting for the final form POST.

3. ``uds.web.views.auth.login`` processes a successful login, and ONLY THEN
   commits the pending registration to the database.

Biometric login (unlocking):

1. ``biometric_start`` returns a server issued challenge for the stored
   credential plus the active PRF salt (to unwrap the vault) and a fresh one
   (to rotate re-encrypting the vault in the same user touch).

2. The client unwraps the vault with the key derived from the PRF secret and
   submits the normal login form.

3. ``biometric_finish`` verifies the assertion server side (challenge,
   origin, rpIdHash, signature, sign counters) BEFORE releasing the
   client-encrypted blob: only a fingerprint verified session can obtain it.

Security invariants:

* The server never receives, stores or derives the PRF secret; the vault is
  an opaque blob useless without the authenticator + fingerprint.
* Rotation on every successful biometric login turns any leaked vault into
  an inert blob the next time the user unlocks.
"""

import json
import logging
import time
import typing

from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from uds import models
from uds.core import consts
from uds.core import types
from uds.core.managers.crypto import CryptoManager
from uds.core.managers.crypto.webauthn import (
    WebAuthnError,
    b64url_decode,
    b64url_encode,
    verify_assertion,
    verify_registration,
)
from uds.core.types.webauthn import WebAuthnResponse
from uds.core.util import webauthn_credentials
from uds.core.util.config import GlobalConfig

logger = logging.getLogger(__name__)

# Session keys and pending-registration age are on "consts.webauthn"
# (aliased as consts in imports above)


def biometric_enabled() -> bool:
    """True if the biometric login facility is usable at all."""
    if GlobalConfig.ENFORCE_ZERO_TRUST.as_bool(False):
        return False  # Zero trust forbids caching credentials in any way
    return GlobalConfig.ALLOW_BIOMETRIC_AUTH.as_bool()


def _rp_and_origin(request: HttpRequest) -> tuple[str, str]:
    """Returns (rp_id, origin) for the current request.

    Note:
        rp.id is the effective domain (no port). WebAuthn requires a real
        domain and a secure context, so IP-only deployments cannot use this
        facility.
    """
    host = request.get_host()
    rp_id = host.rsplit(":", 1)[0].lstrip("[").rstrip("]")
    origin = f"{request.scheme}://{host}"
    return rp_id, origin


def _json_error(error: str, status: int = 400) -> JsonResponse:
    """Helper for error json responses."""
    return JsonResponse({"error": error}, status=status)


def _read_body(request: HttpRequest) -> dict[str, typing.Any]:
    """Reads and validates a JSON object request body."""
    try:
        data: typing.Any = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid JSON body") from None
    if not isinstance(data, dict):
        raise TypeError("JSON body is not an object")
    body: dict[str, typing.Any] = typing.cast("dict[str, typing.Any]", data)
    return body


def _get_user(username: str, auth_id: str) -> models.User | None:
    """Locates an active user by name and authenticator (uuid), or None."""
    user = models.User.objects.filter(name=username, manager__uuid=auth_id).first()
    if user and types.states.State.from_str(user.state).is_active():
        return user
    return None


def _get_credential(
    user: models.User,
    rp_id: str,
    credential_id: str | None,
) -> types.webauthn.StoredCredential | None:
    """Locates a credential of the user for this rp (specific, or latest usable)."""
    if credential_id:
        return user.webauthn_credentials.get(credential_id)
    return user.webauthn_credentials.latest(rp_id)


# ---------------------------------------------------------------------------
# Biometric login (vault unlock)
# ---------------------------------------------------------------------------


@require_POST
@never_cache
def biometric_start(request: HttpRequest) -> JsonResponse:
    """Starts the biometric login flow.

    Body: {"auth_id": <id>, "username": <str>} (optionally "credential_id")

    Returns the challenge for the stored credential and the PRF salts needed
    by the client to unwrap (active) and rotate (next) the vault. If there is
    no usable credential, returns {"available": False} (no user details).
    """
    if not biometric_enabled():
        return _json_error("Biometric login is disabled", status=403)

    try:
        data = _read_body(request)
    except ValueError as e:
        return _json_error(str(e))

    username = str(data.get("username", ""))
    auth_id = str(data.get("auth_id", "") or "")
    credential_id = str(data.get("credential_id") or "")

    if not username or not auth_id:
        return _json_error("Missing required parameters")

    user = _get_user(username, auth_id)
    if user is None:
        # Generic response, user existence is not disclosed
        return JsonResponse({"available": False})

    rp_id, _origin = _rp_and_origin(request)
    credential = _get_credential(user, rp_id, credential_id if credential_id else None)
    if credential is None or not credential.vault:
        return JsonResponse({"available": False})

    challenge = CryptoManager.manager().random_bytes(consts.webauthn.CHALLENGE_SIZE)
    next_salt = CryptoManager.manager().random_bytes(consts.webauthn.SALT_SIZE)

    request.session[consts.webauthn.SESSION_UNLOCK_DATA] = {
        "challenge": b64url_encode(challenge),
        "user_id": user.id,
        "credential": credential.credential_id,
        "next_salt": b64url_encode(next_salt),
        "previous_sign_count": credential.sign_count,
    }

    return JsonResponse(
        {
            "available": True,
            "challenge": b64url_encode(challenge),
            "credential_id": credential.credential_id,
            "salt": credential.prf_salt,  # active salt, to unwrap the vault
            "next_salt": b64url_encode(next_salt),  # fresh salt for rotation
        }
    )


@require_POST
@never_cache
def biometric_finish(request: HttpRequest) -> JsonResponse:
    """Finishes the biometric login flow.

    Body: {"client_data": ..., "auth_data": ..., "signature": ...} (all
    base64url encoded fields from the client assertion).

    Verifies the assertion (server issued challenge, origin, rpIdHash,
    signature and sign counter). On success, releases the opaque,
    client-encrypted vault blob, so the client can decrypt it (key from the
    PRF secret) and submit the normal login form. The session keeps the
    rotation data, applied ONLY by a later successful login POST
    (see auth.login / apply_pending_unlock).
    """
    if not biometric_enabled():
        return _json_error("Biometric login is disabled", status=403)

    try:
        data = _read_body(request)
    except ValueError as e:
        return _json_error(str(e))

    session_data = request.session.get(consts.webauthn.SESSION_UNLOCK_DATA)
    if not session_data:
        return _json_error("No biometric login in progress")

    user = models.User.objects.filter(id=session_data["user_id"]).first()
    if user is None or not types.states.State.from_str(user.state).is_active():
        request.session.pop(consts.webauthn.SESSION_UNLOCK_DATA, None)
        return _json_error("Invalid credential")
    credential = user.webauthn_credentials.get(str(session_data["credential"]))
    if credential is None or not credential.enabled:
        request.session.pop(consts.webauthn.SESSION_UNLOCK_DATA, None)
        return _json_error("Invalid credential")

    _rp_id, origin = _rp_and_origin(request)

    try:
        response = WebAuthnResponse(
            client_data_json=b64url_decode(str(data.get("client_data", ""))),
            authenticator_data=b64url_decode(str(data.get("auth_data", ""))),
            signature=b64url_decode(str(data.get("signature", ""))),
        )
        new_sign_count = verify_assertion(
            rp_id=credential.rp_id,
            origin=origin,
            challenge=b64url_decode(str(session_data["challenge"])),
            cose_key=b64url_decode(credential.public_key),
            previous_sign_count=int(session_data.get("previous_sign_count", credential.sign_count)),
            response=response,
        )
    except (WebAuthnError, ValueError) as e:
        logger.warning("Biometric verification failed: %s", e)
        request.session.pop(consts.webauthn.SESSION_UNLOCK_DATA, None)
        return _json_error(f"Biometric verification failed: {e}")

    # Assertion is fine: update counter state and release the vault
    credential.sign_count = new_sign_count
    credential.last_used = webauthn_credentials.now_epoch()
    user.webauthn_credentials.add(credential)

    request.session[consts.webauthn.SESSION_UNLOCK_DATA] = {
        "user_id": user.id,
        "credential": credential.credential_id,
        "new_salt": session_data["next_salt"],
        "verified": True,
    }

    return JsonResponse(
        {
            "ok": True,
            "username": user.name,
            "auth_id": user.manager.uuid,  # authenticator uuid, as used by the GUI
            "blob": credential.vault,  # opaque client-encrypted vault blob
        }
    )


# ---------------------------------------------------------------------------
# Registration (saving the unlock for later)
# ---------------------------------------------------------------------------


@require_POST
@never_cache
def save_start(request: HttpRequest) -> JsonResponse:
    """Starts the "register biometric credential" flow.

    Body: {"auth_id": <id>, "username": <str>}

    The user has just typed the password in the login form that will be
    submitted a moment later. The challenge, the PRF salts pair and the user
    handle are prepared here; nothing is persisted to the database until
    auth.login sees a successful authentication, so a wrong password leaves
    no trace.
    """
    if not biometric_enabled():
        return _json_error("Biometric login is disabled", status=403)

    try:
        data = _read_body(request)
    except ValueError as e:
        return _json_error(str(e))

    username = str(data.get("username", ""))
    auth_id = str(data.get("auth_id", "") or "")

    if not username or not auth_id:
        return _json_error("Missing required parameters")

    user = _get_user(username, auth_id)
    if user is None:
        return _json_error("Invalid user")

    rp_id, _origin = _rp_and_origin(request)

    challenge = CryptoManager.manager().random_bytes(consts.webauthn.CHALLENGE_SIZE)
    active_salt = CryptoManager.manager().random_bytes(consts.webauthn.SALT_SIZE)
    next_salt = CryptoManager.manager().random_bytes(consts.webauthn.SALT_SIZE)
    user_handle = CryptoManager.manager().random_bytes(16)

    request.session[consts.webauthn.SESSION_SAVE_DATA] = {
        "challenge": b64url_encode(challenge),
        "active_salt": b64url_encode(active_salt),
        "next_salt": b64url_encode(next_salt),
        "user_handle": b64url_encode(user_handle),
        "user_id": user.id,
        "rp_id": rp_id,
        "created": time.time(),
    }

    return JsonResponse(
        {
            "challenge": b64url_encode(challenge),
            "user": {
                "name": user.name,
                "display_name": user.real_name or user.name,
                "handle": b64url_encode(user_handle),
            },
            "salts": {
                "active": b64url_encode(active_salt),
                "next": b64url_encode(next_salt),
            },
        }
    )


@require_POST
@never_cache
def save_finish(request: HttpRequest) -> JsonResponse:
    """Finishes the "register biometric credential" flow.

    Body: {"client_data": ..., "attestation_object": ..., "vault": ...} (all
    base64url encoded, except vault which is the opaque client blob).

    Verifies the attestation (challenge, origin, rpIdHash, user verification,
    attestation format...) and keeps everything on the session, waiting for
    the login POST to commit it if authentication succeeds.
    """
    if not biometric_enabled():
        return _json_error("Biometric login is disabled", status=403)

    try:
        data = _read_body(request)
    except ValueError as e:
        return _json_error(str(e))

    session_data = request.session.get(consts.webauthn.SESSION_SAVE_DATA)
    if not session_data:
        return _json_error("No biometric registration in progress")

    vault = str(data.get("vault", ""))
    if not vault:
        return _json_error("Missing vault data")

    rp_id = str(session_data["rp_id"])
    origin = f"{request.scheme}://{request.get_host()}"

    try:
        verified = verify_registration(
            rp_id=rp_id,
            origin=origin,
            challenge=b64url_decode(str(session_data["challenge"])),
            response=WebAuthnResponse(
                client_data_json=b64url_decode(str(data.get("client_data", ""))),
                attestation_object=b64url_decode(str(data.get("attestation_object", ""))),
            ),
        )
    except (WebAuthnError, ValueError) as e:
        logger.warning("Biometric registration verification failed: %s", e)
        request.session.pop(consts.webauthn.SESSION_SAVE_DATA, None)
        return _json_error(f"Biometric registration failed: {e}")

    request.session[consts.webauthn.SESSION_SAVE_DATA] = {
        **session_data,
        "credential_id": b64url_encode(verified.credential_id),
        "aaguid": verified.aaguid.hex(),
        "public_key": b64url_encode(verified.cose_key),
        "sign_count": verified.sign_count,
        "vault": vault,
    }

    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# Pending state handling, used by the login view (auth.login)
# ---------------------------------------------------------------------------


def clear_pending_state(request: HttpRequest) -> None:
    """Removes any pending WebAuthn state (registration or unlock)."""
    request.session.pop(consts.webauthn.SESSION_SAVE_DATA, None)
    request.session.pop(consts.webauthn.SESSION_UNLOCK_DATA, None)


def apply_pending_save(request: HttpRequest, user: models.User) -> None:
    """Commits a pending registration after a successful login.

    The pending registration must be recent and must belong to the user that
    just logged in, to avoid attaching stale or foreign pending data.
    """
    session_data = request.session.get(consts.webauthn.SESSION_SAVE_DATA)
    request.session.pop(consts.webauthn.SESSION_SAVE_DATA, None)
    if not session_data:
        return

    if session_data.get("user_id") != user.id:
        logger.warning("Pending biometric registration user mismatch, discarding")
        return
    if time.time() - float(session_data.get("created", 0)) > consts.webauthn.PENDING_SAVE_MAX_AGE:
        logger.info("Pending biometric registration expired, discarding")
        return

    user.webauthn_credentials.add(
        types.webauthn.StoredCredential(
            credential_id=str(session_data["credential_id"]),
            rp_id=str(session_data["rp_id"]),
            user_handle=str(session_data["user_handle"]),
            aaguid=str(session_data.get("aaguid", "")),
            public_key=str(session_data["public_key"]),
            sign_count=int(session_data.get("sign_count", 0)),
            prf_salt=str(session_data["active_salt"]),
            vault=str(session_data["vault"]),
            generation=0,
            created=webauthn_credentials.now_epoch(),
            last_used=None,
        )
    )
    logger.info("Biometric credential registered for user %s", user.name)


def apply_pending_unlock(request: HttpRequest, user: models.User, rotated_vault: str) -> None:
    """Applies the vault rotation after a successful biometric login.

    The unlock state exists only if a valid assertion was accepted for this
    session (``session_data.get('verified')``), and the rotated vault comes
    from the POST of the user that just successfully logged in.
    """
    session_data = request.session.get(consts.webauthn.SESSION_UNLOCK_DATA)
    request.session.pop(consts.webauthn.SESSION_UNLOCK_DATA, None)
    if not session_data or not session_data.get("verified"):
        return

    credential = user.webauthn_credentials.get(str(session_data["credential"]))
    if credential is None:
        return

    credential.prf_salt = str(session_data["new_salt"])
    credential.vault = rotated_vault
    credential.generation = credential.generation + 1
    user.webauthn_credentials.add(credential)
    logger.info("Biometric vault rotated for user %s (generation %s)", user.name, credential.generation)


def discard_pending_unlock(request: HttpRequest) -> None:
    """Discards the pending unlock state after a FAILED login.

    The unlocked vault was wrong most probably because the user password
    changed (or expires), so the stored credential is invalidated: the user
    will login normally, and can re-register later.
    """
    session_data = request.session.pop(consts.webauthn.SESSION_UNLOCK_DATA, None)
    if not session_data or not session_data.get("verified"):
        return

    user = models.User.objects.filter(id=session_data.get("user_id")).first()
    if user is None:
        return
    while user.webauthn_credentials.remove(str(session_data["credential"])):
        pass
    user.webauthn_credentials.remove(str(session_data["credential"]))
    logger.info("Biometric credential %s invalidated after login failure", session_data["credential"])

# BSD 3-Clause License
# Copyright (c) 2026, Virtual Cable S.L.
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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
Author: Andres Schumann, aschumann at virtualcable dot es
"""
# pyright: reportUnknownMemberType=false, reportMissingModuleSource=false

import collections.abc
import hashlib
import logging
import typing

from django.conf import settings
from django.utils.translation import gettext as _

# Native (Rust) LDAP client (uds-ldap-client package). This is the only
# backend: the previous pure-Python ldap3 dependency is gone.
from uds_ldap_client import AlreadyExistsError as NativeAlreadyExistsError
from uds_ldap_client import BindError as NativeBindError
from uds_ldap_client import Connection as NativeConnection
from uds_ldap_client import LdapError as NativeLdapError

from uds.core.util import net as util_net
from uds.core.util import utils
from uds.core.util.backoff import Backoff
from uds.core.util.cache import Cache
from uds.core.util.cache import CacheLike

logger = logging.getLogger(__name__)

# Search scopes (RFC 4511), matching the native client values.
SCOPE_BASE = 0
SCOPE_ONELEVEL = 1
SCOPE_SUBTREE = 2

# Asks the server for no attributes: only the entry, with its DN
NO_ATTRIBUTES = "1.1"

# Modify operations, as expected by the native client
MODIFY_ADD = "add"
MODIFY_DELETE = "delete"
MODIFY_REPLACE = "replace"
MODIFY_INCREMENT = "increment"

LDAP_ALREADY_EXISTS_RESULT_CODES = frozenset({20, 68})

LDAPResultType = dict[str, typing.Any]

LDAPConnection = NativeConnection


class LDAPError(Exception):
    @staticmethod
    def reraise(e: typing.Any) -> typing.NoReturn:
        _str = _("Connection error: ")
        _str += str(e)
        raise LDAPError(_str) from e


class AlreadyExistsError(LDAPError):
    pass


class BindError(LDAPError):
    """The server answered but rejected the credentials.

    Split out from a plain :class:`LDAPError` because a rejected bind says
    nothing about the health of the server: callers that keep per-host
    backoff must not penalise a DC for a wrong password.
    """


ALREADY_EXISTS = AlreadyExistsError


class LDAPReferralError(LDAPError):
    """
    Raised when an LDAP search returns referrals that the caller may follow.

    The list of referral URIs (as returned by the LDAP server) is available
    on the ``referrals`` attribute. Callers can decide whether to follow them,
    drop them, or honour an allow-list. Entries returned before the referrals
    are available on ``partial_results``.

    The default ``as_dict`` / ``first`` behaviour is to silently drop
    referrals; pass ``raise_on_referrals=True`` to surface them instead.
    """

    referrals: list[str]
    partial_results: list[LDAPResultType]

    def __init__(
        self,
        referrals: collections.abc.Iterable[str],
        partial_results: collections.abc.Iterable[LDAPResultType] = (),
    ) -> None:
        normalized: list[str] = [r for r in referrals if r]
        super().__init__(f"LDAP referrals: {normalized}")
        self.referrals = normalized
        self.partial_results = list(partial_results)


def escape(value: str) -> str:
    """
    Escape filter chars for ldap search filter
    """
    return (
        value.replace("\\", "\\5c")
        .replace("*", "\\2a")
        .replace("(", "\\28")
        .replace(")", "\\29")
        .replace("\0", "\\00")
    )


def connection(
    username: str,
    passwd: str,
    host: str,
    *,
    port: int = -1,
    read_only: bool = True,  # Most times we want read-only connections, so default to True
    use_ssl: bool = False,
    timeout: int = 3,
    debug: bool = False,
    verify_ssl: bool = True,
    certificate_data: str | None = None,  # Content of the certificate, not the file itself
) -> LDAPConnection:
    """
    Connects and binds to the LDAP server using the native client.

    A rejected bind raises :class:`BindError` (carrying the server
    diagnostic message, so callers can detect AD sub-status codes such as
    "data 773"); any other failure raises :class:`LDAPError`.

    Note: ``read_only`` is not enforced by the native client; the
    enforcement is left to the callers performing modifications.
    """
    logger.debug("Login in to %s as user %s", host, username)

    if port == -1:
        port = 636 if use_ssl else 389
    min_tls_version = getattr(settings, "SECURE_MIN_TLS_VERSION", None) or None
    ciphers = getattr(settings, "SECURE_CIPHERS", None) or None
    try:
        return NativeConnection(
            host,
            port,
            username,
            passwd,
            use_ssl=use_ssl,
            verify_ssl=verify_ssl,
            # Empty string behaves as "no custom CA" (system store).
            ca_certs_data=certificate_data or None,
            min_tls_version=min_tls_version,
            ciphers=ciphers,
            timeout=float(timeout) if timeout and timeout > 0 else None,
        )
    except NativeBindError as e:
        logger.error("Could not bind to LDAP server %s as user %s", host, username)
        # The native BindError message already carries the server diagnostic
        # text (e.g. AD "AcceptSecurityContext error, data 773, ..."), needed
        # by callers that key off specific sub-status codes (AD password
        # expired / must reset).
        raise BindError(str(e)) from e
    except NativeLdapError as e:
        logger.exception("Exception connecting:")
        raise LDAPError(str(e)) from e


def as_dict(
    con: LDAPConnection,
    base: str,
    ldap_filter: str,
    *,
    attributes: collections.abc.Iterable[str] | None = None,
    limit: int = 100,
    scope: int = SCOPE_SUBTREE,
    raise_on_referrals: bool = False,
) -> collections.abc.Generator[LDAPResultType, None, None]:
    """
    Makes a search on LDAP, returns a generator with the results, where each result is a dictionary where values are always a list of strings

    If ``raise_on_referrals`` is True, raises :class:`LDAPReferralError`
    when the server returned referrals for the search (instead of silently
    dropping them, which is the default behaviour). The caller is then
    responsible for deciding whether to follow the referrals, honour an
    allow-list, or fall back to the previous behaviour.
    """
    logger.debug("Filter: %s, attr list: %s", ldap_filter, attributes)
    attr_list = list(attributes) if attributes else None
    try:
        # Referrals are never followed by the native client; they come back
        # in the search response for the caller to decide.
        response = con.search(
            base,
            ldap_filter,
            scope=scope,
            attributes=attr_list,
            size_limit=limit,
        )
    except NativeLdapError as e:
        logger.exception("Exception in search:")
        raise LDAPError(str(e)) from e

    entries: list[LDAPResultType] = response["entries"]
    if raise_on_referrals and response["referrals"]:
        raise LDAPReferralError(
            typing.cast(list[str], response["referrals"]),
            partial_results=[dict(e) for e in entries],
        )

    for entry in entries:
        dct = utils.CaseInsensitiveDict[list[str]]()
        if attr_list is not None:
            # Parity: requested-but-missing attributes come back as [""].
            for attr in attr_list:
                dct[attr] = typing.cast(list[str], entry.get(attr, [""]))
        else:
            for attr, values in entry.items():
                if attr != "dn":
                    dct[attr] = typing.cast(list[str], values)
        dct["dn"] = entry["dn"]
        yield dct


def first(
    con: LDAPConnection,
    base: str,
    object_class: str,
    field: str,
    value: str,
    *,
    attributes: collections.abc.Iterable[str] | None = None,
    max_entries: int = 50,
    raise_on_referrals: bool = False,
) -> "LDAPResultType | None":
    """
    Searches for the username and returns its LDAP entry.

    If ``raise_on_referrals`` is True, propagates :class:`LDAPReferralError`
    when the server returned referrals instead of returning ``None``.
    """
    value = escape(value)
    attr_list = [field] + list(attributes) if attributes else [field]
    ldap_filter = f"(&(objectClass={object_class})({field}={value}))"
    try:
        gen = as_dict(
            con,
            base,
            ldap_filter,
            attributes=attr_list,
            limit=max_entries,
            raise_on_referrals=raise_on_referrals,
        )
        obj = next(gen)
    except LDAPReferralError:
        raise
    except StopIteration:
        return None
    obj["_id"] = value
    return obj


def add(
    con: LDAPConnection,
    dn: str,
    *,
    attributes: dict[str, list[bytes | str]],
) -> bool:
    """
    Adds a new LDAP entry.
    Args:
        con: LDAP connection
        dn: Distinguished Name of the entry to add
        attributes: Dictionary of attributes, e.g. { 'objectClass': ['user'], ... }
    Returns:
        True if the operation was successful, raises LDAPError otherwise
    """
    try:
        con.add(dn, attributes=attributes)
        return True
    except NativeAlreadyExistsError as e:
        raise AlreadyExistsError(str(e)) from e
    except NativeLdapError as e:
        logger.exception("Exception in add:")
        raise LDAPError(str(e)) from e


def delete(con: LDAPConnection, dn: str, *, depth: int = 1) -> None:
    """
    Deletes an LDAP entry and its children up to a certain depth.
    Args:
        con: LDAP connection
        dn: Distinguished Name of the entry to delete
        depth: How many levels to delete (1=only direct children, 2=children and grandchildren, <1=all levels)
    Returns:
        None. Raises LDAPError on failure.
    """
    try:
        response = con.search(dn, "(objectClass=*)", scope=SCOPE_ONELEVEL)
        for entry in response["entries"]:
            delete(con, typing.cast(str, entry["dn"]), depth=depth - 1)
        con.delete(dn)
    except NativeLdapError as e:
        logger.exception("Exception in delete:")
        raise LDAPError(str(e)) from e


def recursive_delete(con: LDAPConnection, base_dn: str) -> None:
    """
    Deletes all direct children and the entry itself (one level deep, for compatibility).
    """
    delete(con, base_dn, depth=1)


def modify(
    con: LDAPConnection,
    dn: str,
    changes: dict[str, list[tuple[str, list[bytes | str]]]],
    *,
    controls: typing.Any = None,
) -> bool:
    """
    Performs a modify operation on the LDAP entry.
    Args:
        con: LDAP connection
        dn: Distinguished Name of the entry to modify
        changes: Dictionary of changes, e.g. { 'member': [(MODIFY_ADD, [b'userdn'])] }
        controls: Optional controls. Not supported by the native backend;
            passing non-None raises LDAPError.
    Returns:
        True if the operation was successful, raises LDAPError otherwise
    """
    if controls is not None:
        raise LDAPError("Modify controls are not supported by the native LDAP client")
    try:
        con.modify(dn, changes=changes)
        return True
    except NativeAlreadyExistsError as e:
        raise AlreadyExistsError(str(e)) from e
    except NativeLdapError as e:
        logger.exception("Exception in modify:")
        raise LDAPError(str(e)) from e


def get_root_dse(con: LDAPConnection) -> "LDAPResultType | None":
    try:
        response = con.search("", "(objectClass=*)", scope=SCOPE_BASE, attributes=["*", "+"])
    except NativeLdapError as e:
        raise LDAPError(str(e)) from e
    entries = response["entries"]
    if not entries:
        return None
    return dict(entries[0])


def dn_from_domain(domain: str) -> str:
    """
    `'a.b.c'` -> `'dc=a,dc=b,dc=c'`. Empty / whitespace input -> empty string.
    """
    parts = [p.strip() for p in domain.split(".") if p.strip()]
    if not parts:
        return ""
    return ",".join(f"dc={p}" for p in parts)


BAD_COOLDOWN_DEFAULT: typing.Final[int] = 30  # 30s seed (transient glitches heal fast)
BAD_COOLDOWN_MAX: typing.Final[int] = 28800  # 8h cap (matches daily DC cycle)
BAD_COOLDOWN_OWNER: typing.Final[str] = "ldap"  # namespace inside the global backoff cache


def preferred_cache_key(hosts: collections.abc.Sequence[tuple[str, int]]) -> str:
    """Cache key holding the preferred DC for *this* pool of servers.

    Keyed by the pool itself: a DC learned while serving one authenticator
    must never be tried on behalf of another, whose configured servers and
    bind credentials are unrelated to it.
    """
    pool: str = "|".join(sorted(f"{h.lower().rstrip('.')}:{p}" for h, p in hosts))
    return f"ldap.preferred.{hashlib.sha256(pool.encode()).hexdigest()[:16]}"


def connect_with_pool(
    user: str,
    password: str,
    hosts: collections.abc.Iterable[tuple[str, int]],
    *,
    use_ssl: bool = False,
    verify_ssl: bool = True,
    certificate_data: str | None = None,
    timeout: int = 8,
    cache: CacheLike | None = None,
    ignore_referrals: bool = True,
    allowed_referral_hosts: tuple[str, ...] = (),
    bad_cooldown: int = BAD_COOLDOWN_DEFAULT,
    probe: bool = True,
    probe_timeout: float = 1.5,
) -> LDAPConnection:
    """
    Try, in order, to bind against each `(host, port)`. Skips any host that
    is currently in backoff (per-key exponential cooldown). A previously
    successful ``(host, port)`` is tried first ("preferred").

    Returns the first successful connection. Raises ``LDAPError`` if every
    host fails.

    ``cache`` defaults to a process-wide ``Cache('ldap')``; callers can pass
    their own (typically only tests do this). ``Backoff`` shares that same
    cache under the ``ldap`` namespace, so a host that fails for one AD
    authenticator is also skipped for every other one — a broken DC is
    broken for everyone.
    """
    host_list: list[tuple[str, int]] = [(h, p) for h, p in hosts if h and h.strip()]
    if not host_list:
        raise LDAPError(_("No LDAP servers configured"))

    def _host_key(host: str, port: int) -> str:
        return f"{host.lower().rstrip('.')}:{port}"

    # Our own cache. ``Backoff`` receives the same instance for the badness
    # state; both code paths use the ``ldap`` namespace.
    ldap_cache: CacheLike = cache if cache is not None else Cache(BAD_COOLDOWN_OWNER)
    bo = Backoff(
        ldap_cache,
        owner=BAD_COOLDOWN_OWNER,
        fail_time=bad_cooldown,
        max_time=BAD_COOLDOWN_MAX,
    )

    preferred_key: str = preferred_cache_key(host_list)

    def _preferred() -> list[tuple[str, int]]:
        """Returns the cached preferred host(s), in priority order.

        Storage format is just the list — the cache serialises it.
        """
        return ldap_cache.get(preferred_key, default=[])

    def _set_preferred(hosts: list[tuple[str, int]]) -> None:
        """Store the hosts as the new preferred list (priority order)."""
        ldap_cache.put(preferred_key, hosts or [], 3600)

    # Order: preferred first (tried in priority order), then the rest in
    # the order the caller supplied. Duplicates collapse. The preferred
    # entry always belongs to this same pool, since the key is derived
    # from ``host_list``.
    ordered: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for h, p in (*_preferred(), *host_list):
        if (h, p) not in seen:
            seen.add((h, p))
            ordered.append((h, p))

    last_error: str = ""
    for h, p in ordered:
        key = _host_key(h, p)
        if bo.is_bad(key):
            logger.debug("Skipping bad host %s:%s (in cooldown)", h, p)
            continue
        if probe and not util_net.test_connectivity(h, p, timeout=probe_timeout):
            logger.debug("Probe TCP failed for %s:%s, marking as bad", h, p)
            bo.mark_bad(key)
            continue
        try:
            con = connection(
                user,
                password,
                h,
                port=p,
                use_ssl=use_ssl,
                timeout=timeout,
                debug=False,
                verify_ssl=verify_ssl,
                certificate_data=certificate_data,
            )
        except BindError as e:
            # The DC answered, it just refused these credentials. Marking it
            # bad would drop a healthy DC into a cooldown that grows to 8h
            # and that every other LDAP/AD authenticator honours.
            last_error = str(e)
            logger.debug("Bind rejected by %s:%s: %s", h, p, e)
            continue
        except LDAPError as e:
            last_error = str(e)
            logger.debug("LDAPError connecting to %s:%s: %s", h, p, e)
            bo.mark_bad(key)
            continue
        except Exception as e:  # pragma: no cover - safety net
            last_error = str(e)
            logger.debug("Exception connecting to %s:%s: %s", h, p, e)
            bo.mark_bad(key)
            continue

        # Success
        _set_preferred([(h, p)])
        bo.clear_bad(key)
        # ``ignore_referrals`` / ``allowed_referral_hosts`` are accepted for
        # API symmetry: the caller decides the referral policy and is
        # responsible for passing ``raise_on_referrals=True`` to ``as_dict``
        # / ``first`` so that any ``LDAPReferralError`` is surfaced back.
        # We don't plumb them through here because this layer only
        # establishes a connection; the actual follow (or drop) happens
        # at the search layer.
        del ignore_referrals, allowed_referral_hosts
        return con

    raise LDAPError(
        _("Could not connect to any LDAP server ({}). Last error: {}").format(
            ", ".join(f"{h}:{p}" for h, p in ordered), last_error
        )
    )

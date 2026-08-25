#
# Copyright (c) 2014-2023 Virtual Cable S.L.
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
"""
# pyright: reportUnknownMemberType=false

import collections.abc
import logging
import ssl
import typing

# For pyasn1 compatibility of ldap3
# This is a workaround for the deprecation warning of pyasn1 when used by ldap3
# It is not recommended to ignore warnings :)
import warnings

from django.conf import settings
from django.utils.translation import gettext as _

from ldap3 import ALL
from ldap3 import ALL_ATTRIBUTES
from ldap3 import BASE
from ldap3 import LEVEL
from ldap3 import MODIFY_ADD as LDAP_MODIFY_ADD
from ldap3 import MODIFY_DELETE as LDAP_MODIFY_DELETE
from ldap3 import MODIFY_INCREMENT as LDAP_MODIFY_INCREMENT
from ldap3 import MODIFY_REPLACE as LDAP_MODIFY_REPLACE
from ldap3 import SIMPLE
from ldap3 import SUBTREE
from ldap3 import Connection
from ldap3 import Server
from ldap3 import Tls

from uds.core.util import net as util_net
from uds.core.util import utils
from uds.core.util.backoff import Backoff
from uds.core.util.cache import Cache
from uds.core.util.cache import CacheLike

warnings.filterwarnings("ignore", module="pyasn1", category=DeprecationWarning)


logger = logging.getLogger(__name__)

# Re-export with our nomenclature
SCOPE_BASE = BASE
SCOPE_SUBTREE = SUBTREE
SCOPE_ONELEVEL = LEVEL

# Also for modify operations
MODIFY_ADD = LDAP_MODIFY_ADD
MODIFY_DELETE = LDAP_MODIFY_DELETE
MODIFY_REPLACE = LDAP_MODIFY_REPLACE
MODIFY_INCREMENT = LDAP_MODIFY_INCREMENT

LDAP_ALREADY_EXISTS_RESULT_CODES = frozenset({20, 68})
LDAP_ALREADY_EXISTS_DESCRIPTIONS = frozenset({"attributeOrValueExists", "entryAlreadyExists"})

LDAPResultType = dict[str, typing.Any]
LDAPSearchResultType = list[dict[str, typing.Any]] | None

LDAPConnection: typing.TypeAlias = Connection


class LDAPError(Exception):
    @staticmethod
    def reraise(e: typing.Any) -> typing.NoReturn:
        _str = _("Connection error: ")
        _str += str(e)
        raise LDAPError(_str) from e


class AlreadyExistsError(LDAPError):
    pass


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


def _raise_for_result(operation: str, result: collections.abc.Mapping[str, typing.Any]) -> typing.NoReturn:
    result_code = result.get("result")
    description = str(result.get("description", ""))
    message = f"{operation} operation failed: {result}"

    try:
        numeric_result = int(typing.cast(str | int, result_code))
    except (TypeError, ValueError):
        numeric_result = None

    if numeric_result in LDAP_ALREADY_EXISTS_RESULT_CODES or description in LDAP_ALREADY_EXISTS_DESCRIPTIONS:
        raise ALREADY_EXISTS(message)

    raise LDAPError(message)


def _extract_referrals(con: Connection) -> list[str]:
    """
    Returns the list of referral URIs from the last LDAP operation on ``con``.

    ldap3 normally surfaces referrals as ``con.result['referrals']``. AD can
    also return ``searchResRef`` response items alongside a successful search,
    so those URIs are collected from ``con.response`` as well. Returns ``[]``
    if no referrals were returned or if the connection result doesn't expose
    them. Defensive: never raises.
    """
    try:
        result: dict[str, typing.Any] | None = con.result
    except Exception:
        return []
    if not isinstance(result, dict):
        return []
    refs: list[str] = []
    try:
        refs.extend(str(r) for r in result.get("referrals", ()) if r)
    except Exception:
        pass

    try:
        response = typing.cast(typing.Any, con.response)
        for item in typing.cast(
            collections.abc.Iterable[typing.Any],
            response if isinstance(response, collections.abc.Iterable) else (),
        ):
            if not isinstance(item, collections.abc.Mapping) or item.get("type") != "searchResRef":
                continue
            uris = typing.cast(typing.Any, item.get("uri", ()))
            if isinstance(uris, str):
                refs.append(uris)
            elif isinstance(uris, collections.abc.Iterable):
                refs.extend(str(uri) for uri in typing.cast(list[typing.Any], uris) if uri)
    except Exception:
        pass

    return list(dict.fromkeys(refs))


def escape(value: str) -> str:
    """
    Escape filter chars for ldap search filter
    """
    # ldap3 does not provide a direct escape, but this is a safe replacement
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
    verify_ssl: bool = False,
    certificate_data: str | None = None,  # Content of the certificate, not the file itself
) -> "LDAPConnection":
    """
    Tries to connect to ldap using ldap3. If username is None, it tries to connect using user provided credentials.
    """
    logger.debug("Login in to %s as user %s", host, username)

    if port == -1:
        port = 636 if use_ssl else 389
    tls = None

    if use_ssl:
        # Use ldap3's own constants for validate and version, not ssl module
        tls_validate = ssl.CERT_REQUIRED if verify_ssl else ssl.CERT_NONE

        if hasattr(settings, "SECURE_MIN_TLS_VERSION") and settings.SECURE_MIN_TLS_VERSION:
            # format is "1.0, 1.1, 1.2 or 1.3", convert to ssl.TLSVersion.TLSv1_0, ssl.TLSVersion.TLSv1_1, ssl.TLSVersion.TLSv1_2 or ssl.TLSVersion.TLSv1_3
            tls_version = getattr(ssl.TLSVersion, "TLSv" + settings.SECURE_MIN_TLS_VERSION.replace(".", "_"))
        else:
            tls_version = ssl.TLSVersion.TLSv1_2

        if hasattr(settings, "SECURE_CIPHERS") and settings.SECURE_CIPHERS:
            cipher = settings.SECURE_CIPHERS
        else:
            cipher = None

        tls = Tls(
            ca_certs_data=certificate_data,
            validate=tls_validate,
            version=tls_version,
            ciphers=cipher,
        )
    server = Server(
        host,
        port=port,
        use_ssl=use_ssl,
        get_info=ALL,
        tls=tls,
    )
    try:
        conn = Connection(
            server,
            user=username,
            password=passwd,
            read_only=read_only,
            authentication=SIMPLE,
            receive_timeout=timeout,
        )
        conn.open()
        if not conn.bind():
            logger.error("Could not bind to LDAP server %s as user %s", host, username)
            # Surface the LDAP result message (e.g. "AcceptSecurityContext
            # error, data 773, ...") so callers that key off specific sub-status
            # codes (AD password expired / must reset) can react. Without this,
            # the bind error text is lost and those callers cannot distinguish
            # an expired password from a generic auth failure.
            inner_msg: str = ""
            try:
                inner_msg = (
                    str(typing.cast(dict[str, typing.Any], conn.result).get("message") or "")
                    if isinstance(conn.result, dict)
                    else ""
                )
            except Exception:
                inner_msg = ""
            prefix = _("Could not bind to LDAP server: {host}").format(host=host)
            raise LDAPError(f"{prefix}: {inner_msg}" if inner_msg else prefix)

        logger.debug("Connection was successful")
        return conn
    except LDAPError:
        raise
    except Exception as e:
        logger.exception("Exception connection:")
        raise LDAPError(str(e)) from e


def as_dict(
    con: Connection,
    base: str,
    ldap_filter: str,
    *,
    attributes: collections.abc.Iterable[str] | None = None,
    limit: int = 100,
    scope: typing.Any = SCOPE_SUBTREE,
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
    attr_list = list(attributes) if attributes else ALL_ATTRIBUTES
    try:
        # ldap3 follows referrals on its own, consuming them before we get to look
        # at them. Hold that off while the search runs, so they reach the caller.
        previous_auto_referrals = con.auto_referrals
        if raise_on_referrals:
            con.auto_referrals = False
        try:
            con.search(
                search_base=base,
                search_filter=ldap_filter,
                search_scope=scope,
                attributes=attr_list,
                size_limit=limit,
            )
        finally:
            con.auto_referrals = previous_auto_referrals
        if raise_on_referrals:
            referrals: list[str] = _extract_referrals(con)
            if referrals:
                entries: list[LDAPResultType] = []
                for entry in typing.cast(typing.Any, con.entries):
                    dct = utils.CaseInsensitiveDict[list[str]]()
                    for attr in attr_list:
                        dct[attr] = entry[attr].values if attr in entry else [""]
                    dct["dn"] = entry.entry_dn
                    entries.append(dct)
                raise LDAPReferralError(referrals, partial_results=entries)
        for entry in typing.cast(typing.Any, con.entries):
            dct = utils.CaseInsensitiveDict[list[str]]()
            for attr in attr_list:
                dct[attr] = entry[attr].values if attr in entry else [""]
            dct["dn"] = entry.entry_dn
            yield dct
    except (LDAPError, LDAPReferralError):
        raise
    except Exception as e:
        logger.exception("Exception in search:")
        raise LDAPError(str(e)) from e


def first(
    con: Connection,
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
    con: Connection,
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
        result = typing.cast(typing.Any, con.add(dn, attributes=attributes))
        if not result:
            _raise_for_result("Add", typing.cast(collections.abc.Mapping[str, typing.Any], con.result))
        return True
    except LDAPError:
        raise
    except Exception as e:
        logger.exception("Exception in add:")
        raise LDAPError(str(e)) from e


def delete(con: Connection, dn: str, *, depth: int = 1) -> None:
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
        con.search(dn, "(objectClass=*)", search_scope=SCOPE_ONELEVEL, attributes=["dn"])
        for entry in typing.cast(list[typing.Any], con.entries):
            child_dn: str = entry.entry_dn
            delete(con, child_dn, depth=depth - 1)
            result = typing.cast(typing.Any, con.delete(child_dn))
            if not result:
                raise LDAPError(f"Delete operation failed: {con.result}")
        result = typing.cast(typing.Any, con.delete(dn))
        if not result:
            raise LDAPError(f"Delete operation failed: {con.result}")
    except Exception as e:
        logger.exception("Exception in delete:")
        raise LDAPError(str(e)) from e


def recursive_delete(con: Connection, base_dn: str) -> None:
    """
    Deletes all direct children and the entry itself (one level deep, for compatibility).
    """
    delete(con, base_dn, depth=1)


def modify(
    con: Connection,
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
        controls: Optional controls
    Returns:
        True if the operation was successful, raises LDAPError otherwise
    """
    try:
        result = typing.cast(typing.Any, con.modify(dn, changes, controls=controls))
        if not result:
            _raise_for_result("Modify", typing.cast(collections.abc.Mapping[str, typing.Any], con.result))
        return True
    except LDAPError:
        raise
    except Exception as e:
        logger.exception("Exception in modify:")
        raise LDAPError(str(e)) from e


def get_root_dse(con: Connection) -> "LDAPResultType | None":
    con.search(
        "",
        "(objectClass=*)",
        search_scope=SCOPE_BASE,
        attributes=ALL_ATTRIBUTES,
        get_operational_attributes=True,
    )
    if con.entries:
        entry = typing.cast(typing.Any, con.entries[0])
        dct: dict[str, typing.Any] = {attr: entry[attr].values for attr in entry.entry_attributes}
        dct["dn"] = entry.entry_dn
        return dct
    return None


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
) -> "LDAPConnection":
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

    def _preferred() -> list[tuple[str, int]]:
        """Returns the cached preferred host(s), in priority order.

        Storage format is just the list — the cache serialises it.
        """
        return ldap_cache.get("ldap.preferred", default=[])

    def _set_preferred(hosts: list[tuple[str, int]]) -> None:
        """Store the hosts as the new preferred list (priority order)."""
        ldap_cache.put("ldap.preferred", hosts or [], 3600)

    # Order: preferred first (tried in priority order), then the rest in
    # the order the caller supplied. Duplicates collapse, but we never drop
    # a preferred entry even if it isn't in ``host_list`` — better to probe
    # it (and let ``is_bad`` skip it if it's down) than to ignore it.
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

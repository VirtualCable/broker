# pyright: reportUnknownMemberType=false
"""
Author: Adolfo Gómez, dkmaster at dkmon dot com
Converted to ldap3 by GitHub Copilot
"""

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
            raise LDAPError(_("Could not bind to LDAP server: {host}").format(host=host))

        logger.debug("Connection was successful")
        return conn
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
) -> collections.abc.Generator[LDAPResultType, None, None]:
    """
    Makes a search on LDAP, returns a generator with the results, where each result is a dictionary where values are always a list of strings
    """
    logger.debug("Filter: %s, attr list: %s", ldap_filter, attributes)
    attr_list = list(attributes) if attributes else ALL_ATTRIBUTES
    try:
        con.search(
            search_base=base,
            search_filter=ldap_filter,
            search_scope=scope,
            attributes=attr_list,
            size_limit=limit,
        )
        for entry in typing.cast(typing.Any, con.entries):
            dct = utils.CaseInsensitiveDict[list[str]]()
            for attr in attr_list:
                dct[attr] = entry[attr].values if attr in entry else [""]
            dct["dn"] = entry.entry_dn
            yield dct
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
) -> "LDAPResultType | None":
    """
    Searchs for the username and returns its LDAP entry
    """
    value = escape(value)
    attr_list = [field] + list(attributes) if attributes else [field]
    ldap_filter = f"(&(objectClass={object_class})({field}={value}))"
    try:
        gen = as_dict(con, base, ldap_filter, attributes=attr_list, limit=max_entries)
        obj = next(gen)
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
    con.search("", "(objectClass=*)", search_scope=SCOPE_BASE)
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
        # ``ignore_referrals`` / ``allowed_referral_hosts`` are part of the
        # API for symmetry with future ldap3 features that need them
        # (``Connection`` constructor flags). For now ldap3 builds the
        # ``Server`` with ``get_info=ALL`` which is enough; the flags are
        # accepted but unused here. Explicit ``del`` keeps pyrefly happy.
        del ignore_referrals, allowed_referral_hosts
        return con

    raise LDAPError(
        _("Could not connect to any LDAP server ({}). Last error: {}").format(
            ", ".join(f"{h}:{p}" for h, p in ordered), last_error
        )
    )

"""Global (system) log endpoint.

Exposes the syslog-destined UDS log lines (the same entries that go to the
journal and the rotating file through ``UDSLogHandler``) that ``LogManager``
stores under the ``SYSLOG`` owner. These lines are the platform's operational
narration: service publications, actor registrations, REST/MCP operations and
internal manager decisions.

The endpoint is **admin only** by design: global lines mix events from every
tenant and, although the platform is expected never to log sensitive data,
restricting the surface to administrators keeps the blast radius of any
future logging mistake as small as possible.

All filters apply at the query level (never after the limit), so asking for
``since`` plus a small ``limit`` really returns the newest entries of that
window instead of a slice of the unfiltered log.
"""

import datetime
import re
import typing

from django.db.models import QuerySet
from django.utils import dateparse, timezone

from uds.core import consts
from uds.core.types.log import LogObjectType
from uds.core.util.log import LogLevel
from uds.models.log import Log

from uds.REST import Handler
from uds.core.exceptions import rest as rest_exceptions

# Bounds for the ``limit`` parameter. The default keeps a single answer
# LLM-friendly while the ceiling protects the database from absurd requests;
# it also caps every date-range query as extra protection.
_DEFAULT_LIMIT: typing.Final[int] = 100
_MAX_LIMIT: typing.Final[int] = 1000

# Owner identity of the global log, mirroring ``LogManager`` (no db object
# means SYSLOG type with the ``-1`` id).
_SYSLOG_OWNER_ID: typing.Final[int] = -1

_DATE_ONLY: typing.Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Logs(Handler):
    """Read the global (system) UDS log, newest entries first.

    Query parameters (all optional):

    * ``since``: only entries at or after this instant. ISO 8601 datetime
      (``2026-09-04T10:00:00``) or plain date (``2026-09-04``, taken as the
      start of that day).
    * ``until``: only entries at or before this instant. Same formats; a
      plain date is taken as the **end** of that day (``23:59:59``).
    * ``limit``: page size (default 100, capped at 1000). Ignored when
      ``$top`` is provided. The newest entries of the requested window are
      returned.
    * ``level``: minimum severity as a ``LogLevel`` name (``INFO``,
      ``ERROR``...) — entries below it are filtered out.
    * ``source``: exact, case-insensitive source match (``REST``,
      ``SERVICE``, ``INTERNAL``...).

    Standard **OData** query parameters are honoured through the same
    machinery every collection uses (and the RFC 10008 ``QUERY`` verb with
    the parameters in the request body works as well): ``$filter``
    (queryset level, so filtering happens before paging), ``$orderby``,
    ``$skip`` and ``$top``. The response carries the ``X-Total-Count``
    header with the number of entries matching the filters, as the rest
    of the REST API does.

    The answer is an object (not a bare list) so consumers can tell a
    complete window from a truncated one:

    * ``entries``: the log entries (newest first by default). Each one
      carries ``date`` (timestamp), ``level`` (numeric), ``level_name``
      (human readable), ``source`` and ``message``.
    * ``truncated``: ``true`` when more entries matching the same filters
      exist beyond the returned page (``$skip + page < X-Total-Count``).
    * ``limit``: the effective page size used.
    * ``hint``: present only when ``truncated`` is ``true``; a human (and
      LLM) readable instruction on how to get the rest.
    """

    ROLE: typing.ClassVar[consts.Role] = consts.Role.ADMIN

    def get(self) -> dict[str, typing.Any]:
        page_size = self._page_size()
        rows = self.filter_odata_queryset(self._filtered_queryset())

        total = int(self.headers().get("X-Total-Count", str(len(rows))))
        skip = self.odata.start or 0
        truncated = skip + len(rows) < total

        return {
            "entries": [
                {
                    "date": entry.created,
                    "level": entry.level,
                    "level_name": _level_name(entry.level),
                    "source": entry.source,
                    "message": entry.data,
                }
                for entry in rows
            ],
            "truncated": truncated,
            "limit": page_size,
            **(
                {
                    "hint": (
                        f"More entries exist for this window; only {len(rows)} of {total} were returned. "
                        "Page with $skip/$top, narrow the window (since/until), "
                        "filter (level/source or $filter) or raise the page size (max 1000)."
                    )
                }
                if truncated
                else {}
            ),
        }

    @typing.override
    def apply_sort(self, qs: "QuerySet[typing.Any]") -> "list[typing.Any] | QuerySet[typing.Any]":
        """Order by the requested ``$orderby`` fields with a stable tiebreaker.

        Without ``$orderby`` the newest entries come first, which is the
        natural browsing order for a log.
        """
        if self.odata.orderby:
            return qs.order_by(*self.odata.orderby, "-id")
        return qs.order_by("-created", "-id")

    def _filtered_queryset(self) -> "QuerySet[Log]":
        """Base queryset: the global slice narrowed by the sugar filters."""
        queryset = Log.objects.filter(owner_id=_SYSLOG_OWNER_ID, owner_type=LogObjectType.SYSLOG.value)

        min_level = self._min_level()
        if min_level is not None:
            queryset = queryset.filter(level__gte=min_level)

        source = self._source()
        if source is not None:
            queryset = queryset.filter(source__iexact=source)

        since = self._boundary("since")
        if since is not None:
            queryset = queryset.filter(created__gte=since)

        until = self._boundary("until")
        if until is not None:
            queryset = queryset.filter(created__lte=until)

        return queryset.order_by("-created", "-id")

    def _page_size(self) -> int:
        """Resolve the effective page size into ``$top``, clamped to bounds.

        ``$top`` (OData) wins over the plain ``limit`` parameter so the
        standard paging contract of the REST API is preserved; both are
        clamped to the 1..1000 band as extra protection.
        """
        requested = self.odata.limit if self.odata.limit is not None else self._limit()
        try:
            requested = int(requested)
        except (TypeError, ValueError):
            requested = _DEFAULT_LIMIT
        self._odata.limit = max(1, min(requested, _MAX_LIMIT))
        return self._odata.limit

    def _limit(self) -> int:
        """Return the requested entry limit clamped to the configured bounds."""
        raw = self._params.get("limit", _DEFAULT_LIMIT)
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        return max(1, min(limit, _MAX_LIMIT))

    def _min_level(self) -> int | None:
        """Return the minimum severity requested, or ``None`` for no filter."""
        raw = self._params.get("level")
        if raw is None or str(raw).strip() == "":
            return None
        name = str(raw).strip().upper()
        try:
            return int(LogLevel[name])
        except KeyError:
            raise rest_exceptions.RequestError(f"Invalid level: {raw}") from None

    def _source(self) -> str | None:
        """Return the normalized source filter, or ``None`` for no filter."""
        raw = self._params.get("source")
        if raw is None or str(raw).strip() == "":
            return None
        return str(raw).strip().lower()

    def _boundary(self, name: typing.Literal["since", "until"]) -> datetime.datetime | None:
        """Parse the ``since``/``until`` boundary into an aware datetime.

        A plain date means start-of-day for ``since`` and end-of-day for
        ``until``, so a whole day can be requested with a single value.
        """
        raw = self._params.get(name)
        if raw is None or str(raw).strip() == "":
            return None
        text = str(raw).strip()
        if _DATE_ONLY.match(text):
            text += " 23:59:59" if name == "until" else " 00:00:00"
        parsed = dateparse.parse_datetime(text)
        if parsed is None:
            raise rest_exceptions.RequestError(f"Invalid {name}: {raw}")
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed


def _level_name(level: int) -> str:
    """Return the human-readable name of a numeric log level."""
    try:
        return LogLevel(level).name
    except ValueError:
        return str(level)

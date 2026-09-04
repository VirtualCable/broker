"""CSV analytical reports, generated through the canonical POST verb.

Only the reports that aggregate data an agent cannot efficiently compute
from the generic listings are curated here (failed logins, admin
activity). Pure listings are redundant with the ``list_*`` tools and the
PDF documents are useless for an LLM.
"""

import typing

from uds.REST.methods.reports import Reports
from uds.reports.lists.admin_activity import AdminActivityReportCSV
from uds.reports.lists.failed_logins import FailedLoginsReportCSV

from ..catalog import ToolDefinition
from ..rest_proxy import RestProxy, RestTarget
from .helpers import POST, MAX_REPORT_CHARS, JsonObject, check_required, schema, string_property, uuid_property

if typing.TYPE_CHECKING:
    from uds.core.reports.report import Report

__all__ = ["curated_tools"]


def _report_tool(
    *,
    name: str,
    title: str,
    description: str,
    report_cls: "type[Report]",
    params_schema: JsonObject,
    required: tuple[str, ...],
    argument_to_param: dict[str, str],
    defaults: dict[str, typing.Any],
    access: str,
) -> ToolDefinition:
    """Build a tool that generates one specific CSV report.

    The report uuid is fixed by the tool (the class is the single source of
    truth through ``get_uuid``), so the agent never has to discover ids; the
    arguments are exactly the report's own parameters. The generated CSV
    text is clamped to ``MAX_REPORT_CHARS`` with an explicit truncation
    marker, so answers stay proportionate for an LLM context.
    """
    report_uuid = report_cls.get_uuid()

    async def executor(arguments: JsonObject, request: typing.Any = None) -> typing.Any:
        check_required(arguments, required)
        params: dict[str, typing.Any] = dict(defaults)
        for argument, param in argument_to_param.items():
            if arguments.get(argument) is not None:
                params[param] = arguments[argument]
        result = await RestProxy().execute(
            RestTarget(Reports, "reports", POST, args=(report_uuid,)),
            request,
            params,
        )
        data = str(typing.cast(JsonObject, result).get("data", ""))
        truncated = len(data) > MAX_REPORT_CHARS
        return {
            "mime_type": result.get("mime_type"),
            "filename": result.get("filename"),
            "data": data[:MAX_REPORT_CHARS],
            "truncated": truncated,
            **(
                {
                    "hint": (
                        f"The report is bigger than {MAX_REPORT_CHARS} characters and was cut. "
                        "Narrow the date range or the scope to get the rest."
                    )
                }
                if truncated
                else {}
            ),
        }

    return ToolDefinition(
        name=name,
        title=title,
        description=description,
        input_schema=schema(params_schema, required),
        access=access,
        returns="An object with mime_type, filename, the CSV text in data, and truncated when the text was cut.",
        required_permission="ALL",
        executor=executor,
    )


def _failed_logins_tool() -> ToolDefinition:
    """Build the failed logins CSV report tool."""

    return _report_tool(
        name="report_failed_logins",
        title="Report: failed logins",
        description=(
            "CSV aggregation of failed login attempts over a date range, per user and "
            "authenticator. Use it to assess brute-force or account-lockout situations; "
            "raw lines live in ``get_system_logs``, this answer is the counted summary."
        ),
        report_cls=FailedLoginsReportCSV,
        params_schema={
            "start_date": string_property("First day of the range, as YYYY-MM-DD."),
            "end_date": string_property("Last day of the range, as YYYY-MM-DD (inclusive)."),
            "authenticator_uuid": uuid_property(
                "Optional authenticator to scope the report to. Omit (or pass the special "
                "value 0-0-0-0) for all authenticators."
            ),
        },
        required=("start_date", "end_date"),
        argument_to_param={"start_date": "start_date", "end_date": "end_date", "authenticator_uuid": "authenticator"},
        defaults={"authenticator": "0-0-0-0"},
        access="Administrators only (the backing reports endpoint requires the admin role).",
    )


def _admin_activity_tool() -> ToolDefinition:
    """Build the admin activity CSV report tool."""

    return _report_tool(
        name="report_admin_activity",
        title="Report: admin activity",
        description=(
            "CSV summary of administrator activity over a date range: requests, errors, "
            "last seen and most used endpoints per admin. Use it to review what "
            "administrators (and automated agents) actually did."
        ),
        report_cls=AdminActivityReportCSV,
        params_schema={
            "start_date": string_property("First day of the range, as YYYY-MM-DD."),
            "end_date": string_property("Last day of the range, as YYYY-MM-DD (inclusive)."),
            "top_paths": {
                "type": "integer",
                "description": "Most-used endpoints to list per admin (1-50). Default: 5.",
                "minimum": 1,
                "maximum": 50,
            },
        },
        required=("start_date", "end_date", "top_paths"),
        argument_to_param={"start_date": "start_date", "end_date": "end_date", "top_paths": "top_paths"},
        defaults={},
        access="Administrators only (the backing reports endpoint requires the admin role).",
    )


def curated_tools() -> tuple[ToolDefinition, ...]:
    """Return the CSV report tools."""
    return (
        _failed_logins_tool(),
        _admin_activity_tool(),
    )

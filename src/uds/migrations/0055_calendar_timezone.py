import datetime
import typing
import zoneinfo

from django.conf import settings
from django.db import migrations, models

# Calendar rules were written by 4.0 (USE_TZ=False) as naive local times of the
# server. Since 5.0 runs with USE_TZ=True those digits are read back as UTC, so a
# rule entered as 08:00 behaves as 08:00 UTC. All of them, including any created
# on a 5.0 test build, are treated as server-local wall clocks and rewritten as
# real UTC instants. The server zone is also stored on each calendar so clients
# can keep showing and editing the rules in it.


def _server_zone() -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(settings.TIME_ZONE or "UTC")
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return zoneinfo.ZoneInfo("UTC")


def _as_server_local(value: datetime.datetime, zone: zoneinfo.ZoneInfo) -> datetime.datetime:
    # Keep the digits written by 4.0 and give them their real zone.
    return value.replace(tzinfo=None).replace(tzinfo=zone).astimezone(datetime.timezone.utc)


def _to_utc(apps: typing.Any, _schema_editor: typing.Any) -> None:
    Calendar = apps.get_model("uds", "Calendar")
    CalendarRule = apps.get_model("uds", "CalendarRule")
    zone = _server_zone()

    Calendar.objects.update(tz=str(zone))

    for rule in CalendarRule.objects.all().iterator():
        rule.start = _as_server_local(rule.start, zone)
        if rule.end_date is not None:
            # 4.0 kept the last day; the rule may fire until the end of it.
            rule.end = datetime.datetime.combine(rule.end_date, datetime.time.max, zone).astimezone(
                datetime.timezone.utc
            )
        rule.save(update_fields=["start", "end"])


def _to_local(apps: typing.Any, _schema_editor: typing.Any) -> None:
    Calendar = apps.get_model("uds", "Calendar")
    CalendarRule = apps.get_model("uds", "CalendarRule")

    for calendar in Calendar.objects.all().iterator():
        try:
            zone = zoneinfo.ZoneInfo(calendar.tz)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            zone = _server_zone()
        for rule in CalendarRule.objects.filter(calendar=calendar).iterator():
            local_start = rule.start.astimezone(zone).replace(tzinfo=None)
            rule.start = local_start.replace(tzinfo=datetime.timezone.utc)
            rule.end_date = rule.end.astimezone(zone).date() if rule.end is not None else None
            rule.save(update_fields=["start", "end_date"])


class Migration(migrations.Migration):
    dependencies = [  # noqa: RUF012
        ("uds", "0054_actionflow_flowaction"),
    ]

    operations = [  # noqa: RUF012
        migrations.AddField(
            model_name="calendar",
            name="tz",
            field=models.CharField(default="UTC", max_length=64),
        ),
        migrations.RenameField(model_name="calendarrule", old_name="end", new_name="end_date"),
        migrations.AddField(
            model_name="calendarrule",
            name="end",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(_to_utc, reverse_code=_to_local),
        migrations.RemoveField(model_name="calendarrule", name="end_date"),
    ]

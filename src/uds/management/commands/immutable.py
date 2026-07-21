# -*- coding: utf-8 -*-
#
# Copyright (c) 2025 Virtual Cable S.L.U.
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

Management command for the immutable audit log.

Usage::

    python manage.py immutable                         # Show stats
    python manage.py immutable --verify                # Full chain verification
    python manage.py immutable --export json           # Raw hex dump
    python manage.py immutable --export json --filter rest_log  # Readable, filtered
"""

import argparse
import csv
import json
import logging
import sys
import typing

from django.core.management.base import BaseCommand

from uds.core.audit.immutable import ImmutableLogger, content_from_bytes
from uds.core.util import model
from uds.core.util.config import GlobalConfig
from uds.models.immutable_log import ImmutableLog

logger = logging.getLogger(__name__)

# -- filter definitions ---------------------------------------------------
# Maps --filter argument to set of 't' values in the unpickled dict

_FILTERS: dict[str, set[str]] = {
    'rest_log': {'rest'},
    'login_log': {'login', 'logout'},
}

# -- readable formatters --------------------------------------------------
# Each formatter converts an (ImmutableLog, unpickled_dict) into a
# clean dict for CSV/JSON/YAML export.


class _RestLogFormatter:
    """Formats a REST API entry into readable fields."""

    @staticmethod
    def format(entry: ImmutableLog, obj: dict[str, typing.Any]) -> dict[str, typing.Any]:
        return {
            'sequence': entry.sequence,
            'stamp': entry.stamp.isoformat(),
            'method': obj.get('m', ''),
            'path': obj.get('p', ''),
            'response_code': obj.get('c', 0),
            'ip': obj.get('i', ''),
            'username': obj.get('u', ''),
        }

    @staticmethod
    def csv_fields() -> list[str]:
        return ['sequence', 'stamp', 'method', 'path', 'response_code', 'ip', 'username']


class _LoginLogFormatter:
    """Formats a login/logout entry into readable fields."""

    @staticmethod
    def format(entry: ImmutableLog, obj: dict[str, typing.Any]) -> dict[str, typing.Any]:
        return {
            'sequence': entry.sequence,
            'stamp': entry.stamp.isoformat(),
            'type': obj.get('t', ''),
            'authenticator': obj.get('a', ''),
            'username': obj.get('u', ''),
            'ip': obj.get('i', ''),
            'os': obj.get('o', ''),
            'result': obj.get('r', ''),
            'error': obj.get('e', False),
        }

    @staticmethod
    def csv_fields() -> list[str]:
        return ['sequence', 'stamp', 'type', 'authenticator', 'username', 'ip', 'os', 'result', 'error']


# Map filter name → formatter
_FORMATTERS: dict[str, type[_RestLogFormatter | _LoginLogFormatter]] = {
    'rest_log': _RestLogFormatter,
    'login_log': _LoginLogFormatter,
}


class Command(BaseCommand):
    help = 'Manage and verify the immutable audit log chain.'

    @typing.override
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            '--verify',
            action='store_true',
            dest='verify',
            default=False,
            help='Perform full chain integrity verification (hashes, links, stamps).',
        )
        group.add_argument(
            '--export',
            action='store',
            dest='export_format',
            choices=('csv', 'yaml', 'json'),
            default=None,
            help='Export entries in the specified format.',
        )
        parser.add_argument(
            '--filter',
            action='store',
            dest='filter_type',
            choices=tuple(_FILTERS),
            default=None,
            help='Filter exported entries by type (requires --export).',
        )

    @typing.override
    def handle(self, *args: typing.Any, **options: typing.Any) -> None:
        if options['verify']:
            self._handle_verify()
        elif options['export_format']:
            self._handle_export(options['export_format'], options['filter_type'])
        else:
            self._handle_stats()

    # -- stats --------------------------------------------------------------

    def _handle_stats(self) -> None:
        """Default: show chain summary."""
        total = ImmutableLog.objects.count()

        if total == 0:
            self.stdout.write('No entries in the immutable log (chain not initialized).')
            return

        anchor_count = ImmutableLog.objects.filter(anchor=True).count()
        enabled = 'yes' if ImmutableLogger.is_enabled() else 'no'
        interval = GlobalConfig.IMMUTABLE_LOG_REANCHOR.as_int()
        reanchor_cfg = f'{interval}s' if interval > 0 else 'disabled'

        size_bytes = self._estimate_size()

        self.stdout.write('─' * 62)
        self.stdout.write('  Immutable Log Summary')
        self.stdout.write('─' * 62)
        self.stdout.write(f'  Enabled (config):  {enabled}')
        self.stdout.write(f'  Total entries:     {total}')
        self.stdout.write(f'    Normal:          {total - anchor_count}')
        self.stdout.write(f'    Anchors:         {anchor_count}')
        self.stdout.write(f'  Re-anchor config:  {reanchor_cfg}')
        self.stdout.write(f'  Est. total size:   {self._format_size(size_bytes)}')
        self.stdout.write(f'  Avg. entry size:   {self._format_size(size_bytes // total) if total else 0}')
        self.stdout.write('─' * 62)

        if not ImmutableLogger.is_enabled():
            self.stdout.write(
                self.style.WARNING(
                    '\nImmutable logging is disabled (GlobalConfig.IMMUTABLE_LOG_ENABLED).\n'
                    'No new entries will be appended.'
                )
            )

    # -- verify -------------------------------------------------------------

    def _handle_verify(self) -> None:
        """Full chain verification."""
        self.stdout.write('Verifying immutable log chain...')
        self.stdout.flush()

        ok, msg = ImmutableLogger.verify()
        if ok:
            self.stdout.write(self.style.SUCCESS(f'  Hash chain: {msg}'))
        else:
            self.stdout.write(self.style.ERROR(f'  Hash chain: {msg}'))
            return

        db_now = model.sql_now()
        stamp_ok, stamp_msg = self._verify_stamps()
        if stamp_ok:
            self.stdout.write(self.style.SUCCESS(f'  Stamps: {stamp_msg}'))
        else:
            self.stdout.write(self.style.ERROR(f'  Stamps: {stamp_msg}'))
            return

        future_entries = ImmutableLog.objects.filter(stamp__gt=db_now).count()
        if future_entries:
            self.stdout.write(
                self.style.WARNING(
                    f'  Future stamps: {future_entries} entries have stamps after DB time '
                    f'({db_now}). Clock skew detected?'
                )
            )

        self.stdout.write('')
        self._handle_stats()

    def _verify_stamps(self) -> tuple[bool, str]:
        prev_stamp = None
        prev_seq = 0
        checked = 0

        for entry in ImmutableLog.objects.order_by('sequence').only('sequence', 'stamp'):
            checked += 1
            if prev_stamp is not None and entry.stamp < prev_stamp:
                return False, (
                    f'Non-monotonic stamp at sequence #{entry.sequence}: '
                    f'{entry.stamp} < #{prev_seq} ({prev_stamp})'
                )
            prev_stamp = entry.stamp
            prev_seq = entry.sequence

        return True, f'{checked} entries, all stamps strictly increasing.'

    # -- export -------------------------------------------------------------

    def _handle_export(self, fmt: str, filter_type: str | None) -> None:
        """Export entries, optionally filtered and formatted for readability."""
        if filter_type:
            self._export_readable(fmt, filter_type)
        else:
            self._export_raw(fmt)

    # -- raw export (hex dump) ----------------------------------------------

    def _export_raw(self, fmt: str) -> None:
        """Raw hex dump of every entry (for backup/forensics)."""
        entries = ImmutableLog.objects.order_by('sequence').iterator()

        if fmt == 'json':
            data = [self._raw_entry_to_dict(e) for e in entries]
            self.stdout.write(json.dumps(data, indent=2, default=str))
        elif fmt == 'csv':
            writer = csv.DictWriter(
                sys.stdout,
                fieldnames=['sequence', 'stamp', 'anchor', 'previous_hash', 'entry_hash', 'data_hex'],
            )
            writer.writeheader()
            for e in entries:
                writer.writerow({
                    'sequence': e.sequence,
                    'stamp': e.stamp.isoformat(),
                    'anchor': e.anchor,
                    'previous_hash': e.previous_hash.hex(),
                    'entry_hash': e.entry_hash.hex(),
                    'data_hex': e.data.hex(),
                })
        elif fmt == 'yaml':
            import yaml

            data = [self._raw_entry_to_dict(e) for e in entries]
            self.stdout.write(yaml.safe_dump(data, default_flow_style=False))

    # -- readable export (verified + unpickled + formatted) -----------------

    def _export_readable(self, fmt: str, filter_type: str) -> None:
        """Walk the verified chain, unpickle matching entries, export formatted."""
        match_types = _FILTERS[filter_type]
        formatter_cls = _FORMATTERS[filter_type]

        self.stderr.write(f'Walking verified chain for "{filter_type}"...')
        self.stderr.flush()

        records: list[dict[str, typing.Any]] = []
        skipped = 0
        total = 0

        for entry in ImmutableLogger.verified_entries():
            total += 1
            if entry.anchor:
                # Genesis / re-anchor — data is TSA token, not pickle
                skipped += 1
                continue

            try:
                obj = content_from_bytes(entry.data)
            except Exception:
                skipped += 1
                continue

            if not isinstance(obj, dict) or typing.cast(dict[str, typing.Any],obj).get('t', '') not in match_types:
                skipped += 1
                continue

            records.append(formatter_cls.format(entry, typing.cast(dict[str, typing.Any], obj)))

        if fmt == 'json':
            self.stdout.write(json.dumps(records, indent=2, default=str))
        elif fmt == 'csv':
            writer = csv.DictWriter(sys.stdout, fieldnames=formatter_cls.csv_fields())
            writer.writeheader()
            for r in records:
                writer.writerow(r)
        elif fmt == 'yaml':
            import yaml

            self.stdout.write(yaml.safe_dump(records, default_flow_style=False))

        self.stderr.write(
            f'Exported {len(records)} entries ({skipped} skipped, {total} total in verified chain).'
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _raw_entry_to_dict(entry: ImmutableLog) -> dict[str, typing.Any]:
        return {
            'sequence': entry.sequence,
            'stamp': entry.stamp.isoformat(),
            'anchor': entry.anchor,
            'previous_hash': entry.previous_hash.hex(),
            'entry_hash': entry.entry_hash.hex(),
            'data_len': len(entry.data),
            'data_hex': entry.data.hex(),
        }

    @staticmethod
    def _estimate_size() -> int:
        try:
            from django.db import connection

            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT pg_total_relation_size('{ImmutableLog._meta.db_table}')"
                    if connection.vendor == 'postgresql'
                    else "SELECT SUM(LENGTH(previous_hash)+LENGTH(data)+LENGTH(entry_hash)+8+8+4)"
                    f" FROM {ImmutableLog._meta.db_table}"
                )
                row = cursor.fetchone()
                return row[0] if row else 0
        except Exception:
            return 0

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f'{size_bytes} B'
        if size_bytes < 1024 * 1024:
            return f'{size_bytes / 1024:.1f} KB'
        if size_bytes < 1024 * 1024 * 1024:
            return f'{size_bytes / (1024 * 1024):.1f} MB'
        return f'{size_bytes / (1024 * 1024 * 1024):.2f} GB'

# -*- coding: utf-8 -*-
#
# Copyright (c) 2023 Virtual Cable S.L.
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
"""
import collections.abc
import csv
import dataclasses
import datetime
import io
import logging
import typing

from django.db.models import Model
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds import models
from uds.core import consts, exceptions, types
from uds.core.exceptions.rest import NotFound, RequestError
from uds.core.types.rest import TableInfo
from uds.core.util import ensure, net, permissions
from uds.core.util import ui as ui_utils
from uds.core.util.model import process_uuid, sql_now
from uds.REST.model import DetailHandler, ModelHandler

logger = logging.getLogger(__name__)

MB: typing.Final[int] = 1024 * 1024


@dataclasses.dataclass
class TokenItem(types.rest.BaseRestItem):
    id: str
    name: str
    stamp: datetime.datetime
    username: str
    ip: str
    hostname: str
    listen_port: int
    mac: str
    token: str
    type: str
    os: str


# REST API for Server Tokens management (for admin interface)
class ServersTokens(ModelHandler[TokenItem]):

    # servers/groups/[id]/servers
    MODEL = models.Server
    EXCLUDE = {
        'type__in': [
            types.servers.ServerType.ACTOR,
            types.servers.ServerType.UNMANAGED,
        ]
    }
    PATH = 'servers'
    NAME = 'tokens'

    TABLE = (
        ui_utils.TableBuilder(_('Registered Servers'))
        .text_column(name='hostname', title=_('Hostname'), visible=True)
        .text_column(name='ip', title=_('IP'), visible=True)
        .text_column(name='mac', title=_('MAC'), visible=True)
        .text_column(name='type', title=_('Type'), visible=False)
        .text_column(name='os', title=_('OS'), visible=True)
        .text_column(name='username', title=_('Issued by'), visible=True)
        .datetime_column(name='stamp', title=_('Date'), visible=True)
        .with_field_mappings(username='register_username', os='os_type')
        .with_filter_fields('hostname', 'ip', 'mac', 'register_username', 'os_type')
        .build()
    )

    @typing.override
    def get_item(self, item: 'Model') -> TokenItem:
        item = typing.cast('models.Server', item)  # We will receive for sure
        return TokenItem(
            id=item.uuid,
            name=str(_('Token isued by {} from {}')).format(item.register_username, item.ip),
            stamp=item.stamp,
            username=item.register_username,
            ip=item.ip,
            hostname=item.hostname,
            listen_port=item.listen_port,
            mac=item.mac,
            token=item.token,
            type=types.servers.ServerType(item.type).as_str(),
            os=item.os_type,
        )

    @typing.override
    def delete(self) -> str:
        """
        Processes a DELETE request
        """
        if len(self._args) != 1:
            raise RequestError('Delete need one and only one argument')

        self.check_access(
            self.MODEL(), types.permissions.PermissionType.ALL, root=True
        )  # Must have write permissions to delete

        try:
            self.MODEL.objects.get(uuid=process_uuid(self._args[0])).delete()
        except self.MODEL.DoesNotExist:
            raise NotFound('Element do not exists') from None

        return consts.OK


@dataclasses.dataclass
class ServerItem(types.rest.BaseRestItem):
    id: str
    hostname: str
    ip: str
    listen_port: int
    mac: str
    maintenance_mode: bool
    register_username: str
    stamp: datetime.datetime


# REST API For servers (except tunnel servers nor actors)
class ServersServers(DetailHandler[ServerItem]):

    CUSTOM_METHODS = [
        types.rest.ModelCustomMethod(
            'maintenance',
            method=types.rest.CustomMethodMethod.POST,
            description='Toggle maintenance mode for a server (enable if disabled, disable if enabled)',
        ),
        types.rest.ModelCustomMethod(
            'importcsv',
            method=types.rest.CustomMethodMethod.POST,
            description='Import server configuration from CSV data',
            params=types.rest.api.SchemaProperty(
                type='object',
                properties={
                    'data': types.rest.api.SchemaProperty(
                        type='string', description='CSV content with server entries'
                    ),
                    'has_header': types.rest.api.SchemaProperty(
                        type='boolean', description='Whether the CSV has a header row'
                    ),
                    'separator': types.rest.api.SchemaProperty(
                        type='string', description='CSV field separator character (default comma)'
                    ),
                },
            ),
        ),
    ]

    # Rest api related information to complete the auto-generated API
    REST_API_INFO = types.rest.api.RestApiInfo(
        typed=types.rest.api.RestApiInfoGuiType.SINGLE_TYPE,
    )

    def as_server_item(self, item: 'models.Server') -> ServerItem:
        return ServerItem(
            id=item.uuid,
            hostname=item.hostname,
            ip=item.ip,
            listen_port=item.listen_port,
            mac=item.mac if item.mac != consts.NULL_MAC else '',
            maintenance_mode=item.maintenance_mode,
            register_username=item.register_username,
            stamp=item.stamp,
        )

    @typing.override
    def get_items(self, parent: 'Model') -> types.rest.ItemsResult[ServerItem]:
        parent = typing.cast('models.ServerGroup', parent)  # We will receive for sure
        return [self.as_server_item(i) for i in self.filter_odata_queryset(parent.servers.all())]

    @typing.override
    def get_item(self, parent: 'Model', item: str) -> ServerItem:
        parent = typing.cast('models.ServerGroup', parent)  # We will receive for sure
        return self.as_server_item(parent.servers.get(uuid=process_uuid(item)))

    @typing.override
    def get_table(self, parent: 'Model') -> TableInfo:
        parent = ensure.is_instance(parent, models.ServerGroup)
        table_info = (
            ui_utils.TableBuilder(_('Servers of {0}').format(parent.name))
            .text_column(name='hostname', title=_('Hostname'))
            .text_column(name='ip', title=_('Ip'))
            .text_column(name='mac', title=_('Mac'))
        )
        if parent.is_managed():
            table_info.text_column(name='listen_port', title=_('Port'))

        return (
            table_info.dict_column(
                name='maintenance_mode',
                title=_('State'),
                dct={True: _('Maintenance'), False: _('Normal')},
            )
            .row_style(prefix='row-maintenance-', field='maintenance_mode')
            .build()
        )

    @typing.override
    def get_gui(self, parent: 'Model', for_type: str) -> list[types.ui.GuiElement]:
        parent = ensure.is_instance(parent, models.ServerGroup)
        kind, subkind = parent.server_type, parent.subtype
        title = _('of type') + f' {subkind.upper()} {kind.name.capitalize()}'
        gui_builder = ui_utils.GuiBuilder(order=100)
        if kind == types.servers.ServerType.UNMANAGED:
            return (
                gui_builder.add_text(
                    name='hostname',
                    label=gettext('Hostname'),
                    tooltip=gettext('Hostname of the server. It must be resolvable by UDS'),
                    default='',
                )
                .add_text(
                    name='ip',
                    label=gettext('IP'),
                )
                .add_text(
                    name='mac',
                    label=gettext('Server MAC'),
                    tooltip=gettext('Optional MAC address of the server'),
                    default='',
                )
                .add_info(
                    name='title',
                    default=title,
                )
                .build()
            )

        return (
            gui_builder.add_choice(
                name='server',
                label=gettext('Server'),
                tooltip=gettext('Server to include on group'),
                choices=[
                    types.ui.ChoiceItem(item.uuid, item.hostname)
                    for item in models.Server.objects.filter(type=parent.type, subtype=parent.subtype)
                    if item.groups.count() == 0
                ],
            )
            .add_info(name='title', default=title)
            .build()
        )

    @typing.override
    def save_item(self, parent: 'Model', item: str | None) -> typing.Any:
        parent = ensure.is_instance(parent, models.ServerGroup)
        # Item is the uuid of the server to add
        server: 'models.Server | None' = None  # Avoid warning on reference before assignment
        mac: str = ''
        if item is None:
            # Create new, depending on server type
            if parent.type == types.servers.ServerType.UNMANAGED:
                # Ensure mac is empty or valid
                mac = self._params['mac'].strip().upper()
                if mac and not net.is_valid_mac(mac):
                    raise exceptions.rest.RequestError(_('Invalid MAC address'))
                # Create a new one, and add it to group
                server = models.Server.objects.create(
                    register_username=self._user.pretty_name,
                    register_ip=self._request.ip,
                    ip=self._params['ip'],
                    hostname=self._params['hostname'],
                    listen_port=0,
                    mac=mac,
                    type=parent.type,
                    subtype=parent.subtype,
                    stamp=sql_now(),
                )
                # Add to group
                parent.servers.add(server)
                return {'id': server.uuid}
            elif parent.type == types.servers.ServerType.SERVER:
                # Get server
                try:
                    server = models.Server.objects.get(uuid=process_uuid(self._params['server']))
                    # Check server type is also SERVER
                    if server and server.type != types.servers.ServerType.SERVER:
                        logger.error('Server type for %s is not SERVER', server.host)
                        raise exceptions.rest.RequestError('Invalid server type') from None
                    parent.servers.add(server)
                except models.Server.DoesNotExist:
                    raise exceptions.rest.NotFound(f'Server not found: {self._params["server"]}') from None
                except Exception as e:
                    logger.error('Error getting server: %s', e)
                    raise exceptions.rest.ResponseError('Error getting server') from None

                return {'id': server.uuid}
        else:
            if parent.type == types.servers.ServerType.UNMANAGED:
                mac = self._params['mac'].strip().upper()
                if mac and not net.is_valid_mac(mac):
                    raise exceptions.rest.RequestError('Invalid MAC address')
                try:
                    models.Server.objects.filter(uuid=process_uuid(item)).update(
                        # Update register info also on update
                        register_username=self._user.pretty_name,
                        register_ip=self._request.ip,
                        hostname=self._params['hostname'],
                        ip=self._params['ip'],
                        mac=mac,
                        stamp=sql_now(),  # Modified now
                    )
                except models.Server.DoesNotExist:
                    raise exceptions.rest.NotFound(f'Server not found: {item}') from None
                except Exception as e:
                    logger.error('Error updating server: %s', e)
                    raise exceptions.rest.ResponseError('Error updating server') from None

            else:
                # Remove current server and add the new one in a single transaction
                try:
                    server = models.Server.objects.get(uuid=process_uuid(item))
                    parent.servers.add(server)
                except models.Server.DoesNotExist:
                    raise exceptions.rest.NotFound(f'Server not found: {item}') from None

            return {'id': item}

    @typing.override
    def delete_item(self, parent: 'Model', item: str) -> None:
        parent = ensure.is_instance(parent, models.ServerGroup)
        try:
            server = models.Server.objects.get(uuid=process_uuid(item))
            if parent.server_type == types.servers.ServerType.UNMANAGED:
                parent.servers.remove(server)  # Remove reference
                server.delete()  # and delete server
            else:
                parent.servers.remove(server)  # Just remove reference
        except models.Server.DoesNotExist:
            raise exceptions.rest.NotFound(f'Server not found: {item}') from None
        except Exception as e:
            logger.error('Error deleting server %s from %s: %s', item, parent, e)
            raise exceptions.rest.ResponseError('Error deleting server') from None

    # Custom methods
    def maintenance(self, parent: 'Model', id: str) -> typing.Any:
        """
        Custom method that swaps maintenance mode state for a server
        :param item:
        """
        ensure.is_instance(parent, models.ServerGroup)  # Just ensures type
        item = models.Server.objects.get(uuid=process_uuid(id))
        self.check_access(item, types.permissions.PermissionType.MANAGEMENT)
        item.maintenance_mode = not item.maintenance_mode
        item.save()
        return 'ok'

    def importcsv(self, parent: 'Model') -> typing.Any:
        """
        We receive the file
        Has no header, only the data.
        """
        parent = ensure.is_instance(parent, models.ServerGroup)
        data: str = self._params.get('data', '')
        has_header: bool = self._params.get('has_header', False)
        separator: str = self._params.get('separator', ',')
        logger.debug('Data received: %s, has_header: %s..., separator: %s', data[:64], has_header, separator)

        csv_file = io.StringIO(data)
        reader = csv.reader(csv_file, delimiter=separator)
        # String lines can have 1, 2 or 3 fields.
        # if 1, it's a hostname
        # if 2, it's a hostname + IP. Hostame can be empty, in this case, it will be the same as IP
        # if 3 or more, it's a hostname + IP + MAC. Hostame can be empty, in this case, it will be the same as IP. MAC can be empty, in this case, it will be set to NULL_MAC
        # if ip is empty and has a hostname, it will be kept, but if it has no hostname, it will be skipped
        import_errors: list[str] = []
        if has_header:
            try:
                next(reader)  # Skip header
            except StopIteration:
                return ['CSV has no data rows']

        for line_number, row in enumerate(reader, 1):
            if len(row) == 0:
                continue
            hostname = row[0].strip()
            ip = ''
            mac = consts.NULL_MAC
            if len(row) > 1:
                ip = row[1].strip()
            if len(row) > 2:
                mac = row[2].strip().upper().strip() or consts.NULL_MAC
                if mac and not net.is_valid_mac(mac):
                    import_errors.append(f'Line {line_number}: MAC {mac} is invalid, skipping')
                    continue  # skip invalid macs
            if ip and not net.is_valid_ip(ip):
                import_errors.append(f'Line {line_number}: IP {ip} is invalid, skipping')
                continue  # skip invalid ips if not empty
            # Must have at least a valid ip or a valid hostname
            if not ip and not hostname:
                import_errors.append(f'Line {line_number}: No IP or hostname, skipping')
                continue

            if hostname and not net.is_valid_host(hostname):
                # Log it has been skipped
                import_errors.append(f'Line {line_number}: Hostname {hostname} is invalid, skipping')
                continue  # skip invalid hostnames

            # Seems valid, create server if not exists already (by ip OR hostname)
            logger.debug('Creating server with ip %s, hostname %s and mac %s', ip, hostname, mac)
            try:
                q = parent.servers.all()
                if ip != '':
                    q = q.filter(ip=ip)
                if hostname != '':
                    q = q.filter(hostname=hostname)
                if q.count() == 0:
                    server = models.Server.objects.create(
                        register_username=self._user.pretty_name,
                        register_ip=self._request.ip,
                        ip=ip,
                        hostname=hostname,
                        listen_port=0,
                        mac=mac,
                        type=parent.type,
                        subtype=parent.subtype,
                        stamp=sql_now(),
                    )
                    parent.servers.add(server)  # And register it on group
                else:
                    # Log it has been skipped
                    import_errors.append(f'Line {line_number}: duplicated server, skipping')
            except Exception as e:
                import_errors.append(f'Error creating server on line {line_number}: {str(e)}')
                logger.exception('Error creating server on line %s', line_number)

        return import_errors


@dataclasses.dataclass
class GroupItem(types.rest.BaseRestItem):
    id: str
    name: str
    comments: str
    type: str
    subtype: str
    type_name: str
    tags: list[str]
    servers_count: int
    permission: types.permissions.PermissionType
    weights_cpu: int
    weights_memory: int
    weights_users: int
    weights_max_expected_users: int


class ServersGroups(ModelHandler[GroupItem]):

    CUSTOM_METHODS = [
        types.rest.ModelCustomMethod(
            'stats',
            True,
            description='Retrieve aggregate server statistics including counts by state, type, and resource usage',
        ),
    ]
    MODEL = models.ServerGroup
    FILTER = {
        'type__in': [
            types.servers.ServerType.SERVER,
            types.servers.ServerType.UNMANAGED,
        ]
    }
    DETAIL = {'servers': ServersServers}

    PATH = 'servers'
    NAME = 'groups'

    FIELDS_TO_SAVE = [
        'name',
        'comments',
        'type:_',
        'subtype:_',
        'data_type:_',
        'tags',
    ]  # Subtype is appended on pre_save

    TABLE = (
        ui_utils.TableBuilder(_('Servers Groups'))
        .text_column(name='name', title=_('Name'), visible=True)
        .text_column(name='comments', title=_('Comments'))
        .text_column(name='type_name', title=_('Type'), visible=True)
        .text_column(name='type', title='', visible=False)
        .text_column(name='subtype', title=_('Subtype'), visible=True)
        .numeric_column(name='servers_count', title=_('Servers'), width='5rem')
        .text_column(name='tags', title=_('tags'), visible=False)
        .build()
    )

    # Rest api related information to complete the auto-generated API
    REST_API_INFO = types.rest.api.RestApiInfo(
        typed=types.rest.api.RestApiInfoGuiType.MULTIPLE_TYPES,
    )

    @typing.override
    def enum_types(
        self, *args: typing.Any, **kwargs: typing.Any
    ) -> collections.abc.Generator[types.rest.TypeInfo, None, None]:
        for i in types.servers.ServerSubtype.manager().enum():
            yield types.rest.TypeInfo(
                name=i.description,
                type=f'{i.type.name}@{i.subtype}',
                description='',
                icon=i.icon,
                group=gettext('Managed') if i.managed else gettext('Unmanaged'),
            )

    @typing.override
    def get_gui(self, for_type: str) -> list[types.ui.GuiElement]:
        if '@' not in for_type:  # If no subtype, use default
            for_type += '@default'
        kind, subkind = for_type.split('@')[:2]
        if kind == types.servers.ServerType.SERVER.name:
            kind = _('Standard')
        elif kind == types.servers.ServerType.UNMANAGED.name:
            kind = _('Unmanaged')
        title = _('of type') + f' {subkind.upper()} {kind}'

        return (
            ui_utils.GuiBuilder()
            .add_stock_field(types.rest.stock.StockField.NAME)
            .add_stock_field(types.rest.stock.StockField.TAGS)
            .add_stock_field(types.rest.stock.StockField.COMMENTS)
            # Add weigths fields
            # class ServerStatsWeights:
            # cpu: float = 0.3
            # memory: float = 0.6
            # users: float = 0.1
            # max_expected_users: int = 100  # Max expected users to consider in load calculation
            # min_memory: int = 0  # Minimum free memory in bytes to consider server as available, 0 means no limit
            # users_limit: int = 0  # Maximum number of users to consider server as available, 0 means no limit
            .new_tab(_('Load calculation weights'))
            .add_numeric(
                name='weights_cpu',
                label=_('CPU weight'),
                tooltip=_('Weight of CPU load on server load calculation. Value between 0 and 1'),
                default=30,
            )
            .add_numeric(
                name='weights_memory',
                label=_('Memory weight'),
                tooltip=_('Weight of memory load on server load calculation. Value between 0 and 1'),
                default=60,
            )
            .add_numeric(
                name='weights_users',
                label=_('Users weight'),
                tooltip=_('Weight of users load on server load calculation. Value between 0 and 1'),
                default=10,
            )
            .add_numeric(
                name='weights_max_expected_users',
                label=_('Max expected users'),
                tooltip=_('Max expected users to consider in load calculation'),
                default=100,
            )
            .add_info(
                name='title',
                default=title,
            )
            .build()
        )

    @typing.override
    def pre_save(self, fields: dict[str, typing.Any]) -> None:
        # Allow from admin gui (data_type) or from API (type and subtype)
        # Extract weights if they are on fields
        if 'data_type' in fields:
            type, subtype = typing.cast(str, fields['data_type']).split('@')
            fields['type'] = types.servers.ServerType[type.upper()].value
            fields['subtype'] = subtype
            del fields['data_type']
        elif not ('type' in fields and 'subtype' in fields):
            raise exceptions.rest.RequestError('Type and subtype must be provided') from None

        return super().pre_save(fields)

    @typing.override
    def post_save(self, item: 'Model') -> None:
        # Save weights if they are on fields
        item = ensure.is_instance(item, models.ServerGroup)
        weights_params = [
            'weights_cpu',
            'weights_memory',
            'weights_users',
            'weights_max_expected_users',
        ]
        args = self.fields_from_params(weights_params)
        # Load parameters to be normalized
        cpu = int(args['weights_cpu']) if args['weights_cpu'] is not None else 0
        memory = int(args['weights_memory']) if args['weights_memory'] is not None else 0
        users = int(args['weights_users']) if args['weights_users'] is not None else 0
        # rest of parameters, not normalized
        max_expected_users = (
            int(args['weights_max_expected_users']) if args['weights_max_expected_users'] is not None else 100
        )
        # Normalize weights to sum 100 if they are not
        all_sum = cpu + memory + users
        cpu = cpu * 100 // all_sum if all_sum > 0 else 0
        memory = memory * 100 // all_sum if all_sum > 0 else 0
        users = 100 - cpu - memory
        max_expected_users = max_expected_users if max_expected_users > 0 else 100
        # Set property, autosaves
        item.weights = types.servers.ServerStatsWeights(
            cpu=float(cpu) / 100,
            memory=float(memory) / 100,
            users=float(users) / 100,
            max_expected_users=max_expected_users,
        )

    @typing.override
    def get_item(self, item: 'Model') -> GroupItem:
        item = ensure.is_instance(item, models.ServerGroup)
        return GroupItem(
            id=item.uuid,
            name=item.name,
            comments=item.comments,
            type=f'{types.servers.ServerType(item.type).name}@{item.subtype}',
            subtype=item.subtype.capitalize(),
            type_name=types.servers.ServerType(item.type).name.capitalize(),
            tags=[tag.tag for tag in item.tags.all()],
            servers_count=item.servers.count(),
            permission=permissions.effective_permissions(self._user, item),
            weights_cpu=int(item.weights.cpu * 100 + 0.5),
            weights_memory=int(item.weights.memory * 100 + 0.5),
            weights_users=int(item.weights.users * 100 + 0.5),
            weights_max_expected_users=item.weights.max_expected_users,
        )

    @typing.override
    def delete_item(self, item: 'Model') -> None:
        item = ensure.is_instance(item, models.ServerGroup)
        """
        Processes a DELETE request
        """
        self.check_access(
            self.MODEL(), permissions.PermissionType.ALL, root=True
        )  # Must have write permissions to delete

        try:
            if item.type == types.servers.ServerType.UNMANAGED:
                # Unmanaged has to remove ALSO the servers
                for server in item.servers.all():
                    server.delete()
            item.delete()
        except self.MODEL.DoesNotExist:
            raise NotFound('Element do not exists') from None

    def stats(self, item: 'Model') -> typing.Any:
        # Avoid circular imports
        from uds.core.managers.servers import ServerManager

        item = ensure.is_instance(item, models.ServerGroup)

        return [
            {
                'stats': s[0].as_dict() if s[0] else None,
                'server': {
                    'id': s[1].uuid,
                    'hostname': s[1].hostname,
                    'mac': s[1].mac if s[1].mac != consts.NULL_MAC else '',
                    'ip': s[1].ip,
                    'load': s[0].load(weights=item.weights) if s[0] else 0,
                    'weights': item.weights.as_dict(),
                },
            }
            for s in ServerManager.manager().get_server_stats(item.servers.all())
        ]

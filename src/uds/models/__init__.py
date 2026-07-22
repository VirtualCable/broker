# pylint: disable=unused-import

#
# Copyright (c) 2012-2023 Virtual Cable S.L.
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

# Imports all models so they are available for migrations, etc..
from .managed_object_model import ManagedObjectModel as ManagedObjectModel

# Permissions
from .permissions import Permissions as Permissions

# Services
from .provider import Provider as Provider
from .service import Service as Service, ServiceTokenAlias as ServiceTokenAlias

# Os managers
from .osmanager import OSManager as OSManager

# Transports
from .transport import Transport as Transport
from .network import Network as Network

# Authenticators
from .authenticator import Authenticator as Authenticator
from .user import User as User
from .group import Group as Group

# Provisioned services
from .service_pool import ServicePool as ServicePool
from .meta_pool import MetaPool as MetaPool, MetaPoolMember as MetaPoolMember
from .service_pool_group import ServicePoolGroup as ServicePoolGroup
from .service_pool_publication import (
    ServicePoolPublication as ServicePoolPublication,
    ServicePoolPublicationChangelog as ServicePoolPublicationChangelog,
)

from .user_service import UserService as UserService
from .user_service_session import UserServiceSession as UserServiceSession

# Especific log information for an user service
from .log import Log as Log

# Stats
from .stats_counters import StatsCounters as StatsCounters
from .stats_counters_accum import StatsCountersAccum as StatsCountersAccum
from .stats_events import StatsEvents as StatsEvents

# General utility models, such as a database cache (for caching remote content of slow connections to external services providers for example)
# We could use django cache (and maybe we do it in a near future), but we need to clean up things when objecs owning them are deleted
from .cache import Cache as Cache
from .config import Config as Config
from .storage import Storage as Storage
from .unique_id import UniqueId as UniqueId
from .properties import Properties as Properties

# Workers/Schedulers related
from .scheduler import Scheduler as Scheduler
from .delayed_task import DelayedTask as DelayedTask

# Image galery related
from .image import Image as Image

# Ticket storage
from .ticket_store import TicketStore as TicketStore

# Calendar related
from .calendar import Calendar as Calendar
from .calendar_rule import CalendarRule as CalendarRule

from .calendar_access import CalendarAccess as CalendarAccess, CalendarAccessMeta as CalendarAccessMeta
from .calendar_action import CalendarAction as CalendarAction

# Accounting
from .account import Account as Account
from .account_usage import AccountUsage as AccountUsage


# Tagging
from .tag import Tag as Tag, TaggingMixin as TaggingMixin

# Servers
from .servers import Server as Server, ServerGroup as ServerGroup

# Notifications & Alerts
from .notifications import Notification as Notification, Notifier as Notifier, LogLevel as LogLevel

# Multi factor authentication
from .mfa import MFA as MFA

# Immutable audit log (blockchain-like hash chain)
from .immutable_log import ImmutableLog as ImmutableLog

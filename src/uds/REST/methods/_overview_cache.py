# -*- coding: utf-8 -*-
#
# Copyright (c) 2014-2019 Virtual Cable S.L.
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
Shared short-lived cache for REST overview (get_items) results.

The dashboard drill-down hammers several overview endpoints (it asks every
authenticator / every service pool at once) yet the underlying data changes
seldom, so we memoize the assembled list for a short while. flush=1 bypasses it.

Lives in its own module so both users_groups and user_services can use it
without an import cycle (users_groups imports user_services).
"""
import collections.abc
import typing

from django.db import models as django_models

from uds.core import consts
from uds.core.util.cache import Cache
from uds.models import Group, User

# Authenticator/pool overviews are hammered by the dashboard drill-down yet change
# seldom, so we memoize the assembled list for a short while. flush=1 bypasses it.
_overview_cache: typing.Final = Cache('UsersGroupsOverview')

_T = typing.TypeVar('_T')


class _SupportsQueryParams(typing.Protocol):
    def query_params(self) -> collections.abc.Mapping[str, typing.Any]: ...


def cached_overview(
    handler: _SupportsQueryParams,
    cache_key: str,
    builder: collections.abc.Callable[[], list[_T]],
) -> list[_T]:
    flush = str(handler.query_params().get('flush', '')).lower() in ('1', 'true', 'yes')
    if not flush:
        cached = _overview_cache.get(cache_key, consts.cache.CACHE_NOT_FOUND)
        if cached is not consts.cache.CACHE_NOT_FOUND:
            return typing.cast('list[_T]', cached)
    data = builder()
    _overview_cache.put(cache_key, data, consts.cache.SHORT_CACHE_TIMEOUT)
    return data


def invalidate_overviews(*args: typing.Any, **kwargs: typing.Any) -> None:
    _overview_cache.clear()


# An edit must be visible on the next listing, so drop every overview on write.
# Deliberately not hooked to UserService: its churn would keep the cache empty,
# so the counts derived from it stay stale until the timeout.
for _model in (User, Group):
    django_models.signals.post_save.connect(invalidate_overviews, sender=_model)
    django_models.signals.post_delete.connect(invalidate_overviews, sender=_model)

# -*- coding: utf-8 -*-
#
# Copyright (c) 2025 Virtual Cable S.L.
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
Every concrete REST handler must implement the whole abstract contract of its base.

``ModelHandler`` and ``DetailHandler`` are ABCs, and the dispatcher instantiates
them per request. A subclass missing an ``@abstractmethod`` is not detected at
import time: it raises ``TypeError`` on instantiation, which the REST layer turns
into a generic "Error 500: Unexpected error" with an empty table on the admin UI.

Importing the dispatcher registers every handler module, so ``__subclasses__()``
walking below sees the complete tree.
"""

import collections.abc
import importlib
import typing

from uds.REST.model.base import BaseModelHandler
from uds.REST.model.detail import DetailHandler
from uds.REST.model.master import ModelHandler

importlib.import_module('uds.REST.dispatcher')

_AnyHandler: typing.TypeAlias = type[BaseModelHandler[typing.Any]]


def _descendants(cls: _AnyHandler) -> collections.abc.Iterator[_AnyHandler]:
    for subclass in cls.__subclasses__():
        yield subclass
        yield from _descendants(subclass)


def _pending_abstracts(base: _AnyHandler) -> dict[str, list[str]]:
    return {
        f"{subclass.__module__}.{subclass.__qualname__}": sorted(subclass.__abstractmethods__)
        for subclass in _descendants(base)
        if subclass.__abstractmethods__
    }


def test_detail_handlers_implement_abstract_methods() -> None:
    assert list(_descendants(DetailHandler)), "no DetailHandler subclass found, registration is broken"
    assert _pending_abstracts(DetailHandler) == {}


def test_model_handlers_implement_abstract_methods() -> None:
    assert list(_descendants(ModelHandler)), "no ModelHandler subclass found, registration is broken"
    assert _pending_abstracts(ModelHandler) == {}

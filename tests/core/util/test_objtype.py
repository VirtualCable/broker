# -*- coding: utf-8 -*-
#
# Copyright (c) 2026 Virtual Cable S.L.
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
Tests for :mod:`uds.core.util.objtype`.

Covers:
- ``ObjectType`` enum integrity (unique values, registered count)
- ``.model`` and ``.type`` properties
- ``ObjectType.from_model`` happy path for several model classes
- ``ObjectType.from_model`` raises ``ValueError`` for unknown models
- ``__eq__`` semantics (ObjectType vs ObjectType, ObjectType vs int)
- Roundtrip ``type -> from_model`` consistency
"""

from __future__ import annotations

import typing

from django.db import models

from uds.core.util import objtype

from tests.utils.test import UDSTestCase


def _bare_instance(cls: type) -> typing.Any:
    """Build a Django model instance without invoking ``__init__``.

    Useful for tests that only need an instance whose ``type(...)`` is the
    given model class — DB defaults / required fields are irrelevant.
    """
    return cls()


class ObjectTypeTest(UDSTestCase):
    def test_enum_is_unique(self) -> None:
        """Each ObjectType member must have a unique integer value."""
        values: list[int] = [member.type for member in objtype.ObjectType]
        self.assertEqual(len(values), len(set(values)), "ObjectType values must be unique")

    def test_minimum_registered_types(self) -> None:
        """Sanity check: at least the core types must be registered."""
        for required in (
            objtype.ObjectType.PROVIDER,
            objtype.ObjectType.SERVICE,
            objtype.ObjectType.USER,
            objtype.ObjectType.POOL,
            objtype.ObjectType.GROUP,
        ):
            self.assertIsInstance(required.type, int)

    def test_model_property_returns_model_class(self) -> None:
        """``.model`` should yield the registered Django model class."""
        from uds import models

        self.assertIs(objtype.ObjectType.PROVIDER.model, models.Provider)
        self.assertIs(objtype.ObjectType.USER.model, models.User)
        self.assertIs(objtype.ObjectType.SERVICE.model, models.Service)
        self.assertIs(objtype.ObjectType.POOL.model, models.ServicePool)

    def test_type_property_is_int(self) -> None:
        """``.type`` returns the integer code for the object type."""
        self.assertEqual(objtype.ObjectType.PROVIDER.type, 1)
        self.assertEqual(objtype.ObjectType.SERVICE.type, 2)
        self.assertEqual(objtype.ObjectType.USER.type, 9)
        self.assertEqual(objtype.ObjectType.GROUP.type, 10)

    def test_from_model_returns_enum(self) -> None:
        """``from_model`` returns the right enum member for several models."""
        from uds import models

        result = objtype.ObjectType.from_model(_bare_instance(models.Provider))
        self.assertIs(result, objtype.ObjectType.PROVIDER)
        self.assertEqual(result.type, 1)

        result = objtype.ObjectType.from_model(_bare_instance(models.User))
        self.assertIs(result, objtype.ObjectType.USER)

        result = objtype.ObjectType.from_model(_bare_instance(models.ServicePool))
        self.assertIs(result, objtype.ObjectType.POOL)

    def test_from_model_each_registered_member(self) -> None:
        """``from_model(member.model)`` must round-trip for every ObjectType."""
        for member in objtype.ObjectType:
            with self.subTest(member=member):
                self.assertIs(objtype.ObjectType.from_model(_bare_instance(member.model)), member)

    def test_from_model_unregistered_raises(self) -> None:
        """Unknown model classes raise ``ValueError``.

        ``from_model`` formats the offending argument into the exception
        message, so we pass a non-Model object whose ``__str__`` is harmless
        (Django's ``Model.__str__`` chokes on bare ``__new__`` instances
        because ``_meta.pk`` isn't fully wired up).
        """
        # ``type`` (the metaclass itself) is not a registered model class
        with self.assertRaises(ValueError):
            objtype.ObjectType.from_model(typing.cast(models.Model, type))

        class _NotARegisteredModel:
            """Plain class — not a Django Model and not registered."""

        with self.assertRaises(ValueError) as ctx:
            objtype.ObjectType.from_model(typing.cast(models.Model, _NotARegisteredModel()))
        self.assertIn("Invalid model type", str(ctx.exception))

    def test_from_model_builtin_type_raises(self) -> None:
        """Builtins (non-Model) are not registered."""
        with self.assertRaises(ValueError):
            objtype.ObjectType.from_model(int)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            objtype.ObjectType.from_model(str)  # type: ignore[arg-type]

    def test_eq_object_type(self) -> None:
        """Same enum compares True, different enum compares False."""
        self.assertTrue(objtype.ObjectType.PROVIDER == objtype.ObjectType.PROVIDER)
        self.assertFalse(objtype.ObjectType.PROVIDER == objtype.ObjectType.SERVICE)  # pyright: ignore[reportUnnecessaryComparison]

    def test_eq_int(self) -> None:
        """Comparison with the underlying int code is supported.

        Note: ``ObjectType.__eq__`` returns ``super().__eq__(other) or self.value.obj_type == other``.
        When ``other`` is an ``int``, ``super().__eq__`` returns ``NotImplemented`` (truthy in ``or``),
        so the int comparison is always evaluated.
        """
        self.assertTrue(objtype.ObjectType.PROVIDER == 1)  # pyright: ignore[reportUnnecessaryComparison]
        self.assertTrue(objtype.ObjectType.SERVICE == 2)  # pyright: ignore[reportUnnecessaryComparison]
        self.assertFalse(objtype.ObjectType.PROVIDER == 2)  # pyright: ignore[reportUnnecessaryComparison]
        self.assertFalse(objtype.ObjectType.SERVICE == 1)  # pyright: ignore[reportUnnecessaryComparison]

    def test_eq_other_types(self) -> None:
        """Comparison against unrelated types never raises.

        The implementation always falls back to the int comparison; with
        non-int ``other`` that comparison returns ``False``. Document that
        ``1.0 == 1`` evaluates ``True`` (Python numeric equality).
        """
        self.assertFalse(objtype.ObjectType.PROVIDER == "PROVIDER")  # pyright: ignore[reportUnnecessaryComparison]
        self.assertFalse(objtype.ObjectType.PROVIDER == "1")  # pyright: ignore[reportUnnecessaryComparison]
        self.assertFalse(objtype.ObjectType.PROVIDER == None)  # noqa: E711  (intentional)
        self.assertTrue(objtype.ObjectType.PROVIDER == 1.0)

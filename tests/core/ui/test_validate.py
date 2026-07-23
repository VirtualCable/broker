# -*- coding: utf-8 -*-
#
# Tests for InputField.validate() and related fixes (audit 2026-07-23).
#
""" """

import datetime

from uds.core import consts
from uds.core import types
from uds.core.ui.user_interface import gui

from ...utils.test import UDSTestCase


class TextFieldPatternDefaultTest(UDSTestCase):
    def test_validate_default_pattern_does_not_crash(self) -> None:
        # Today: TextField(label='T').validate() raises AssertionError
        # because `_field_info.pattern` stays None due to `if pattern:`.
        # After fix: returns True (no pattern = no validation).
        t = gui.TextField(label="T")
        self.assertTrue(t.validate())

    def test_validate_explicit_none_pattern_does_not_crash(self) -> None:
        t = gui.TextField(label="T", pattern=types.ui.FieldPatternType.NONE)
        self.assertTrue(t.validate())

    def test_validate_empty_string_pattern_does_not_crash(self) -> None:
        # Edge case: passing "" as pattern used to also crash (also falsy).
        t = gui.TextField(label="T", pattern="")
        self.assertTrue(t.validate())

    def test_validate_ipv4_pattern_rejects_bad_ip(self) -> None:
        t = gui.TextField(label="T", pattern=types.ui.FieldPatternType.IPV4)
        t.value = "not-an-ip"
        self.assertFalse(t.validate())

    def test_validate_ipv4_pattern_accepts_good_ip(self) -> None:
        t = gui.TextField(label="T", pattern=types.ui.FieldPatternType.IPV4)
        t.value = "192.168.1.1"
        self.assertTrue(t.validate())


class RequiredValidateTest(UDSTestCase):
    def test_required_empty_value_is_invalid(self) -> None:
        f = gui.TextField(label="T", required=True, value="")
        self.assertFalse(f.validate())

    def test_required_none_value_is_invalid(self) -> None:
        f = gui.TextField(label="T", required=True)
        # When value is None and required=True, that's an invalid state.
        f._field_info.value = None
        self.assertFalse(f.validate())

    def test_not_required_empty_value_is_valid(self) -> None:
        f = gui.TextField(label="T", required=False, value="")
        self.assertTrue(f.validate())

    def test_required_none_with_empty_value_is_valid(self) -> None:
        # `required=None` (unset) means not required by the original
        # ``required`` property (it filters None -> False), so an empty
        # value is considered valid.
        f = gui.TextField(label="T", value="")
        self.assertTrue(f.validate())

    def test_required_with_default_falls_back(self) -> None:
        # If value is None but default is non-empty, the getter returns
        # the default, so validate() should be True.
        f = gui.TextField(label="T", required=True, default="hello")
        self.assertTrue(f.validate())


class NumericFieldValidateTest(UDSTestCase):
    def test_validate_min_value_enforced(self) -> None:
        # B1
        n = gui.NumericField(label="P", min_value=0, value=-1)
        self.assertFalse(n.validate())

    def test_validate_max_value_enforced(self) -> None:
        n = gui.NumericField(label="P", max_value=100, value=999)
        self.assertFalse(n.validate())

    def test_validate_within_range_is_valid(self) -> None:
        n = gui.NumericField(label="P", min_value=0, max_value=100, value=50)
        self.assertTrue(n.validate())

    def test_validate_without_bounds_accepts_anything(self) -> None:
        n = gui.NumericField(label="P", value=-9999)
        self.assertTrue(n.validate())

    def test_set_value_invalid_sets_default(self) -> None:
        n = gui.NumericField(label="N", value=42, default=99)
        n.value = "not-a-number"  # type: ignore
        self.assertEqual(n.value, 99)


class MultiChoiceValidateTest(UDSTestCase):
    def test_validate_all_items_in_choices(self) -> None:
        f = gui.MultiChoiceField(
            label="M",
            choices=[gui.choice_item("a", "A"), gui.choice_item("b", "B")],
            value=["a", "b"],
        )
        self.assertTrue(f.validate())

    def test_validate_item_not_in_choices_is_invalid(self) -> None:
        f = gui.MultiChoiceField(
            label="M",
            choices=[gui.choice_item("a", "A"), gui.choice_item("b", "B")],
            value=["a", "c"],
        )
        self.assertFalse(f.validate())

    def test_validate_empty_choices_value_is_invalid(self) -> None:
        f = gui.MultiChoiceField(
            label="M",
            choices=[gui.choice_item("a", "A")],
            value=["zzz"],
        )
        self.assertFalse(f.validate())


class ImageChoiceValidateTest(UDSTestCase):
    def test_validate_id_in_choices(self) -> None:
        f = gui.ImageChoiceField(
            label="I",
            choices=[gui.choice_image("1", "One", "i1"), gui.choice_image("2", "Two", "i2")],
            value="1",
        )
        self.assertTrue(f.validate())

    def test_validate_id_not_in_choices(self) -> None:
        f = gui.ImageChoiceField(
            label="I",
            choices=[gui.choice_image("1", "One", "i1")],
            value="99",
        )
        self.assertFalse(f.validate())


class CheckBoxValidateTest(UDSTestCase):
    def test_validate_true_value(self) -> None:
        f = gui.CheckBoxField(label="C", value=True)
        self.assertTrue(f.validate())

    def test_validate_false_value(self) -> None:
        f = gui.CheckBoxField(label="C", value=False)
        self.assertTrue(f.validate())


class DateFieldDegradeTest(UDSTestCase):
    def test_invalid_string_value_degrades_to_never(self) -> None:
        # Today: d.value = 'not-a-date' raises ValueError.
        # After fix: degrades to consts.NEVER.date().
        d = gui.DateField(label="D")
        d.value = "not-a-date"
        self.assertEqual(d.value, consts.NEVER.date())

    def test_invalid_type_degrades_to_never(self) -> None:
        d = gui.DateField(label="D")
        d.value = 12345  # type: ignore
        self.assertEqual(d.value, consts.NEVER.date())

    def test_valid_string_value_preserved(self) -> None:
        d = gui.DateField(label="D")
        d.value = "2024-01-15"
        self.assertEqual(d.value, datetime.date(2024, 1, 15))

    def test_valid_date_value_preserved(self) -> None:
        d = gui.DateField(label="D")
        d.value = datetime.date(2024, 1, 15)
        self.assertEqual(d.value, datetime.date(2024, 1, 15))

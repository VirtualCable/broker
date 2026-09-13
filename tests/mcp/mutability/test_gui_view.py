"""Agent renderer of gui definitions (uds.mutability.gui_view).

Pure unit tests (no DB): the renderer is the only place where the gui
turns into the agent field surface, so its decision table (roles,
representable types, foreign overlays) is pinned here.
"""

import logging
import typing
import unittest

from uds.core.types import ui as types_ui
from uds.core.types.mutability import FieldMutability, FieldMutabilityRole
from uds.core.util import ui as ui_builder
from uds.mutability import gui_view

JsonObject = dict[str, typing.Any]


def _element(
    name: str,
    field_type: types_ui.FieldType = types_ui.FieldType.TEXT,
    *,
    label: str = "Label",
    tooltip: str = "Tooltip",
    value: typing.Any = None,
    choices: types_ui.ChoicesType | None = None,
    overlay: typing.Any = None,
) -> types_ui.GuiElement:
    return types_ui.GuiElement(
        name=name,
        value=value,
        gui=types_ui.FieldInfo(
            label=label,
            tooltip=tooltip,
            order=0,
            type=field_type,
            choices=choices,
        ),
        overlay=overlay,
    )


def _definition(element: types_ui.GuiElement, **kwargs: typing.Any) -> JsonObject:
    """Render an element, asserting it reached the agent surface."""
    definition = gui_view.agent_definition(element, **kwargs)
    assert definition is not None
    return definition


class OverlayValidationTest(unittest.TestCase):
    def test_no_overlay_defaults_to_proposable(self) -> None:
        element = _element("name")
        self.assertIs(gui_view.role_of(element), FieldMutabilityRole.PROPOSABLE)
        self.assertEqual(_definition(element)["name"], "name")

    def test_foreign_overlay_is_ignored(self) -> None:
        # The overlay slot is generic: anything that is not a FieldMutability
        # must behave exactly like no overlay at all (never crash).
        element = _element("name", overlay={"role": "hidden"})
        self.assertIsNone(gui_view.mutability_of(element))
        self.assertIsNotNone(gui_view.agent_definition(element))

    def test_field_mutability_overlay_is_recognized(self) -> None:
        element = _element("name", overlay=FieldMutability.hidden())
        self.assertIsNotNone(gui_view.mutability_of(element))
        self.assertIs(gui_view.role_of(element), FieldMutabilityRole.HIDDEN)


class RolesTest(unittest.TestCase):
    def test_hidden_and_relation_are_not_proposed(self) -> None:
        hidden = _element("secret_ref", overlay=FieldMutability.hidden())
        relation = _element(
            "members",
            overlay=FieldMutability.relation(
                item_type="servicepool",
                item_label="pool",
                detail_handler="uds.REST.methods.meta_service_pools.MetaServicesPool",
                detail_path="meta_pools/{uuid}/pools",
                parent_collection="meta_pools",
                relation_manager="members",
            ),
        )
        self.assertIsNone(gui_view.agent_definition(hidden))
        self.assertIsNone(gui_view.agent_definition(relation))
        # ... but they still exist for consumers that read the gui
        self.assertIs(gui_view.role_of(relation), FieldMutabilityRole.RELATION)

    def test_context_travels_in_fingerprint_but_not_in_definitions(self) -> None:
        context = _element(
            "image_id",
            field_type=types_ui.FieldType.IMAGECHOICE,
            overlay=FieldMutability.context(agent_tooltip="Icon reference"),
        )
        normal = _element("name")
        elements = [normal, context]
        self.assertEqual([d["name"] for d in gui_view.agent_definitions(elements)], ["name"])
        self.assertEqual(gui_view.fingerprint_names(elements), ["name", "image_id"])

    def test_agent_tooltip_overrides_human_one(self) -> None:
        element = _element(
            "image_id",
            tooltip="Human tooltip",
            overlay=FieldMutability.context(agent_tooltip="Agent tooltip"),
        )
        # context role: not in the proposable definitions...
        self.assertIsNone(gui_view.agent_definition(element))
        # ... but the override is what the renderer would emit if proposable
        proposable = _element(
            "name",
            tooltip="Human tooltip",
            overlay=FieldMutability.proposable(agent_tooltip="Agent tooltip"),
        )
        self.assertEqual(_definition(proposable)["tooltip"], "Agent tooltip")


class RepresentableTypesTest(unittest.TestCase):
    def test_unrepresentable_type_warns_and_is_omitted(self) -> None:
        element = _element("created", field_type=types_ui.FieldType.DATE)
        with self.assertLogs("uds.mutability.gui_view", level=logging.WARNING) as logs:
            self.assertIsNone(gui_view.agent_definition(element))
        self.assertTrue(any("created" in message for message in logs.output))

    def test_representable_types_have_no_warning(self) -> None:
        with self.assertNoLogs("uds.mutability.gui_view", level=logging.WARNING):
            for field_type in gui_view.REPRESENTABLE_FIELD_TYPES:
                self.assertIsNotNone(gui_view.agent_definition(_element("field", field_type)))


class UniversalOmissionsTest(unittest.TestCase):
    """INFO and readonly are policy omissions: silent, not support gaps."""

    def test_info_field_omitted_without_warning(self) -> None:
        element = _element("help_text", field_type=types_ui.FieldType.INFO)
        with self.assertNoLogs("uds.mutability.gui_view", level=logging.WARNING):
            self.assertIsNone(gui_view.agent_definition(element))
            self.assertFalse(gui_view.is_proposable(element))

    def test_info_field_out_of_fingerprint(self) -> None:
        # INFO values never travel in the PUT payload, so the CAS base
        # must not track them either
        elements = [_element("name"), _element("help_text", field_type=types_ui.FieldType.INFO)]
        self.assertEqual(gui_view.fingerprint_names(elements), ["name"])

    def test_readonly_field_omitted_without_warning(self) -> None:
        element = _element("on_logout")
        element.gui.readonly = True
        with self.assertNoLogs("uds.mutability.gui_view", level=logging.WARNING):
            self.assertIsNone(gui_view.agent_definition(element))
            self.assertFalse(gui_view.is_proposable(element))

    def test_readonly_field_stays_in_fingerprint(self) -> None:
        # The merged PUT carries readonly fields (unchanged), so the
        # fingerprint must track them like the handler ETag does
        element = _element("on_logout")
        element.gui.readonly = True
        self.assertEqual(gui_view.fingerprint_names([_element("name"), element]), ["name", "on_logout"])

    def test_proposable_elements_filters_by_policy(self) -> None:
        elements = [
            _element("name"),
            _element("help_text", field_type=types_ui.FieldType.INFO),
            _element("on_logout", overlay=FieldMutability.context()),
            _element("networks", overlay=FieldMutability.hidden()),
        ]
        self.assertEqual([e.name for e in gui_view.proposable_elements(elements)], ["name"])


class FromInstanceTest(unittest.TestCase):
    def test_from_instance_marks_definitions(self) -> None:
        model = _element("name")
        config = _element("test_url")

        def marker(name: str) -> bool:
            return name != "name"

        self.assertIs(_definition(model, from_instance=marker)["from_instance"], False)
        self.assertIs(_definition(config, from_instance=marker)["from_instance"], True)

    def test_without_from_instance_no_key_emitted(self) -> None:
        # Renderers whose whole surface is model-backed (metapool) must
        # keep the historical definition shape
        self.assertNotIn("from_instance", _definition(_element("name")))


class DefinitionsTest(unittest.TestCase):
    def test_gui_metadata_flows_into_definition(self) -> None:
        element = _element(
            "short_name",
            label="Short Name",
            tooltip="32 chars",
            choices=None,
            overlay=None,
        )
        element.gui.length = 32
        element.gui.required = True
        definition = _definition(element)
        self.assertEqual(definition["label"], "Short Name")
        self.assertEqual(definition["tooltip"], "32 chars")
        self.assertEqual(definition["length"], 32)
        self.assertIs(definition["required"], True)
        self.assertIs(definition["secret"], False)
        self.assertNotIn("choices", definition)

    def test_secret_types_are_flagged(self) -> None:
        password = _element("password", field_type=types_ui.FieldType.PASSWORD)
        hidden = _element("hidden_field", field_type=types_ui.FieldType.HIDDEN)
        self.assertIs(_definition(password)["secret"], True)
        self.assertIs(_definition(hidden)["secret"], True)

    def test_choices_render_as_value_label(self) -> None:
        choices = [
            types_ui.ChoiceItem(id=0, text="Zero"),
            types_ui.ChoiceItem(id=1, text="One"),
        ]
        element = _element("policy", field_type=types_ui.FieldType.CHOICE, choices=choices)
        self.assertEqual(
            _definition(element)["choices"],
            [
                {"value": 0, "label": "Zero"},
                {"value": 1, "label": "One"},
            ],
        )

    def test_callable_choices_are_not_resolved(self) -> None:
        element = _element(
            "image_id",
            field_type=types_ui.FieldType.IMAGECHOICE,
            choices=lambda: [types_ui.ChoiceItem(id="u", text="Name")],
        )
        self.assertNotIn("choices", _definition(element))

    def test_as_dict_contract_unaffected_by_overlay(self) -> None:
        # The overlay must never reach the REST GUI payload
        element = _element("image_id", overlay=FieldMutability.context())
        self.assertEqual(set(element.as_dict()), {"name", "gui", "value"})


class GuiOverlayContractTest(unittest.TestCase):
    def test_field_mutability_is_a_gui_overlay(self) -> None:
        self.assertIsInstance(FieldMutability.hidden(), types_ui.GuiOverlay)

    def test_overlay_as_dict_is_json_shape(self) -> None:
        data = FieldMutability.context(agent_tooltip="ctx").as_dict()
        self.assertEqual(data, {"role": "context", "agent_tooltip": "ctx"})

    def test_builder_with_overlay_annotates_copy(self) -> None:
        # The public path annotates a copy (dataclasses.replace): the
        # element that was added stays untouched (it may be shared with
        # the static gui definitions), the built gui carries the overlay.
        original = _element("name")
        overlay = FieldMutability.context(agent_tooltip="ctx")
        built = ui_builder.GuiBuilder().add_fields([original]).with_overlay("name", overlay).build()
        self.assertIs(built[0].overlay, overlay)
        self.assertEqual(built[0].name, original.name)
        self.assertIs(built[0].gui, original.gui)
        self.assertIsNone(original.overlay)

"""Agent renderer of gui definitions (uds.mutability.gui_view).

Pure unit tests (no DB): the renderer is the only place where the gui
turns into the agent field surface, so its decision table (roles,
representable types, foreign overlays) is pinned here.
"""

import logging
import typing

import pytest

from uds.core.types import ui as types_ui
from uds.core.types.mutability import FieldMutability, FieldMutabilityRole
from uds.core.util import ui as ui_builder
from uds.mutability import gui_view


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


class TestOverlayValidation:
    def test_no_overlay_defaults_to_proposable(self) -> None:
        element = _element("name")
        assert gui_view.role_of(element) is FieldMutabilityRole.PROPOSABLE
        definition = gui_view.agent_definition(element)
        assert definition is not None
        assert definition["name"] == "name"

    def test_foreign_overlay_is_ignored(self) -> None:
        # The overlay slot is generic: anything that is not a FieldMutability
        # must behave exactly like no overlay at all (never crash).
        element = _element("name", overlay={"role": "hidden"})
        assert gui_view.mutability_of(element) is None
        assert gui_view.agent_definition(element) is not None

    def test_field_mutability_overlay_is_recognized(self) -> None:
        element = _element("name", overlay=FieldMutability.hidden())
        assert gui_view.mutability_of(element) is not None
        assert gui_view.role_of(element) is FieldMutabilityRole.HIDDEN


class TestRoles:
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
        assert gui_view.agent_definition(hidden) is None
        assert gui_view.agent_definition(relation) is None
        # ... but they still exist for consumers that read the gui
        assert gui_view.role_of(relation) is FieldMutabilityRole.RELATION

    def test_context_travels_in_fingerprint_but_not_in_definitions(self) -> None:
        context = _element(
            "image_id",
            field_type=types_ui.FieldType.IMAGECHOICE,
            overlay=FieldMutability.context(agent_tooltip="Icon reference"),
        )
        normal = _element("name")
        elements = [normal, context]
        assert [d["name"] for d in gui_view.agent_definitions(elements)] == ["name"]
        assert gui_view.fingerprint_names(elements) == ["name", "image_id"]

    def test_agent_tooltip_overrides_human_one(self) -> None:
        element = _element(
            "image_id",
            tooltip="Human tooltip",
            overlay=FieldMutability.context(agent_tooltip="Agent tooltip"),
        )
        # context role: not in the proposable definitions...
        assert gui_view.agent_definition(element) is None
        # ... but the override is what the renderer would emit if proposable
        proposable = _element(
            "name",
            tooltip="Human tooltip",
            overlay=FieldMutability.proposable(agent_tooltip="Agent tooltip"),
        )
        definition = gui_view.agent_definition(proposable)
        assert definition is not None
        assert definition["tooltip"] == "Agent tooltip"


class TestRepresentableTypes:
    def test_unrepresentable_type_warns_and_is_omitted(self, caplog: pytest.LogCaptureFixture) -> None:
        element = _element("created", field_type=types_ui.FieldType.DATE)
        with caplog.at_level(logging.WARNING, logger="uds.mutability.gui_view"):
            assert gui_view.agent_definition(element) is None
        assert any("created" in record.getMessage() for record in caplog.records)

    def test_representable_types_have_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="uds.mutability.gui_view"):
            for field_type in gui_view.REPRESENTABLE_FIELD_TYPES:
                assert gui_view.agent_definition(_element("field", field_type)) is not None
        assert not caplog.records


class TestDefinitions:
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
        definition = gui_view.agent_definition(element)
        assert definition is not None
        assert definition["label"] == "Short Name"
        assert definition["tooltip"] == "32 chars"
        assert definition["length"] == 32
        assert definition["required"] is True
        assert definition["secret"] is False
        assert "choices" not in definition

    def test_secret_types_are_flagged(self) -> None:
        password = _element("password", field_type=types_ui.FieldType.PASSWORD)
        hidden = _element("hidden_field", field_type=types_ui.FieldType.HIDDEN)
        assert gui_view.agent_definition(password)["secret"] is True  # type: ignore[index]
        assert gui_view.agent_definition(hidden)["secret"] is True  # type: ignore[index]

    def test_choices_render_as_value_label(self) -> None:
        choices = [
            types_ui.ChoiceItem(id=0, text="Zero"),
            types_ui.ChoiceItem(id=1, text="One"),
        ]
        element = _element("policy", field_type=types_ui.FieldType.CHOICE, choices=choices)
        definition = gui_view.agent_definition(element)
        assert definition is not None
        assert definition["choices"] == [
            {"value": 0, "label": "Zero"},
            {"value": 1, "label": "One"},
        ]

    def test_callable_choices_are_not_resolved(self) -> None:
        element = _element(
            "image_id",
            field_type=types_ui.FieldType.IMAGECHOICE,
            choices=lambda: [types_ui.ChoiceItem(id="u", text="Name")],
        )
        definition = gui_view.agent_definition(element)
        assert definition is not None
        assert "choices" not in definition

    def test_as_dict_contract_unaffected_by_overlay(self) -> None:
        # The overlay must never reach the REST GUI payload
        element = _element("image_id", overlay=FieldMutability.context())
        assert set(element.as_dict()) == {"name", "gui", "value"}


class TestGuiOverlayContract:
    def test_field_mutability_is_a_gui_overlay(self) -> None:
        assert isinstance(FieldMutability.hidden(), types_ui.GuiOverlay)

    def test_overlay_as_dict_is_json_shape(self) -> None:
        data = FieldMutability.context(agent_tooltip="ctx").as_dict()
        assert data == {"role": "context", "agent_tooltip": "ctx"}

    def test_builder_with_overlay_annotates_copy(self) -> None:
        # The public path annotates a copy (dataclasses.replace): the
        # element that was added stays untouched (it may be shared with
        # the static gui definitions), the built gui carries the overlay.
        original = _element("name")
        overlay = FieldMutability.context(agent_tooltip="ctx")
        built = ui_builder.GuiBuilder().add_fields([original]).with_overlay("name", overlay).build()
        assert built[0].overlay is overlay
        assert built[0].name == original.name
        assert built[0].gui is original.gui
        assert original.overlay is None

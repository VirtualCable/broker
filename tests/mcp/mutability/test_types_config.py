"""``config.update`` action type: dynamic allowlist, secrets blocked, CAS."""

import asyncio
from unittest import mock

from uds.core.exceptions import rest as rest_exceptions
from uds.core.util.config import Config as CfgConfig
from uds.REST.methods.config import Config as ConfigHandler
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.config import NOT_MUTABLE_TYPES, ConfigUpdate
from uds.models import Config as DBConfig

from tests.mcp.mutability._helpers import FlowTestCase, make_request

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


def _db_value(section: str, key: str, value: str, field_type: CfgConfig.FieldType) -> DBConfig:
    return DBConfig.objects.create(
        section=section, key=key, value=value, field_type=int(field_type), help=f"help for {key}"
    )


class ConfigUpdateRegistryTest(FlowTestCase):
    """config.update is registered and resolves its Section.key target."""

    def test_config_update_is_registered(self) -> None:
        found = registry_get("config.update")
        self.assertIs(found, ConfigUpdate)
        self.assertIn("config.update", [t.type_id for t in all_types()])

    def test_resolve_target_splits_section_and_key(self) -> None:
        cfg = _db_value("Security", "Admin Trusted Sources", "", CfgConfig.FieldType.TEXT)
        found = ConfigUpdate().resolve_target(f"Security.{cfg.key}")
        self.assertEqual(found.pk, cfg.pk)

    def test_resolve_target_allows_dots_in_key(self) -> None:
        cfg = _db_value("UDS", "some.key.with.dots", "x", CfgConfig.FieldType.TEXT)
        found = ConfigUpdate().resolve_target(f"UDS.{cfg.key}")
        self.assertEqual(found.pk, cfg.pk)

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            ConfigUpdate().resolve_target("Security.Not A Real Key")


class ConfigUpdateAccessTest(FlowTestCase):
    """Proposals are superuser-only (configuration has no permission model)."""

    def test_staff_is_denied(self) -> None:
        cfg = _db_value("Security", "Some Setting", "0", CfgConfig.FieldType.BOOLEAN)
        with self.assertRaises(rest_exceptions.AccessDenied):
            ConfigUpdate().check_propose_access(self.owner, cfg)

    def test_admin_is_allowed(self) -> None:
        cfg = _db_value("Security", "Some Setting", "0", CfgConfig.FieldType.BOOLEAN)
        self.other.is_admin = True
        ConfigUpdate().check_propose_access(self.other, cfg)


class ConfigUpdateFieldsTest(FlowTestCase):
    """Discovery lists every materialized value of the section minus secrets."""

    def test_field_definitions_exclude_secrets(self) -> None:
        _db_value("Security", "Visible Text", "hello", CfgConfig.FieldType.TEXT)
        _db_value("Security", "Visible Number", "42", CfgConfig.FieldType.NUMERIC)
        _db_value("Security", "Secret Password", "hashed", CfgConfig.FieldType.PASSWORD)
        _db_value("Security", "Secret Hidden", "hidden", CfgConfig.FieldType.HIDDEN)

        defs = ConfigUpdate().field_definitions("Security")
        names = [d["name"] for d in defs]
        self.assertIn("Security.Visible Text", names)
        self.assertIn("Security.Visible Number", names)
        self.assertNotIn("Security.Secret Password", names)
        self.assertNotIn("Security.Secret Hidden", names)

        by_name = {d["name"]: d for d in defs}
        self.assertEqual(by_name["Security.Visible Number"]["type"], "numeric")
        self.assertEqual(by_name["Security.Visible Number"]["current"], "42")

    def test_boolean_values_offer_choices(self) -> None:
        _db_value("Security", "Toggle", "0", CfgConfig.FieldType.BOOLEAN)
        by_name = {d["name"]: d for d in ConfigUpdate().field_definitions("Security")}
        self.assertEqual(by_name["Security.Toggle"]["choices"], ["true", "false"])

    def test_snapshot_fingerprint_tracks_the_value(self) -> None:
        cfg = _db_value("Security", "Some Setting", "0", CfgConfig.FieldType.BOOLEAN)
        action_type = ConfigUpdate()
        name = f"Security.{cfg.key}"

        self.assertEqual(action_type.snapshot_values(cfg, [name]), {name: "0"})
        self.assertEqual(action_type.snapshot_values(cfg, ["other.key"]), {})

        base = action_type.fingerprint(cfg)
        self.assertEqual(base, action_type.fingerprint(cfg))

        cfg.value = "1"
        cfg.save(update_fields=["value"])
        self.assertNotEqual(base, action_type.fingerprint(cfg))

    def test_secrets_are_never_mutable_by_type(self) -> None:
        self.assertIn(CfgConfig.FieldType.PASSWORD, NOT_MUTABLE_TYPES)
        self.assertIn(CfgConfig.FieldType.HIDDEN, NOT_MUTABLE_TYPES)


class ConfigUpdateExecuteTest(FlowTestCase):
    """Execution goes through the canonical REST config machinery."""

    def test_execute_sends_rest_put_shape(self) -> None:
        cfg = _db_value("Security", "Some Setting", "0", CfgConfig.FieldType.BOOLEAN)
        action = self._action(
            self._flow(),
            action_type="config.update",
            target_uuid=f"Security.{cfg.key}",
            values={f"Security.{cfg.key}": True},
            base_values={f"Security.{cfg.key}": "0"},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.config.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = asyncio.run(ConfigUpdate().execute(action, request=make_request()))

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, ConfigHandler)
        params = execute_call[0][2]
        # A Python bool is normalized exactly like Config.Value.set does
        self.assertEqual(params, {"Security": {cfg.key: {"value": "1"}}})


class ConfigUpdateProposalBaseTest(FlowTestCase):
    """The CAS base of a proposal is the current value of the key."""

    def test_snapshot_and_fingerprint_for_proposal(self) -> None:
        cfg = _db_value("Security", "Another Setting", "0", CfgConfig.FieldType.BOOLEAN)
        action_type = ConfigUpdate()
        values = {f"Security.{cfg.key}": "1"}
        base_values, base_etag = action_type.snapshot_and_fingerprint(cfg, values)
        self.assertEqual(base_values, {f"Security.{cfg.key}": "0"})
        self.assertTrue(base_etag)

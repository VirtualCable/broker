"""``metapool.update``: registration, discovery, CAS and execution shape."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.meta_pools import MetaPoolUpdate
from uds.REST.methods.meta_pools import MetaPools

from tests.fixtures.authenticators import create_db_authenticator, create_db_groups
from tests.fixtures.services import (
    create_db_metapool,
    create_db_osmanager,
    create_db_provider,
    create_db_service,
    create_db_servicepool,
)
from tests.mcp.mutability._helpers import FlowTestCase, make_request


class MetaPoolUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("metapool.update")
        self.assertIs(found, MetaPoolUpdate)
        self.assertIn("metapool.update", [t.type_id for t in all_types()])

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            MetaPoolUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class MetaPoolUpdateFieldsTest(FlowTestCase):
    def test_mutable_fields_carry_policy_choices(self) -> None:
        defs = MetaPoolUpdate().field_definitions("metapool")
        by_name = {d["name"]: d for d in defs}

        for name in (
            "name",
            "short_name",
            "comments",
            "tags",
            "visible",
            "policy",
            "ha_policy",
            "transport_grouping",
            "calendar_message",
        ):
            self.assertIn(name, by_name)
        # References and relations are not proposable
        for name in ("image_id", "servicesPoolGroup_id", "members"):
            self.assertNotIn(name, by_name)

        self.assertEqual(by_name["policy"]["choices"], [0, 1, 2])
        self.assertEqual(by_name["ha_policy"]["choices"], [0, 1])
        self.assertEqual(by_name["transport_grouping"]["choices"], [0, 1, 2])

        errors = MetaPoolUpdate().validate_values("metapool", {"visible": "yes"})
        self.assertTrue(any("must be a boolean" in e for e in errors))
        errors = MetaPoolUpdate().validate_values("metapool", {"members": []})
        self.assertTrue(any("unknown fields" in e for e in errors))

    def test_snapshot_covers_columns_and_context(self) -> None:
        authenticator = create_db_authenticator()
        _groups = create_db_groups(authenticator, 1)
        service = create_db_service(create_db_provider())
        pool = create_db_servicepool(service=service, osmanager=create_db_osmanager())
        meta_pool = create_db_metapool([pool], _groups)
        action_type = MetaPoolUpdate()

        snapshot = action_type.snapshot_values(meta_pool, action_type.etag_fields("metapool"))
        self.assertEqual(snapshot["image_id"], "-1")
        self.assertEqual(snapshot["servicesPoolGroup_id"], "-1")
        self.assertEqual(snapshot["policy"], meta_pool.policy)
        self.assertEqual(snapshot["ha_policy"], meta_pool.ha_policy)
        self.assertEqual(snapshot["transport_grouping"], meta_pool.transport_grouping)

        base = action_type.fingerprint(meta_pool)
        self.assertEqual(base, action_type.fingerprint(meta_pool))
        meta_pool.policy = 1
        meta_pool.save(update_fields=["policy"])
        self.assertNotEqual(base, action_type.fingerprint(meta_pool))


class MetaPoolUpdateExecuteTest(FlowTestCase):
    def test_execute_sends_whole_form_with_proposed_values(self) -> None:
        authenticator = create_db_authenticator()
        _groups = create_db_groups(authenticator, 1)
        service = create_db_service(create_db_provider())
        pool = create_db_servicepool(service=service, osmanager=create_db_osmanager())
        meta_pool = create_db_metapool([pool], _groups)
        action = self._action(
            self._flow(),
            action_type="metapool.update",
            target_uuid=meta_pool.uuid,
            values={"policy": 1},
            base_values={"policy": meta_pool.policy},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.meta_pools.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(MetaPoolUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, MetaPools)
        params: dict[str, typing.Any] = execute_call[0][2]
        # The PUT is form-shaped: every required field travels, proposed
        # values win, context keeps stored values
        for name in (
            "name",
            "short_name",
            "comments",
            "tags",
            "image_id",
            "servicesPoolGroup_id",
            "visible",
            "policy",
            "ha_policy",
            "calendar_message",
            "transport_grouping",
        ):
            self.assertIn(name, params)
        self.assertEqual(params["policy"], 1)
        self.assertEqual(params["image_id"], "-1")
        self.assertEqual(params["servicesPoolGroup_id"], "-1")

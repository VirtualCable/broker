"""``servicepool.update``: registration, discovery, CAS and execution shape."""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core.exceptions import rest as rest_exceptions
from uds.mutability import all_types, get as registry_get
from uds.mutability.types.service_pools import ServicePoolUpdate
from uds.REST.methods.services_pools import ServicesPools

from tests.fixtures.services import (
    create_db_osmanager,
    create_db_provider,
    create_db_service,
    create_db_servicepool,
)
from tests.mcp.mutability._helpers import FlowTestCase, make_request

if typing.TYPE_CHECKING:
    from uds import models


def _create_pool() -> tuple["models.ServicePool", "models.Service", "models.OSManager"]:
    service = create_db_service(create_db_provider())
    osmanager = create_db_osmanager()
    return create_db_servicepool(service=service, osmanager=osmanager), service, osmanager


class ServicePoolUpdateRegistryTest(FlowTestCase):
    def test_is_registered(self) -> None:
        found = registry_get("servicepool.update")
        self.assertIs(found, ServicePoolUpdate)
        self.assertIn("servicepool.update", [t.type_id for t in all_types()])

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            ServicePoolUpdate().resolve_target("00000000-0000-0000-0000-000000000000")


class ServicePoolUpdateFieldsTest(FlowTestCase):
    def test_mutable_fields_and_excluded_references(self) -> None:
        defs = ServicePoolUpdate().field_definitions("servicepool")
        names = [d["name"] for d in defs]

        for name in (
            "name",
            "short_name",
            "comments",
            "tags",
            "initial_srvs",
            "cache_l1_srvs",
            "cache_l2_srvs",
            "max_srvs",
            "show_transports",
            "visible",
            "allow_users_remove",
            "allow_users_reset",
            "ignores_unused",
            "calendar_message",
            "custom_message",
            "display_custom_message",
        ):
            self.assertIn(name, names)

        # Identity/reference columns are not proposable
        for name in (
            "service_id",
            "osmanager_id",
            "image_id",
            "pool_group_id",
            "account_id",
            "publish_on_save",
        ):
            self.assertNotIn(name, names)
        errors = ServicePoolUpdate().validate_values("servicepool", {"service_id": "other"})
        self.assertTrue(any("unknown fields" in e for e in errors))
        errors = ServicePoolUpdate().validate_values("servicepool", {"cache_l1_srvs": "soon"})
        self.assertTrue(any("must be numeric" in e for e in errors))

    def test_snapshot_covers_columns_and_context(self) -> None:
        pool, service, osmanager = _create_pool()
        action_type = ServicePoolUpdate()

        snapshot = action_type.snapshot_values(pool, action_type.etag_fields("servicepool"))
        # Context uuids, as the PUT expects them
        self.assertEqual(snapshot["service_id"], service.uuid)
        self.assertEqual(snapshot["osmanager_id"], osmanager.uuid)
        self.assertEqual(snapshot["image_id"], "-1")
        self.assertEqual(snapshot["pool_group_id"], "-1")
        self.assertEqual(snapshot["account_id"], "")
        # Columns
        self.assertEqual(snapshot["cache_l1_srvs"], pool.cache_l1_srvs)
        self.assertEqual(snapshot["allow_users_remove"], pool.allow_users_remove)

        base = action_type.fingerprint(pool)
        self.assertEqual(base, action_type.fingerprint(pool))
        pool.comments = "changed"
        pool.save(update_fields=["comments"])
        self.assertNotEqual(base, action_type.fingerprint(pool))


class ServicePoolUpdateExecuteTest(FlowTestCase):
    def test_execute_sends_whole_form_with_proposed_values(self) -> None:
        pool, _service, _osmanager = _create_pool()
        action = self._action(
            self._flow(),
            action_type="servicepool.update",
            target_uuid=pool.uuid,
            values={"cache_l1_srvs": 7},
            base_values={"cache_l1_srvs": pool.cache_l1_srvs},
            base_etag="etag",
        )

        with mock.patch("uds.mutability.types.service_pools.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            summary = async_to_sync(ServicePoolUpdate().execute)(action, request=make_request())

        self.assertIn("updated", summary)
        execute_call = proxy_cls.return_value.execute.call_args
        self.assertIs(execute_call[0][0].handler, ServicesPools)
        params: dict[str, typing.Any] = execute_call[0][2]
        # The PUT is form-shaped: every required field travels, proposed
        # values win, context keeps stored values
        for name in (
            "name",
            "short_name",
            "comments",
            "tags",
            "service_id",
            "osmanager_id",
            "image_id",
            "pool_group_id",
            "account_id",
            "initial_srvs",
            "cache_l1_srvs",
            "cache_l2_srvs",
            "max_srvs",
            "show_transports",
            "visible",
            "allow_users_remove",
            "allow_users_reset",
            "ignores_unused",
            "calendar_message",
            "custom_message",
            "display_custom_message",
        ):
            self.assertIn(name, params)
        self.assertEqual(params["cache_l1_srvs"], 7)
        self.assertEqual(params["service_id"], pool.service.uuid)
        osmanager = typing.cast("models.OSManager", pool.osmanager)
        self.assertEqual(params["osmanager_id"], osmanager.uuid)

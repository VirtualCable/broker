"""``servicepool.publication.add`` / ``.delete``: publishing surface.

Publications are operations, not fields: ``add`` initiates one (optional
changelog message) through the ``publish`` custom method, ``delete``
cancels a running one by uuid through the item-scoped ``cancel`` custom
method. ``read`` mirrors the two read-only collections (publications and
changelog, joined by revision). Both write operations run FORCE (the
publication advances asynchronously; REST holds the authoritative
checks at execution time).
"""

import typing
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.util.model import sql_now
from uds.mutability import all_type_ids, get as registry_get
from uds.mutability.base import ActionOperation, MutableActionType, StalePolicy
from uds.mutability.types.service_pools.publication import ServicePoolPublications
from uds.REST.methods.services_pools import ServicesPools
from uds.REST.methods.user_services import Publications

from tests.mcp.mutability._helpers import FlowTestCase, make_request

State = types.states.State


def _pool() -> models.ServicePool:
    from tests.fixtures.services import (
        create_db_osmanager,
        create_db_provider,
        create_db_service,
        create_db_servicepool,
    )

    service = create_db_service(create_db_provider())
    return create_db_servicepool(service=service, osmanager=create_db_osmanager())


def _publication(
    pool: models.ServicePool,
    state: str = State.LAUNCHING,
    revision: int | None = None,
) -> models.ServicePoolPublication:
    now = sql_now()
    return models.ServicePoolPublication.objects.create(
        deployed_service=pool,
        publish_date=now,
        state_date=now,
        state=state,
        revision=pool.current_pub_revision if revision is None else revision,
    )


def _action_type(operation: str) -> MutableActionType:
    factory = registry_get(f"servicepool.publication.{operation}")
    assert factory is not None
    return factory()


class ServicePoolPublicationsRegistryTest(FlowTestCase):
    def test_both_operations_are_registered(self) -> None:
        self.assertIs(type(_action_type("add")), ServicePoolPublications)
        self.assertIs(type(_action_type("delete")), ServicePoolPublications)
        self.assertIn("servicepool.publication.add", all_type_ids())
        self.assertIn("servicepool.publication.delete", all_type_ids())
        self.assertEqual(
            ServicePoolPublications.supported_operations(),
            frozenset({ActionOperation.ADD, ActionOperation.DELETE}),
        )

    def test_read_override_publishes_a_read(self) -> None:
        self.assertTrue(ServicePoolPublications.readable())

    def test_stale_policy_is_force_for_both_operations(self) -> None:
        # A publication advances asynchronously; pending proposals must
        # survive the state machine running between propose and approve.
        self.assertIs(_action_type("add").get_stale_policy(), StalePolicy.FORCE)
        self.assertIs(_action_type("delete").get_stale_policy(), StalePolicy.FORCE)

    def test_is_target_scoped(self) -> None:
        self.assertIs(ServicePoolPublications.target_scoped_fields, True)

    def test_resolve_unknown_target_is_not_found(self) -> None:
        with self.assertRaises(rest_exceptions.NotFound):
            _action_type("add").resolve_target("00000000-0000-0000-0000-000000000000")

    def test_read_view_publishes_publications_and_changelog(self) -> None:
        pool = _pool()
        running = _publication(pool, State.LAUNCHING)
        _publication(pool, State.USABLE, revision=1)
        pool.changelog.create(revision=2, stamp=sql_now(), log="first note")
        view = ServicePoolPublications().read(pool.uuid)
        self.assertEqual(view["entity_type"], "servicepool.publication")
        self.assertEqual(view["target_uuid"], pool.uuid)
        self.assertEqual(view["current_revision"], pool.current_pub_revision)
        by_uuid = {row["uuid"]: row for row in view["publications"]}
        self.assertEqual(by_uuid[running.uuid]["state"], State.LAUNCHING)
        self.assertEqual(by_uuid[running.uuid]["revision"], pool.current_pub_revision)
        self.assertEqual(by_uuid[running.uuid]["state_label"], str(State.LAUNCHING.localized))
        self.assertEqual(
            [(entry["revision"], entry["log"]) for entry in view["changelog"]],
            [(2, "first note")],
        )


class ServicePoolPublicationsFieldsTest(FlowTestCase):
    def test_add_publishes_only_the_changelog_field(self) -> None:
        defs = _action_type("add").field_definitions("servicepool")
        self.assertEqual([d["name"] for d in defs], ["changelog"])
        self.assertEqual(defs[0]["type"], types.ui.FieldType.TEXT.value)

    def test_delete_publishes_only_the_publication_field(self) -> None:
        pool = _pool()
        running = _publication(pool, State.PREPARING)
        defs = _action_type("delete").field_definitions("servicepool", pool)
        self.assertEqual([d["name"] for d in defs], ["publication"])
        self.assertIn(running.uuid, defs[0]["tooltip"])
        self.assertIn(f"revision {running.revision}", defs[0]["tooltip"])

    def test_delete_tooltip_reports_nothing_cancelable(self) -> None:
        pool = _pool()
        defs = _action_type("delete").field_definitions("servicepool", pool)
        self.assertIn("no cancelable publication", defs[0]["tooltip"])


class ServicePoolPublicationsAddValidationTest(FlowTestCase):
    def test_publish_is_valid_with_or_without_changelog(self) -> None:
        pool = _pool()
        action_type = _action_type("add")
        self.assertEqual(action_type.validate_values("servicepool", {}, pool), [])
        self.assertEqual(action_type.validate_values("servicepool", {"changelog": "why"}, pool), [])

    def test_changelog_must_be_a_string(self) -> None:
        pool = _pool()
        errors = _action_type("add").validate_values("servicepool", {"changelog": 5}, pool)
        self.assertTrue(any("changelog must be a string" in e for e in errors))

    def test_running_publication_blocks_a_new_one_early(self) -> None:
        pool = _pool()
        _publication(pool, State.LAUNCHING)
        errors = _action_type("add").validate_values("servicepool", {}, pool)
        self.assertTrue(any("already publishing" in e for e in errors))

    def test_maintenance_blocks_publishing_early(self) -> None:
        pool = _pool()
        pool.service.provider.maintenance_mode = True
        pool.service.provider.save(update_fields=["maintenance_mode"])
        errors = _action_type("add").validate_values("servicepool", {}, pool)
        self.assertTrue(any("maintenance" in e for e in errors))

    def test_unknown_fields_rejected_per_operation(self) -> None:
        errors = _action_type("add").validate_values("servicepool", {"publication": "x"})
        self.assertTrue(any("unknown fields: publication" in e for e in errors))
        errors = _action_type("delete").validate_values("servicepool", {"changelog": "x"})
        self.assertTrue(any("unknown fields: changelog" in e for e in errors))


class ServicePoolPublicationsDeleteValidationTest(FlowTestCase):
    def test_publication_field_is_required(self) -> None:
        pool = _pool()
        errors = _action_type("delete").validate_values("servicepool", {}, pool)
        self.assertTrue(any("publication is required" in e for e in errors))

    def test_must_be_a_publication_of_this_pool(self) -> None:
        pool = _pool()
        other = _publication(_pool(), State.LAUNCHING)
        errors = _action_type("delete").validate_values("servicepool", {"publication": other.uuid}, pool)
        self.assertTrue(any("is not a publication of this service pool" in e for e in errors))

    def test_only_running_publications_are_cancelable(self) -> None:
        pool = _pool()
        usable = _publication(pool, State.USABLE)
        errors = _action_type("delete").validate_values("servicepool", {"publication": usable.uuid}, pool)
        self.assertTrue(any("is not running" in e for e in errors))
        for state in (State.LAUNCHING, State.PREPARING, State.CANCELING):
            running = _publication(pool, state)
            self.assertEqual(
                _action_type("delete").validate_values("servicepool", {"publication": running.uuid}, pool),
                [],
            )

    def test_malformed_uuid_rejected(self) -> None:
        pool = _pool()
        errors = _action_type("delete").validate_values("servicepool", {"publication": "not-a-uuid"}, pool)
        self.assertTrue(any("not a valid uuid" in e for e in errors))
        errors = _action_type("delete").validate_values("servicepool", {"publication": 7}, pool)
        self.assertTrue(any("must be the uuid string" in e for e in errors))


class ServicePoolPublicationsFingerprintTest(FlowTestCase):
    def test_fingerprint_tracks_the_next_revision_and_running_pubs(self) -> None:
        pool = _pool()
        action_type = _action_type("add")
        self.assertEqual(
            action_type.snapshot_values(pool, ["revision", "in_progress"]),
            {"revision": pool.current_pub_revision, "in_progress": []},
        )
        base = action_type.fingerprint(pool)
        self.assertEqual(base, action_type.fingerprint(pool))

        running = _publication(pool, State.LAUNCHING)
        self.assertNotEqual(base, action_type.fingerprint(pool))
        # Once it leaves the interesting states (canceled), the item is back
        running.state = State.CANCELED
        running.save(update_fields=["state"])
        self.assertEqual(base, action_type.fingerprint(pool))
        # A usable publication is an interesting change (it is the serving one)
        _publication(pool, State.USABLE)
        self.assertNotEqual(base, action_type.fingerprint(pool))


class ServicePoolPublicationsExecuteTest(FlowTestCase):
    def _run(self, operation: str, pool: models.ServicePool, values: dict[str, typing.Any]) -> mock.Mock:
        action = self._action(
            self._flow(),
            action_type=f"servicepool.publication.{operation}",
            target_uuid=pool.uuid,
            values=values,
            base_values={},
            base_etag="etag",
        )
        proxy_cls = mock.MagicMock()
        proxy_cls.return_value.execute = mock.AsyncMock(return_value=None)
        with mock.patch("uds.mutability.types.service_pools.publication.RestProxy", proxy_cls):
            summary = async_to_sync(_action_type(operation).execute)(action, request=make_request())
        self._last_summary = summary
        return proxy_cls

    def test_publish_posts_the_collection_custom_method(self) -> None:
        pool = _pool()
        proxy_cls = self._run("add", pool, {"changelog": "first"})
        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, Publications)
        self.assertEqual(target.path, "services_pools/{uuid}/publications")
        self.assertEqual(target.method, types.rest.CustomMethodMethod.POST)
        self.assertEqual(target.args, ("publish",))
        self.assertIs(target.parent.handler, ServicesPools)
        self.assertEqual(call[0][2], {"changelog": "first"})
        self.assertEqual(call[1]["parent_uuid"], pool.uuid)
        self.assertIn("publication initiated", self._last_summary)

    def test_publish_without_changelog_sends_no_params(self) -> None:
        proxy_cls = self._run("add", _pool(), {})
        call = proxy_cls.return_value.execute.await_args_list[0]
        self.assertEqual(call[0][2], {})

    def test_publish_blocked_by_a_running_publication(self) -> None:
        pool = _pool()
        _publication(pool, State.LAUNCHING)
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("add", pool, {})

    def test_publish_blocked_in_maintenance(self) -> None:
        pool = _pool()
        pool.service.provider.maintenance_mode = True
        pool.service.provider.save(update_fields=["maintenance_mode"])
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("add", pool, {})

    def test_cancel_posts_the_item_custom_method(self) -> None:
        pool = _pool()
        running = _publication(pool, State.PREPARING)
        proxy_cls = self._run("delete", pool, {"publication": running.uuid})
        call = proxy_cls.return_value.execute.await_args_list[0]
        target = call[0][0]
        self.assertIs(target.handler, Publications)
        self.assertEqual(target.args, (running.uuid, "cancel"))
        self.assertEqual(call[0][2], {})
        self.assertIn(f"revision {running.revision} canceling", self._last_summary)

    def test_cancel_aborts_when_the_publication_vanished(self) -> None:
        pool = _pool()
        running = _publication(pool, State.LAUNCHING)
        uuid_value = running.uuid
        running.delete()
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("delete", pool, {"publication": uuid_value})

    def test_cancel_aborts_when_it_stopped_running(self) -> None:
        pool = _pool()
        running = _publication(pool, State.LAUNCHING)
        running.state = State.USABLE
        running.save(update_fields=["state"])
        with self.assertRaises(rest_exceptions.RequestError):
            self._run("delete", pool, {"publication": running.uuid})

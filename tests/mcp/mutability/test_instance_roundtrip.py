"""Instance-values round-trip for the module-backed mutability types.

Closes the loop on the ``from_instance`` surface with concrete values
stored in the DB row: concrete values serialized on creation ->
``snapshot_values`` returns exactly those -> ``validate_values`` accepts
them (and rejects bad types) -> ``execute`` merges the *proposed* value
for touched fields while untouched instance fields keep their stored
values on the REST PUT payload.
"""

import typing
from unittest import mock

from asgiref.sync import async_to_sync
from django.db import models as db_models

from uds import models
from uds.core import environment
from uds.mutability.base import JsonObject, MutableActionType
from uds.mutability.types.authenticators import AuthenticatorUpdate
from uds.mutability.types.notifiers import NotifierUpdate
from uds.mutability.types.osmanagers import OsManagerUpdate
from uds.mutability.types.transports import TransportUpdate

from tests.fixtures.notifiers import createEmailNotifier
from tests.fixtures.services import ensure_test_modules_registered
from tests.mcp.mutability._helpers import FlowTestCase, make_request


def _db_osmanager_with(values: "JsonObject") -> models.OSManager:
    from tests.fixtures.modules.osmanager import TestOSManager

    ensure_test_modules_registered()
    instance = TestOSManager(environment.Environment.testing_environment(), values)
    return models.OSManager.objects.create(
        name="Roundtrip osmanager",
        comments="",
        data_type=TestOSManager.type_type,
        data=instance.serialize(),
    )


def _db_transport_with(values: "JsonObject") -> models.Transport:
    from tests.fixtures.modules.transport import TestTransport

    ensure_test_modules_registered()
    instance = TestTransport(environment.Environment.testing_environment(), values)
    return models.Transport.objects.create(
        name="Roundtrip transport",
        comments="",
        data_type=TestTransport.type_type,
        data=instance.serialize(),
    )


def _db_authenticator_with(values: "JsonObject") -> models.Authenticator:
    from uds.auths.InternalDB.authenticator import InternalDBAuth

    instance = InternalDBAuth(environment.Environment.testing_environment(), values)
    return models.Authenticator.objects.create(
        name="Roundtrip authenticator",
        data_type=InternalDBAuth.type_type,
        data=instance.serialize(),
    )


class InstanceRoundtripTest(FlowTestCase):
    """Shared helpers; concrete cases below fix the values per type."""

    def _assert_roundtrip(
        self,
        action_type: MutableActionType,
        type_id: str,
        for_type: str,
        target: db_models.Model,
        stored_instance_values: "JsonObject",
        invalid_values: "JsonObject",
        proposed_value: tuple[str, typing.Any],
        secret_name: str | None = None,
    ) -> None:
        # Discovery: every stored instance field shows up as mutable
        names = {d["name"] for d in action_type.field_definitions(for_type)}
        for name in stored_instance_values:
            self.assertIn(name, names)

        # CAS snapshot: exact stored values, not defaults
        fields = action_type.etag_fields(for_type)
        snapshot = action_type.snapshot_values(target, fields)
        for name, expected in stored_instance_values.items():
            self.assertEqual(snapshot[name], expected)

        # Validation: stored values are valid, bad types and unknown
        # fields are rejected
        self.assertEqual(action_type.validate_values(for_type, dict(stored_instance_values)), [])
        errors = action_type.validate_values(for_type, invalid_values)
        self.assertTrue(any("must be" in e for e in errors))
        errors = action_type.validate_values(for_type, {"nope": 1})
        self.assertTrue(any("unknown fields" in e for e in errors))

        # Secrets are flagged as such
        secrets = action_type.secret_names(for_type)
        if secret_name is None:
            self.assertEqual(secrets, set())
        else:
            self.assertIn(secret_name, secrets)

        # Execution: the proposed instance value replaces the stored one,
        # the untouched instance fields keep their stored values
        field, value = proposed_value
        action = self._action(
            self._flow(),
            action_type=type_id,
            target_uuid=action_type.target_uuid_of(target),
            values={field: value},
            base_values={field: snapshot[field]},
            base_etag="etag",
        )
        with mock.patch("uds.mutability.types._module_update.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="done")
            async_to_sync(action_type.execute)(action, request=make_request())
        params = proxy_cls.return_value.execute.call_args[0][2]
        self.assertEqual(params[field], value)
        for name, expected in stored_instance_values.items():
            if name != field:
                self.assertEqual(params[name], expected)


class TransportInstanceRoundtripTest(InstanceRoundtripTest):
    def test_roundtrip(self) -> None:
        stored = {
            "test_url": "https://roundtrip.example",
            "pin": "1234",
            "force_new_window": True,
        }
        transport = _db_transport_with(stored)
        self._assert_roundtrip(
            TransportUpdate(),
            "transport.update",
            "TestTransport",
            transport,
            stored,
            invalid_values={"force_new_window": "maybe"},
            proposed_value=("test_url", "https://changed.example"),
            secret_name="pin",
        )


class OsManagerInstanceRoundtripTest(InstanceRoundtripTest):
    def test_roundtrip(self) -> None:
        # on_logout is readonly in the module gui: not part of the surface
        stored = {"idle": 4242}
        osmanager = _db_osmanager_with({"on_logout": "keep", "idle": 4242})
        self._assert_roundtrip(
            OsManagerUpdate(),
            "osmanager.update",
            "TestOsManager",
            osmanager,
            stored,
            invalid_values={"idle": "soon"},
            proposed_value=("idle", 9999),
        )


class NotifierInstanceRoundtripTest(InstanceRoundtripTest):
    def test_roundtrip(self) -> None:
        notifier = createEmailNotifier(host="roundtrip.local")
        stored = {
            "hostname": "roundtrip.local",
            "username": "",
            "password": "",
            "from_email": "from@email.com",
            "enable_html": False,
        }
        self._assert_roundtrip(
            NotifierUpdate(),
            "notifier.update",
            "emailNotifications",
            notifier,
            stored,
            invalid_values={"enable_html": "yes"},
            proposed_value=("hostname", "changed.local"),
            secret_name="password",
        )


class AuthenticatorInstanceRoundtripTest(InstanceRoundtripTest):
    def test_roundtrip(self) -> None:
        # unique_by_host/reverse_dns are readonly in the InternalDB gui;
        # accepts_proxy is the remaining mutable instance field
        stored = {"accepts_proxy": True}
        authenticator = _db_authenticator_with(
            {"unique_by_host": True, "reverse_dns": False, "accepts_proxy": True}
        )
        self._assert_roundtrip(
            AuthenticatorUpdate(),
            "authenticator.update",
            "InternalDBAuth",
            authenticator,
            stored,
            invalid_values={"accepts_proxy": "nope"},
            proposed_value=("accepts_proxy", False),
        )

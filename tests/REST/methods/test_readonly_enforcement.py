"""
Read-only gui fields are enforced on update (not just promised to the UI).

PUT with a readonly parameter carrying a DIFFERENT value than the stored
one must be rejected; PUT with the stored value (or without the field)
keeps working. Uses ``TestOSManager`` whose ``on_logout`` gui field is
``readonly=True`` (module instance field, applied via ``get_instance``)
to exercise the module-backed path.

Reference: src/uds/REST/model/master/__init__.py (ModelHandler.put)
"""

import typing

from ...fixtures.services import create_db_osmanager
from ...utils import rest


class ReadonlyEnforcementTest(rest.test.RESTTestCase):
    """Readonly gui fields cannot be changed through PUT."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        self.osmanager = create_db_osmanager()
        # TestOSManager factory values: on_logout="remove" (readonly), idle=300

    def _put(self, payload: dict[str, typing.Any]) -> typing.Any:
        return self.client.rest_put(f"osmanagers/{self.osmanager.uuid}", data=payload)

    def base_payload(self) -> dict[str, typing.Any]:
        return {
            "name": self.osmanager.name,
            "comments": self.osmanager.comments,
            "data_type": self.osmanager.data_type,
            "tags": [],
            "idle": 300,
        }

    def test_put_changing_readonly_field_is_rejected(self) -> None:
        payload = self.base_payload() | {"on_logout": "keep"}
        response = self._put(payload)
        self.assertEqual(response.status_code, 400, response.content)
        # Stored value untouched
        self.osmanager.refresh_from_db()
        instance = typing.cast(typing.Any, self.osmanager.get_instance(None))
        self.assertEqual(instance.on_logout.value, "remove")

    def test_put_sending_stored_readonly_value_succeeds(self) -> None:
        payload = self.base_payload() | {"on_logout": "remove", "comments": "updated"}
        response = self._put(payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.osmanager.refresh_from_db()
        self.assertEqual(self.osmanager.comments, "updated")

    def test_put_omitting_readonly_field_succeeds(self) -> None:
        payload = self.base_payload() | {"comments": "updated again"}
        response = self._put(payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.osmanager.refresh_from_db()
        instance = typing.cast(typing.Any, self.osmanager.get_instance(None))
        self.assertEqual(instance.on_logout.value, "remove")
        self.assertEqual(instance.idle.value, 300)

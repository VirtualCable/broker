#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
"""
Tests for the owner surface of proposal flows (``/flows/own``).

Covers draft creation, listing (full own history), single lookup (any
own status, others are 404), submit (draft -> pending with the declared
resolution window), cancel (draft or pending only), refusals of the
generic edit paths, and the actions detail (create/edit through the
registry with MANAGEMENT over the target, CAS base and caps, drafts
only).
"""

from __future__ import annotations

import datetime
import typing

from uds import models
from uds.core.consts import mcp as consts_mcp
from uds.core.types import permissions as permissions_types
from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.core.util import objtype
from uds.core.util.model import sql_now
from uds.mutability.store import FlowStore

from tests.fixtures.services import create_db_provider
from tests.utils import rest

VALID_VALUES: typing.Final[dict[str, typing.Any]] = {"name": "renamed by owner"}


def _grant_management(user: models.User, provider: models.Provider) -> None:
    """Grant MANAGEMENT over one provider to a user."""
    models.Permissions.objects.create(
        user=user,
        object_type=objtype.ObjectType.PROVIDER.type,
        object_id=provider.pk,
        permission=int(permissions_types.PermissionType.MANAGEMENT),
        created=datetime.datetime.now(datetime.timezone.utc),
    )


class FlowsOwnAccessTest(rest.test.RESTTestCase):
    """Access rules of the owner surface."""

    def test_staff_creates_own_flow(self) -> None:
        self.login(user=self.staffs[0])
        response = self.client.rest_post("flows/own", data={"name": "my proposal", "justification": "because"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], FlowStatus.DRAFT)
        flow = models.ActionFlow.objects.get(uuid=body["id"])
        self.assertEqual(flow.owner, self.staffs[0])
        self.assertEqual(flow.name, "my proposal")

    def test_plain_user_is_refused(self) -> None:
        self.login(user=self.plain_users[0])
        response = self.client.rest_post("flows/own", data={"name": "nope"})
        self.assertEqual(response.status_code, 403, response.content)

    def test_list_shows_only_own_pending(self) -> None:
        self.login(user=self.staffs[0])
        own = [self.client.rest_post("flows/own", data={"name": f"own {i}"}).json()["id"] for i in range(2)]
        # Another user's flow (even pending) must not show up
        other = FlowStore().create_flow(owner=self.admins[1], name="other user flow")
        response = self.client.rest_get("flows/own")
        self.assertEqual(response.status_code, 200, response.content)
        listed = {item["id"] for item in response.json()}
        self.assertEqual(listed, set(own))
        self.assertNotIn(other.uuid, listed)

    def test_get_own_flow_any_status(self) -> None:
        self.login(user=self.staffs[0])
        flow_id = self.client.rest_post("flows/own", data={"name": "inspect me"}).json()["id"]
        self.assertEqual(self.client.rest_get(f"flows/own/{flow_id}").status_code, 200)
        # Once approved (by an admin), the owner can still inspect it
        flow = models.ActionFlow.objects.get(uuid=flow_id)
        action = FlowStore().add_action(
            flow,
            action_type="provider.update",
            target_uuid="any",
            values={"name": "x"},
            base_values={"name": "y"},
            base_etag="e",
        )
        FlowStore().submit_flow(flow, actor_uuid=self.staffs[0].uuid)
        action.status = FlowActionStatus.APPROVED
        action.approved_etag = "frozen"
        action.save(update_fields=["status"])
        FlowStore().approve_flow(flow, admin=self.admins[1])
        response = self.client.rest_get(f"flows/own/{flow_id}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], FlowStatus.APPROVED)

    def test_other_users_flow_is_404(self) -> None:
        self.login(user=self.staffs[0])
        other = FlowStore().create_flow(owner=self.admins[1], name="secret")
        self.assertEqual(self.client.rest_get(f"flows/own/{other.uuid}").status_code, 404)

    def test_list_shows_full_own_history(self) -> None:
        self.login(user=self.staffs[0])
        cancelled_id = self.client.rest_post("flows/own", data={"name": "to cancel"}).json()["id"]
        kept_id = self.client.rest_post("flows/own", data={"name": "kept"}).json()["id"]
        self.client.rest_delete(f"flows/own/{cancelled_id}")
        listed = {i["id"]: i["status"] for i in self.client.rest_get("flows/own").json()}
        # Full history: decided flows remain listed with their outcome
        self.assertEqual(set(listed), {cancelled_id, kept_id})
        self.assertEqual(listed[cancelled_id], FlowStatus.CANCELLED)
        self.assertEqual(listed[kept_id], FlowStatus.DRAFT)

    def test_draft_is_hidden_from_the_administration_surface(self) -> None:
        """Drafts are the owner's composing space: they do not exist for admins."""
        self.login(user=self.staffs[0])
        draft_id = self.client.rest_post("flows/own", data={"name": "private draft"}).json()["id"]
        store = FlowStore()
        store.add_action(
            models.ActionFlow.objects.get(uuid=draft_id),
            action_type="provider.update",
            target_uuid="any",
            values={"name": "x"},
            base_values={"name": "y"},
            base_etag="e",
        )
        self.login(user=self.admins[0])
        items: list[dict[str, typing.Any]] = self.client.rest_get("flows/management/overview").json()
        self.assertNotIn(draft_id, {i["id"] for i in items})
        # Not even reachable by direct uuid lookup
        self.assertEqual(self.client.rest_get(f"flows/management/{draft_id}").status_code, 404)
        self.assertEqual(self.client.rest_get(f"flows/management/{draft_id}/actions").status_code, 404)
        # Submitting makes it visible to the administrator
        self.login(user=self.staffs[0])
        self.assertEqual(self.client.rest_post(f"flows/own/{draft_id}/submit", data={}).status_code, 200)
        self.login(user=self.admins[0])
        items = self.client.rest_get("flows/management/overview").json()
        self.assertIn(draft_id, {i["id"] for i in items})


class FlowsOwnTtlTest(rest.test.RESTTestCase):
    """Resolution window declared at submit; expiry is terminal."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login(user=self.staffs[0])

    def _draft(self, **extra: typing.Any) -> typing.Any:
        response = self.client.rest_post("flows/own", data={"name": "ttl", **extra})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def _submit(self, flow_uuid: str, **extra: typing.Any) -> typing.Any:
        response = self.client.rest_post(f"flows/own/{flow_uuid}/submit", data={**extra})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_draft_creation_ignores_the_resolution_window(self) -> None:
        body = self._draft(expires_in_hours=48)
        flow = models.ActionFlow.objects.get(uuid=body["id"])
        assert flow.due_date is not None
        remaining = flow.due_date - sql_now()
        self.assertLess(remaining, datetime.timedelta(days=consts_mcp.DRAFT_TTL_DAYS + 1))
        self.assertGreater(remaining, datetime.timedelta(days=consts_mcp.DRAFT_TTL_DAYS - 1))

    def test_submit_honours_expires_in_hours(self) -> None:
        _grant_management(self.staffs[0], self.provider)
        flow_uuid = self._draft()["id"]
        self.client.rest_post(
            f"flows/own/{flow_uuid}/actions",
            data={
                "action_type": "provider.update",
                "target_uuid": self.provider.uuid,
                "values": VALID_VALUES,
            },
        )
        self._submit(flow_uuid, expires_in_hours=48)
        flow = models.ActionFlow.objects.get(uuid=flow_uuid)
        assert flow.due_date is not None
        remaining = flow.due_date - sql_now()
        self.assertLess(remaining, datetime.timedelta(hours=49))
        self.assertGreater(remaining, datetime.timedelta(hours=47))

    def test_submit_clamps_expires_in_hours(self) -> None:
        _grant_management(self.staffs[0], self.provider)
        flow_uuid = self._draft()["id"]
        self.client.rest_post(
            f"flows/own/{flow_uuid}/actions",
            data={
                "action_type": "provider.update",
                "target_uuid": self.provider.uuid,
                "values": VALID_VALUES,
            },
        )
        self._submit(flow_uuid, expires_in_hours=10**6)
        flow = models.ActionFlow.objects.get(uuid=flow_uuid)
        assert flow.due_date is not None
        remaining = flow.due_date - sql_now()
        self.assertLess(remaining, datetime.timedelta(hours=consts_mcp.MAX_TTL_HOURS + 1))
        self.assertGreater(remaining, datetime.timedelta(hours=consts_mcp.MAX_TTL_HOURS - 1))

    def test_expired_draft_is_final(self) -> None:
        _grant_management(self.staffs[0], self.provider)
        body = self._draft()
        flow_uuid = body["id"]
        store = FlowStore()
        action = store.add_action(
            models.ActionFlow.objects.get(uuid=flow_uuid),
            action_type="provider.update",
            target_uuid=self.provider.uuid,
            values={"name": "v1"},
            base_values={"name": "old"},
            base_etag="etag",
        )
        # Force expiry (due date gone -> the lazy pass discards the draft)
        models.ActionFlow.objects.filter(uuid=flow_uuid).update(due_date=sql_now() - datetime.timedelta(days=1))
        store.get_flow(flow_uuid)
        self.assertEqual(
            models.ActionFlow.objects.get(uuid=flow_uuid).status,
            FlowStatus.EXPIRED,
        )

        # Terminal: neither the actions nor the flow come back, and no
        # edit revives them
        response = self.client.rest_put(
            f"flows/own/{flow_uuid}/actions/{action.uuid}",
            data={"values": {"name": "v2"}},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            models.ActionFlow.objects.get(uuid=flow_uuid).status,
            FlowStatus.EXPIRED,
        )


class FlowsOwnLifecycleTest(rest.test.RESTTestCase):
    """Submit, cancel and refusal of the generic edit paths."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login(user=self.staffs[0])
        _grant_management(self.staffs[0], self.provider)

    def _draft_with_action(self) -> str:
        flow_id = self.client.rest_post("flows/own", data={"name": "flow"}).json()["id"]
        response = self.client.rest_post(
            f"flows/own/{flow_id}/actions",
            data={
                "action_type": "provider.update",
                "target_uuid": self.provider.uuid,
                "values": VALID_VALUES,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        return flow_id

    def test_submit_flow_queues_it(self) -> None:
        flow_id = self._draft_with_action()
        response = self.client.rest_post(f"flows/own/{flow_id}/submit", data={})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], FlowStatus.PENDING)
        self.assertEqual(
            models.ActionFlow.objects.get(uuid=flow_id).status,
            FlowStatus.PENDING,
        )

    def test_submit_empty_flow_is_refused(self) -> None:
        flow_id = self.client.rest_post("flows/own", data={"name": "nothing to review"}).json()["id"]
        response = self.client.rest_post(f"flows/own/{flow_id}/submit", data={})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            models.ActionFlow.objects.get(uuid=flow_id).status,
            FlowStatus.DRAFT,
        )

    def test_submit_twice_is_400(self) -> None:
        flow_id = self._draft_with_action()
        self.client.rest_post(f"flows/own/{flow_id}/submit", data={})
        response = self.client.rest_post(f"flows/own/{flow_id}/submit", data={})
        self.assertEqual(response.status_code, 400, response.content)

    def test_cancel_own_pending_flow(self) -> None:
        flow_id = self._draft_with_action()
        self.client.rest_post(f"flows/own/{flow_id}/submit", data={})
        response = self.client.rest_delete(f"flows/own/{flow_id}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], FlowStatus.CANCELLED)
        self.assertEqual(
            models.ActionFlow.objects.get(uuid=flow_id).status,
            FlowStatus.CANCELLED,
        )

    def test_cancel_twice_is_400(self) -> None:
        self.login(user=self.staffs[0])
        flow_id = self.client.rest_post("flows/own", data={"name": "once"}).json()["id"]
        self.client.rest_delete(f"flows/own/{flow_id}")
        response = self.client.rest_delete(f"flows/own/{flow_id}")
        self.assertEqual(response.status_code, 400, response.content)

    def test_cancel_other_flow_is_404(self) -> None:
        self.login(user=self.staffs[0])
        other = FlowStore().create_flow(owner=self.admins[1], name="not mine")
        response = self.client.rest_delete(f"flows/own/{other.uuid}")
        self.assertEqual(response.status_code, 404, response.content)

    def test_generic_edit_is_refused(self) -> None:
        self.login(user=self.staffs[0])
        flow_id = self.client.rest_post("flows/own", data={"name": "immutable"}).json()["id"]
        response = self.client.rest_put(f"flows/own/{flow_id}", data={"name": "renamed"})
        self.assertEqual(response.status_code, 403, response.content)


class FlowsOwnActionsTest(rest.test.RESTTestCase):
    """Create/edit actions of own pending flows through the registry."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login(user=self.staffs[0])
        _grant_management(self.staffs[0], self.provider)
        self.flow_id = self.client.rest_post("flows/own", data={"name": "with actions"}).json()["id"]

    def _actions_url(self) -> str:
        return f"flows/own/{self.flow_id}/actions"

    def _add_action(self, values: dict[str, typing.Any] | None = None, **kwargs: typing.Any) -> typing.Any:
        payload: dict[str, typing.Any] = {
            "action_type": "provider.update",
            "target_uuid": self.provider.uuid,
            "values": VALID_VALUES if values is None else values,
            **kwargs,
        }
        return self.client.rest_post(self._actions_url(), data=payload)

    def test_add_action(self) -> None:
        response = self._add_action(justification="because")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()["result"]
        self.assertEqual(body["status"], FlowActionStatus.PENDING)
        self.assertEqual(body["order"], 1)
        self.assertEqual(body["target_uuid"], self.provider.uuid)

    def test_actions_listing_hides_admin_view_data(self) -> None:
        # Owner surface: no compliance indicator, no approval snapshot
        self._add_action()
        items = self.client.rest_get(self._actions_url()).json()
        self.assertEqual(items[0]["compliance"], "")
        self.assertEqual(items[0]["snap_info"], {})

    def test_owner_has_no_admin_action_methods(self) -> None:
        # Not exposed on the owner surface: the POST falls through to an
        # opaque invalid-request refusal (no admin operation leaks here)
        action_id = self._add_action().json()["result"]["id"]
        approve = self.client.rest_post(f"{self._actions_url()}/{action_id}/approve", data={})
        self.assertEqual(approve.status_code, 400, approve.content)
        skip = self.client.rest_post(f"{self._actions_url()}/{action_id}/skip", data={})
        self.assertEqual(skip.status_code, 400, skip.content)

    def test_add_action_without_management_is_403(self) -> None:
        # A provider this user has no permissions over
        other_provider = create_db_provider()
        response = self.client.rest_post(
            self._actions_url(),
            data={
                "action_type": "provider.update",
                "target_uuid": other_provider.uuid,
                "values": VALID_VALUES,
            },
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_add_action_unknown_field_is_400(self) -> None:
        response = self._add_action(values={"destroy": True})
        self.assertEqual(response.status_code, 400, response.content)

    def test_add_action_unknown_type_is_400(self) -> None:
        response = self.client.rest_post(
            self._actions_url(),
            data={
                "action_type": "provider.destroy_everything",
                "target_uuid": self.provider.uuid,
                "values": VALID_VALUES,
            },
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_add_read_only_action_is_400(self) -> None:
        # Reads are not registry bindings anymore: a ".get" id never
        # existed as an action type, so it is simply unknown (refused
        # before any target lookup)
        response = self.client.rest_post(
            self._actions_url(),
            data={
                "action_type": "servicepool.group.get",
                "target_uuid": self.provider.uuid,
                "values": {"groups": []},
            },
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(b"Unknown action type", response.content)

    def test_edit_action_rebases(self) -> None:
        action_id = self._add_action().json()["result"]["id"]
        response = self.client.rest_put(
            f"{self._actions_url()}/{action_id}",
            data={"values": {"name": "rebased by owner"}},
        )
        self.assertEqual(response.status_code, 200, response.content)
        action = models.ActionFlow.objects.get(uuid=self.flow_id).actions.get(uuid=action_id)
        self.assertEqual(action.values, {"name": "rebased by owner"})

    def test_edit_non_draft_action_is_400(self) -> None:
        action_id = self._add_action().json()["result"]["id"]
        self.client.rest_delete(f"flows/own/{self.flow_id}")  # Flow cancelled
        response = self.client.rest_put(
            f"{self._actions_url()}/{action_id}",
            data={"values": {"name": "too late"}},
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_add_action_after_submit_is_400(self) -> None:
        # A submitted flow is final: it no longer accepts actions or edits
        self._add_action()
        self.assertEqual(self.client.rest_post(f"flows/own/{self.flow_id}/submit", data={}).status_code, 200)
        response = self._add_action(values={"name": "one more"})
        self.assertEqual(response.status_code, 400, response.content)

    def test_edit_action_after_submit_is_400(self) -> None:
        action_id = self._add_action().json()["result"]["id"]
        self.assertEqual(self.client.rest_post(f"flows/own/{self.flow_id}/submit", data={}).status_code, 200)
        response = self.client.rest_put(
            f"{self._actions_url()}/{action_id}",
            data={"values": {"name": "too late"}},
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_action_delete_is_refused(self) -> None:
        action_id = self._add_action().json()["result"]["id"]
        response = self.client.rest_delete(f"{self._actions_url()}/{action_id}")
        self.assertEqual(response.status_code, 400, response.content)

    def test_add_action_to_foreign_flow_is_404(self) -> None:
        # Forged parent: another user's flow uuid in the detail URL
        foreign = FlowStore().create_flow(owner=self.admins[1], name="not mine")
        response = self.client.rest_post(
            f"flows/own/{foreign.uuid}/actions",
            data={
                "action_type": "provider.update",
                "target_uuid": self.provider.uuid,
                "values": VALID_VALUES,
            },
        )
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(foreign.actions.count(), 0)

    def test_foreign_flow_actions_are_404(self) -> None:
        foreign = FlowStore().create_flow(owner=self.admins[1], name="not mine either")
        response = self.client.rest_get(f"flows/own/{foreign.uuid}/actions")
        self.assertEqual(response.status_code, 404, response.content)

    def test_foreign_flow_single_action_is_404(self) -> None:
        foreign = FlowStore().create_flow(owner=self.admins[1], name="secret flow")
        action = FlowStore().add_action(
            foreign,
            action_type="provider.update",
            target_uuid=self.provider.uuid,
            values=dict(VALID_VALUES),
            base_values={"name": "old"},
            base_etag="etag",
        )
        self.assertEqual(
            self.client.rest_get(f"flows/own/{foreign.uuid}/actions/{action.uuid}").status_code,
            404,
        )

    def test_edit_action_on_foreign_flow_is_404(self) -> None:
        foreign = FlowStore().create_flow(owner=self.admins[1], name="not mine")
        action = FlowStore().add_action(
            foreign,
            action_type="provider.update",
            target_uuid=self.provider.uuid,
            values=dict(VALID_VALUES),
            base_values={"name": "old"},
            base_etag="etag",
        )
        response = self.client.rest_put(
            f"flows/own/{foreign.uuid}/actions/{action.uuid}",
            data={"values": {"name": "hijacked"}},
        )
        self.assertEqual(response.status_code, 404, response.content)
        action.refresh_from_db()
        self.assertEqual(action.values, dict(VALID_VALUES))

    def test_actions_listing_is_scoped_to_the_flow(self) -> None:
        # Even among the user's OWN flows, the detail lists just this flow's actions
        other_own = FlowStore().create_flow(owner=self.staffs[0], name="my other flow")
        for flow in (models.ActionFlow.objects.get(uuid=self.flow_id), other_own):
            FlowStore().add_action(
                flow,
                action_type="provider.update",
                target_uuid=self.provider.uuid,
                values=dict(VALID_VALUES),
                base_values={"name": "old"},
                base_etag="etag",
            )
        response = self.client.rest_get(self._actions_url())
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["order"], 1)

    def test_actions_cap(self) -> None:
        store = FlowStore()
        flow = models.ActionFlow.objects.get(uuid=self.flow_id)
        for _ in range(consts_mcp.MAX_ACTIONS_PER_FLOW - 1):
            store.add_action(
                flow,
                action_type="provider.update",
                target_uuid=self.provider.uuid,
                values=dict(VALID_VALUES),
                base_values={"name": "old"},
                base_etag="etag",
            )
        # One more fits; the one after the cap must be refused
        self.assertEqual(self._add_action().status_code, 200)
        response = self._add_action()
        self.assertEqual(response.status_code, 400, response.content)

    def test_flow_cap(self) -> None:
        store = FlowStore()
        # setUp already created one pending flow; fill up to the cap
        for _ in range(consts_mcp.MAX_FLOWS_PER_USER - 1):
            store.create_flow(owner=self.staffs[0], name="filler")
        response = self.client.rest_post("flows/own", data={"name": "one too many"})
        self.assertEqual(response.status_code, 400, response.content)


class FlowsOwnConfigUpdateTest(rest.test.RESTTestCase):
    """``config.update`` proposals: the propose gate is superuser-only.

    Configuration has no permission model: ``MutableActionType
    .check_propose_access`` is overridden on the type, so staff with
    MANAGEMENT over anything still cannot propose configuration changes.
    """

    @staticmethod
    def _create_config_value(key: str, value: str) -> models.Config:
        from uds.core.util.config import Config as CfgConfig

        return models.Config.objects.create(
            section="Security", key=key, value=value, field_type=int(CfgConfig.FieldType.BOOLEAN), help="test"
        )

    def _payload(self, cfg: models.Config) -> dict[str, typing.Any]:
        return {
            "action_type": "config.update",
            "target_uuid": f"Security.{cfg.key}",
            "values": {f"Security.{cfg.key}": True},
            "justification": "security check",
        }

    def test_staff_cannot_propose_config_changes(self) -> None:
        cfg = self._create_config_value("Some Setting", "0")
        self.login(user=self.staffs[0])
        flow_id = self.client.rest_post("flows/own", data={"name": "cfg"}).json()["id"]
        response = self.client.rest_post(f"flows/own/{flow_id}/actions", data=self._payload(cfg))
        self.assertEqual(response.status_code, 403, response.content)

    def test_admin_can_propose_config_changes(self) -> None:
        cfg = self._create_config_value("Some Setting", "0")
        self.login(user=self.admins[0])
        flow_id = self.client.rest_post(
            "flows/own", data={"name": "cfg", "justification": "security check"}
        ).json()["id"]
        response = self.client.rest_post(f"flows/own/{flow_id}/actions", data=self._payload(cfg))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()["result"]
        self.assertEqual(body["status"], FlowActionStatus.PENDING)
        # The CAS base is the current value of the key
        action = models.FlowAction.objects.get(uuid=body["id"])
        self.assertEqual(
            action.base_values,
            {f"Security.{cfg.key}": "0"},
        )

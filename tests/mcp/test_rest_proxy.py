"""Tests for the MCP OData-to-REST argument translation and the detail proxy."""

import typing
import unittest
from unittest import mock

from asgiref.sync import async_to_sync

from uds import models
from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.core.types.requests import ExtendedHttpRequestWithUser
from uds.mcp.rest_proxy import ODataArgs, RestProxy, RestTarget, odata_params_from
from uds.REST.handlers import Handler
from uds.REST.methods.providers import Providers
from uds.REST.methods.services import Services

from tests.fixtures import services as services_fixtures
from tests.utils import rest


class ODataTranslationTest(unittest.TestCase):
    """Validate the structured OData argument translation."""

    def test_structured_fields_are_translated(self) -> None:
        params = odata_params_from(
            {
                "filter": "state eq ACTIVE",
                "orderby": "name",
                "top": 5,
                "skip": 10,
                "select": ["id", "name", "state"],
            }
        )
        self.assertEqual(params["$filter"], "state eq ACTIVE")
        self.assertEqual(params["$orderby"], "name")
        self.assertEqual(params["$top"], 5)
        self.assertEqual(params["$skip"], 10)
        self.assertEqual(params["$select"], "id,name,state")

    def test_typed_args_are_translated(self) -> None:
        params = odata_params_from(ODataArgs(filter="x", top=10))
        self.assertEqual(params, {"$filter": "x", "$top": 10})

    def test_raw_dollar_keys_are_kept(self) -> None:
        params = odata_params_from({"$filter": "name eq foo", "$top": 3})
        self.assertEqual(params, {"$filter": "name eq foo", "$top": 3})

    def test_unsupported_inputs_return_empty(self) -> None:
        self.assertEqual(odata_params_from(None), {})
        self.assertEqual(odata_params_from("not a dict"), {})
        self.assertEqual(odata_params_from(42), {})


class DetailProxyPermissionsTest(unittest.TestCase):
    """``_execute_detail_sync`` mirrors ``process_detail``: writes to a
    detail collection need MANAGEMENT over the parent, reads only READ,
    and the parent uuid scopes the collection."""

    @staticmethod
    def _targets(method: types.rest.CustomMethodMethod) -> tuple[RestTarget, typing.Any]:
        parent = RestTarget(typing.cast("type[Handler]", mock.MagicMock(name="ParentHandler")), "meta_pools")
        detail_cls = mock.MagicMock(name="DetailHandler")
        target = RestTarget(
            typing.cast("type[Handler]", detail_cls),
            "meta_pools/{uuid}/pools",
            method,
            args=("member-1",),
            parent=parent,
        )
        return target, detail_cls

    def _run(
        self,
        method: types.rest.CustomMethodMethod,
        has_access_return: bool,
    ) -> tuple[typing.Any, mock.MagicMock, mock.MagicMock]:
        target, detail_cls = self._targets(method)
        request = mock.MagicMock(name="Request")
        with mock.patch(
            "uds.mcp.rest_proxy.permissions.has_access", return_value=has_access_return
        ) as has_access:
            try:
                result = async_to_sync(RestProxy().execute)(target, request, {"a": 1}, parent_uuid="parent-1")
            except rest_exceptions.AccessDenied:
                result = "denied"
        return result, has_access, detail_cls

    def test_write_verbs_require_management(self) -> None:
        for method in (
            types.rest.CustomMethodMethod.PUT,
            types.rest.CustomMethodMethod.POST,
            types.rest.CustomMethodMethod.DELETE,
        ):
            result, has_access, _ = self._run(method, True)
            self.assertEqual(
                has_access.call_args[0][2], types.permissions.PermissionType.MANAGEMENT, str(method)
            )
            self.assertNotEqual(result, "denied", str(method))

    def test_read_requires_only_read(self) -> None:
        result, has_access, _ = self._run(types.rest.CustomMethodMethod.QUERY, True)
        self.assertEqual(has_access.call_args[0][2], types.permissions.PermissionType.READ)
        self.assertNotEqual(result, "denied")

    def test_access_denied_without_permission(self) -> None:
        result, _, detail_cls = self._run(types.rest.CustomMethodMethod.DELETE, False)
        self.assertEqual(result, "denied")
        detail_cls.assert_not_called()

    def test_parent_uuid_scopes_the_detail_path(self) -> None:
        target, detail_cls = self._targets(types.rest.CustomMethodMethod.POST)
        request = mock.MagicMock(name="Request")
        with mock.patch("uds.mcp.rest_proxy.permissions.has_access", return_value=True):
            async_to_sync(RestProxy().execute)(target, request, {"a": 1}, parent_uuid="parent-1")
        instantiate_args = detail_cls.call_args
        # path resolved, member uuid travels as the only positional arg
        self.assertEqual(instantiate_args[0][1], "meta_pools/parent-1/pools")
        self.assertEqual(instantiate_args[0][3], "member-1")
        detail_cls.return_value.post.assert_called_once_with()


class DetailProxyIfMatchContractTest(rest.test.RESTTestCase):
    """Contract: the proxy executes the *real* ``DetailHandler`` verb method.

    MCP mutations end here (``op_update`` and friends issue PUT/POST/DELETE
    through the proxy), so whatever the REST layer validates — including the
    RFC 7232 ``If-Match`` precondition that ``DetailHandler.put`` evaluates
    before ``save_item`` — must keep running through the proxy unchanged.
    Today preconditions are optional, so a headerless MCP PUT succeeds; the
    stale-``If-Match`` case is the one that would break if a future refactor
    bypassed ``put()`` (or if the check became mandatory and the flow layer
    was not wired to carry the agent's etag).
    """

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        self.service = services_fixtures.create_db_service(self.provider)

    def _api_request(self, if_match: str | None = None) -> ExtendedHttpRequestWithUser:
        """Authenticated request carrying the optional precondition header.

        ``headers`` is a ``cached_property`` over ``META``, so writing the
        ``HTTP_*`` entries before the first access is enough.
        """
        token = self.login_with_api_token()
        request = ExtendedHttpRequestWithUser()
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        if if_match is not None:
            request.META["HTTP_IF_MATCH"] = if_match
        return request

    def _target(self) -> RestTarget:
        return RestTarget(
            Services,
            "providers/{uuid}/services",
            types.rest.CustomMethodMethod.PUT,
            args=(str(self.service.uuid),),
            parent=RestTarget(Providers, "providers"),
        )

    def _payload(self, suffix: str) -> dict[str, typing.Any]:
        return {
            "name": self.service.name + suffix,
            "comments": self.service.comments,
            "data_type": self.service.data_type,
            "max_services_count_type": 0,
            "tags": [],
        }

    def _current_etag(self) -> str:
        """The exact etag ``DetailHandler.put`` will compare ``If-Match`` against."""
        request = self._api_request()
        # Same wiring the proxy does in ``_execute_detail_sync``; the parent
        # handler is a ``ModelHandler[ProviderItem]``, the detail one expects
        # the invariant item type, so the bridge is cast here as in the proxy.
        parent = typing.cast(typing.Any, Providers(request, "providers", "put", {}, str(self.provider.uuid)))
        parent_item = typing.cast(typing.Any, models.Provider.objects.get(uuid=self.provider.uuid))
        detail = Services(
            parent,
            f"providers/{self.provider.uuid}/services",
            {},
            str(self.service.uuid),
            user=parent._user,
            parent_item=parent_item,
        )
        _, etag = detail._item_with_etag_from_uuuid(parent_item, str(self.service.uuid))
        self.assertNotEqual(etag, "", "detail etag must be computed for ManagedObjectItem")
        return etag

    def _put(self, if_match: str | None, suffix: str) -> typing.Any:
        return async_to_sync(RestProxy().execute)(
            self._target(),
            self._api_request(if_match),
            self._payload(suffix),
            parent_uuid=str(self.provider.uuid),
        )

    def test_put_without_precondition_is_allowed(self) -> None:
        """RFC 7232 optionality: headerless proxy PUT applies the update."""
        result = self._put(if_match=None, suffix=" (proxy no precondition)")
        self.assertEqual(result.item.uuid, self.service.uuid)
        self.service.refresh_from_db()
        self.assertTrue(self.service.name.endswith(" (proxy no precondition)"))

    def test_put_with_matching_if_match_succeeds(self) -> None:
        result = self._put(if_match=f'"{self._current_etag()}"', suffix=" (proxy matching)")
        self.assertEqual(result.item.uuid, self.service.uuid)

    def test_put_with_stale_if_match_raises_before_saving(self) -> None:
        """The contract case: a drifted item rejects the proxy PUT via the
        handler's own precondition check, and nothing is written."""
        stale = self._current_etag()
        self._put(if_match=None, suffix=" (drift)")
        with self.assertRaises(rest_exceptions.PreconditionFailed):
            self._put(if_match=f'"{stale}"', suffix=" (stale)")
        self.service.refresh_from_db()
        self.assertTrue(self.service.name.endswith(" (drift)"), "stale PUT must not reach save_item")

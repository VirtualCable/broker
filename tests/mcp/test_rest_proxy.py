"""Tests for the MCP OData-to-REST argument translation and the detail proxy."""

import typing
import unittest
from unittest import mock

from asgiref.sync import async_to_sync

from uds.core import types
from uds.core.exceptions import rest as rest_exceptions
from uds.mcp.rest_proxy import ODataArgs, RestProxy, RestTarget, odata_params_from
from uds.REST.handlers import Handler


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

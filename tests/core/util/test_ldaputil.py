# -*- coding: utf-8 -*-

from uds.core.util import ldaputil

from ...utils.test import UDSTestCase


class FakeLDAPConnection:
    def __init__(self, result: dict[str, object], *, add_result: bool = True, modify_result: bool = True):
        self.result = result
        self._add_result = add_result
        self._modify_result = modify_result

    def add(self, dn: str, attributes: dict[str, list[bytes | str]]) -> bool:
        del dn, attributes
        return self._add_result

    def modify(
        self,
        dn: str,
        changes: dict[str, list[tuple[str, list[bytes | str]]]],
        controls: object = None,
    ) -> bool:
        del dn, changes, controls
        return self._modify_result


class LdapUtilTest(UDSTestCase):
    def test_add_raises_already_exists_for_duplicate_entry(self) -> None:
        connection = FakeLDAPConnection(
            {"result": 68, "description": "entryAlreadyExists", "message": "entry already exists"},
            add_result=False,
        )

        with self.assertRaises(ldaputil.ALREADY_EXISTS):
            ldaputil.add(connection, "cn=test,dc=example,dc=com", attributes={"cn": ["test"]})  # type: ignore[arg-type]

    def test_modify_raises_already_exists_for_duplicate_member(self) -> None:
        connection = FakeLDAPConnection(
            {"result": 20, "description": "attributeOrValueExists", "message": "value already exists"},
            modify_result=False,
        )

        with self.assertRaises(ldaputil.ALREADY_EXISTS):
            ldaputil.modify(
                connection,  # type: ignore[arg-type]
                "cn=group,dc=example,dc=com",
                {"member": [(ldaputil.MODIFY_ADD, ["cn=machine,dc=example,dc=com"])]},
            )

    def test_add_raises_ldap_error_for_non_duplicate_failure(self) -> None:
        connection = FakeLDAPConnection(
            {"result": 50, "description": "insufficientAccessRights", "message": "access denied"},
            add_result=False,
        )

        with self.assertRaises(ldaputil.LDAPError):
            ldaputil.add(connection, "cn=test,dc=example,dc=com", attributes={"cn": ["test"]})  # type: ignore[arg-type]

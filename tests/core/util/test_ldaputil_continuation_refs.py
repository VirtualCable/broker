"""Regression: referrals that arrive as SearchResultReference are not surfaced.

An AD DC that holds the forest root answers a subtree search for an object living in a
child domain with ``success`` plus a continuation reference, not with result code 10:

    result: success (0)
    result['referrals']: None
    response: [{'uri': ['ldap://child.corp.local/DC=child,DC=corp,DC=local'],
                'type': 'searchResRef'}, ...]

``_extract_referrals`` only reads ``con.result['referrals']``, which is populated on
result code 10 (Referral) — searching directly at the child base. On the forest root
base, which is what the AD authenticator searches when no explicit Base DN is set, the
referral is dropped and the caller never gets a chance to follow it.

This test fails on purpose until the continuation references are surfaced too.
"""

import typing

from uds.core.util import ldaputil

from tests.utils.test import UDSTestCase


class _ContinuationRefConn:
    """ldap3 connection double for a successful search with continuation references."""

    def __init__(self) -> None:
        self.result: dict[str, typing.Any] = {
            "result": 0,
            "description": "success",
            "referrals": None,
        }
        self.response: list[dict[str, typing.Any]] = [
            {
                "uri": ["ldap://child.corp.local/DC=child,DC=corp,DC=local"],
                "type": "searchResRef",
            },
            {
                "uri": ["ldap://corp.local/CN=Configuration,DC=corp,DC=local"],
                "type": "searchResRef",
            },
        ]
        self.entries: list[typing.Any] = []

    def search(self, **kwargs: typing.Any) -> bool:
        del kwargs
        return True


class ContinuationReferencesTest(UDSTestCase):
    def test_search_result_references_are_surfaced_as_referrals(self) -> None:
        con = typing.cast(typing.Any, _ContinuationRefConn())

        with self.assertRaises(ldaputil.LDAPReferralError) as ctx:
            list(
                ldaputil.as_dict(
                    con=con,
                    base="dc=corp,dc=local",
                    ldap_filter="(userPrincipalName=someone@child.corp.local)",
                    attributes=["displayName"],
                    limit=100,
                    raise_on_referrals=True,
                )
            )

        self.assertIn(
            "ldap://child.corp.local/DC=child,DC=corp,DC=local", ctx.exception.referrals
        )

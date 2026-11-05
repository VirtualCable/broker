"""
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

from uds.core import types

from ...utils.helpers import limited_iterator
from ...utils.test import UDSTransactionTestCase
from . import fixtures

# From PoolObjectSQL::name_is_valid in the OpenNebula core
ONE_MAX_NAME_LENGTH = 128
ONE_INVALID_NAME_CHARS = "&|:\\\";/'#{}$<>\t\n\v\f\r"


class TestOpenNebulaPublicationName(UDSTransactionTestCase):
    def test_longest_basename_still_yields_a_valid_template_name(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider(), basename="a" * 14, lenname=1)
            pub = fixtures.create_publication(srvc)

            state = pub.publish()
            for _ in limited_iterator(lambda: state == types.states.TaskState.RUNNING, limit=64):
                state = pub.check_state()

            self.assertEqual(state, types.states.TaskState.FINISHED, pub._error_debug_info)
            name = one.names[pub.get_template_id()]
            self.assertTrue(name.startswith("UDS-Pub-" + "a" * 14 + "-"))
            self.assertLessEqual(len(name), ONE_MAX_NAME_LENGTH)
            self.assertFalse(set(name) & set(ONE_INVALID_NAME_CHARS), name)

    def test_servicepool_name_is_sanitized_for_opennebula(self) -> None:
        srvc = fixtures.create_service(fixtures.create_provider())
        self.assertEqual(srvc.sanitized_name("UDS-Pub-pool:a/b|c-v1"), "UDS-Pub-pool_a_b_c-v1")

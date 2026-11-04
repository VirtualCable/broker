"""
Author: Andres Schumann, aschumann at virtualcable dot es
"""

from unittest import mock

from uds.core import types

from ...utils.helpers import limited_iterator
from ...utils.test import UDSTransactionTestCase
from . import fixtures


class TestOpenNebulaPublication(UDSTransactionTestCase):
    def test_publish_creates_a_template_and_waits_until_published(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider())
            pub = fixtures.create_publication(srvc)

            state = pub.publish()
            self.assertEqual(state, types.states.TaskState.RUNNING)

            checks = 0
            for _ in limited_iterator(lambda: state == types.states.TaskState.RUNNING, limit=64):
                state = pub.check_state()
                checks += 1

            self.assertEqual(state, types.states.TaskState.FINISHED, pub._error_debug_info)
            self.assertGreater(checks, 1)  # The template was not published right away

            template_id = pub.get_template_id()
            self.assertEqual(one.called("make_template"), [template_id])
            self.assertEqual(one.names[template_id], pub._name)
            self.assertNotIn(" ", pub._name)

    def test_publish_error_is_reported(self) -> None:
        with fixtures.patched_opennebula():
            srvc = fixtures.create_service(fixtures.create_provider())
            pub = fixtures.create_publication(srvc)

            with mock.patch.object(srvc, "make_template", side_effect=Exception("no space")):
                state = pub.publish()
                for _ in limited_iterator(lambda: state == types.states.TaskState.RUNNING, limit=64):
                    state = pub.check_state()

            self.assertEqual(state, types.states.TaskState.ERROR)
            self.assertIn("no space", pub.error_reason())

    def test_destroy_removes_the_template(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider())
            pub = fixtures.create_publication(srvc)
            pub._vmid = "55"
            one.templates["55"] = True

            with mock.patch("uds.core.services.generics.dynamic.service.DeferredDeletionWorker") as worker:
                state = pub.destroy()
                for _ in limited_iterator(lambda: state == types.states.TaskState.RUNNING, limit=64):
                    state = pub.check_state()

            worker.add.assert_called_once_with(srvc, "TP:55")
            # The worker is mocked, so run the deferred deletion here
            srvc.execute_delete("TP:55")
            self.assertEqual(one.called("remove_template"), ["55"])

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

    def test_longest_names_fit_in_opennebula(self) -> None:
        # OpenNebula rejects names longer than 128 characters (PoolObjectSQL::name_is_valid)
        opennebula_max_name_length = 128
        # Base name plus name length cannot exceed 15 (validators.validate_basename)
        srvc = fixtures.create_service(fixtures.create_provider(), basename="B" * 14, lenname=1)
        pub = fixtures.create_publication(srvc)

        with mock.patch.object(pub, "revision", return_value=999_999):
            template_name = pub.generate_name()
        disk_image_name = srvc.sanitized_name(f"{template_name} DSK 99")

        self.assertLessEqual(len(template_name), opennebula_max_name_length)
        self.assertLessEqual(len(disk_image_name), opennebula_max_name_length)

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

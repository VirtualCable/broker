"""
Author: Andres Schumann, aschumann at virtualcable dot es
"""

from unittest import mock

from uds.core import exceptions, types
from uds.services.OpenNebula import on

from ...utils.test import UDSTransactionTestCase
from . import fixtures


class TestOpenNebulaService(UDSTransactionTestCase):
    def test_name_fields_keep_their_previous_names(self) -> None:
        srvc = fixtures.create_service(fixtures.create_provider())

        self.assertIn("baseName", srvc.basename.old_field_name())
        self.assertIn("lenName", srvc.lenname.old_field_name())
        self.assertEqual(srvc.get_basename(), "bname")
        self.assertEqual(srvc.get_lenname(), 5)

    def test_machine_info(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider())
            one.vms["1"] = fixtures.FakeVm(mac="02:aa:bb:cc:dd:ee", ip="10.0.0.7")

            self.assertEqual(srvc.get_mac(None, "1"), "02:AA:BB:CC:DD:EE")
            self.assertEqual(srvc.get_ip(None, "1"), "10.0.0.7")
            # Nothing can be given before the machine exists
            self.assertEqual(srvc.get_mac(None, "", for_unique_id=True), "")
            self.assertEqual(srvc.get_mac(None, "1", for_unique_id=True), "")

    def test_is_running_only_when_active(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider())
            one.vms["1"] = fixtures.FakeVm()

            for state, expected in (
                (on.types.VmState.ACTIVE, True),
                (on.types.VmState.POWEROFF, False),
                (on.types.VmState.STOPPED, False),
            ):
                one.vms["1"].state = state
                self.assertEqual(srvc.is_running(None, "1"), expected, state)

            self.assertFalse(srvc.is_running(None, "unknown"))

    def test_power_operations(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider())
            one.vms["1"] = fixtures.FakeVm()

            srvc.start(None, "1")
            srvc.stop(None, "1")
            srvc.shutdown(None, "1")
            srvc.reset(None, "1")

            self.assertEqual([c[0] for c in one.calls], ["start", "stop", "shutdown", "reset"])

    def test_delete_is_deferred_with_the_kind_of_the_item(self) -> None:
        with fixtures.patched_opennebula():
            srvc = fixtures.create_service(fixtures.create_provider())
            userservice = fixtures.create_userservice(srvc)
            publication = userservice.publication()

            with mock.patch("uds.core.services.generics.dynamic.service.DeferredDeletionWorker") as worker:
                srvc.delete(userservice, "7")
                srvc.delete(publication, "8")

            self.assertEqual(
                worker.add.call_args_list,
                [mock.call(srvc, "VM:7"), mock.call(srvc, "TP:8")],
            )

    def test_execute_delete_machine(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider())

            for state, substate in (
                (on.types.VmState.ACTIVE, fixtures.LCM_RUNNING),
                (on.types.VmState.POWEROFF, 0),
                (on.types.VmState.STOPPED, 0),
                (on.types.VmState.DONE, 0),
            ):
                one.calls.clear()
                one.vms["1"] = fixtures.FakeVm(state=state, substate=substate)
                srvc.execute_delete("VM:1")
                self.assertEqual(one.called("remove"), ["1"], state)

    def test_execute_delete_waits_while_the_machine_is_booting(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider())
            one.vms["1"] = fixtures.FakeVm(state=on.types.VmState.ACTIVE, substate=2)

            with self.assertRaises(exceptions.services.generics.RetryableError):
                srvc.execute_delete("VM:1")
            self.assertEqual(one.called("remove"), [])

            # Once it is running, it can be removed
            one.vms["1"].substate = fixtures.LCM_RUNNING
            srvc.execute_delete("VM:1")
            self.assertEqual(one.called("remove"), ["1"])

    def test_execute_delete_machine_not_found(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider())

            with self.assertRaises(exceptions.services.generics.NotFoundError):
                srvc.execute_delete("VM:404")
            self.assertEqual(one.called("remove"), [])

    def test_execute_delete_template(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider())
            one.templates["5"] = True

            srvc.execute_delete("TP:5")

            self.assertEqual(one.called("remove_template"), ["5"])
            self.assertEqual(one.called("remove"), [])

    def test_does_not_stop_before_deleting(self) -> None:
        srvc = fixtures.create_service(fixtures.create_provider())
        self.assertFalse(srvc.must_stop_before_deletion)
        self.assertEqual(
            srvc.user_service_type._destroy_queue,
            [
                types.services.Operation.DELETE,
                types.services.Operation.DELETE_COMPLETED,
                types.services.Operation.FINISH,
            ],
        )

"""
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import typing

from unittest import mock

from uds import models
from uds.core import types
from uds.services.OpenNebula import on

from ...utils.helpers import limited_iterator
from ...utils.test import UDSTransactionTestCase
from . import fixtures

if typing.TYPE_CHECKING:
    from uds.services.OpenNebula.deployment import OpenNebulaLiveDeployment


class TestOpenNebulaUserService(UDSTransactionTestCase):
    def _run(
        self, userservice: "OpenNebulaLiveDeployment", state: types.states.TaskState
    ) -> types.states.TaskState:
        for _ in limited_iterator(lambda: state == types.states.TaskState.RUNNING, limit=128):
            state = userservice.check_state()
            # Simulates the wake up from the os manager of a machine waiting to go to the L2 cache
            if userservice._queue and userservice._queue[0] == types.services.Operation.WAIT:
                userservice.process_ready_from_os_manager(None)
        return state

    def _setup(self, one: fixtures.FakeOpenNebula) -> "OpenNebulaLiveDeployment":
        srvc = fixtures.create_service(fixtures.create_provider())
        pub = fixtures.create_publication(srvc)
        pub._vmid = "10"  # Template id
        return fixtures.create_userservice(srvc, pub)

    def assert_deployed(self, userservice: "OpenNebulaLiveDeployment", one: fixtures.FakeOpenNebula) -> None:
        service = userservice.service()
        self.assertEqual(one.called("deploy"), [userservice._vmid])
        self.assertEqual(one.names[userservice._vmid], userservice._name)
        self.assertEqual(userservice._name[: len(service.get_basename())], service.get_basename())
        self.assertEqual(len(userservice._name), len(service.get_basename()) + service.get_lenname())
        vm = one.vms[userservice._vmid]
        self.assertEqual(userservice.get_unique_id(), vm.mac.upper())
        self.assertEqual(userservice.get_ip(), vm.ip)

    def test_cache_l1(self) -> None:
        with fixtures.patched_opennebula() as one:
            userservice = self._setup(one)

            state = userservice.deploy_for_cache(level=types.services.CacheLevel.L1)
            self.assertEqual(state, types.states.TaskState.RUNNING)
            state = self._run(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            self.assert_deployed(userservice, one)
            self.assertEqual(one.vms[userservice._vmid].state, on.types.VmState.ACTIVE)

    def test_cache_l2_ends_shut_down(self) -> None:
        with fixtures.patched_opennebula() as one:
            userservice = self._setup(one)

            state = userservice.deploy_for_cache(level=types.services.CacheLevel.L2)
            state = self._run(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            self.assert_deployed(userservice, one)
            self.assertEqual(one.called("shutdown"), [userservice._vmid])
            self.assertEqual(one.vms[userservice._vmid].state, on.types.VmState.POWEROFF)

    def test_user(self) -> None:
        with fixtures.patched_opennebula() as one:
            userservice = self._setup(one)

            state = userservice.deploy_for_user(models.User())
            state = self._run(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            self.assert_deployed(userservice, one)

    def test_machine_lost_while_creating_is_an_error(self) -> None:
        with fixtures.patched_opennebula() as one:
            userservice = self._setup(one)

            state = userservice.deploy_for_user(models.User())
            for _ in limited_iterator(lambda: not userservice._vmid, limit=16):
                state = userservice.check_state()
            del one.vms[userservice._vmid]  # Someone removes it on OpenNebula
            state = self._run(userservice, state)

            self.assertEqual(state, types.states.TaskState.ERROR)

    def test_machine_name_is_not_prefixed(self) -> None:
        with fixtures.patched_opennebula() as one:
            userservice = self._setup(one)

            name = userservice.get_vmname()

            self.assertTrue(name.startswith("bname"), name)
            self.assertEqual(len(name), len("bname") + 5)

    def test_set_ready_starts_a_stopped_machine(self) -> None:
        with fixtures.patched_opennebula() as one:
            userservice = self._setup(one)
            state = self._run(userservice, userservice.deploy_for_cache(level=types.services.CacheLevel.L2))
            self.assertEqual(state, types.states.TaskState.FINISHED)
            one.calls.clear()

            state = self._run(userservice, userservice.set_ready())

            self.assertEqual(state, types.states.TaskState.FINISHED)
            self.assertEqual(one.called("start"), [userservice._vmid])

    def test_reset(self) -> None:
        with fixtures.patched_opennebula() as one:
            userservice = self._setup(one)
            state = self._run(userservice, userservice.deploy_for_user(models.User()))
            self.assertEqual(state, types.states.TaskState.FINISHED)

            state = self._run(userservice, userservice.reset())

            self.assertEqual(state, types.states.TaskState.FINISHED)
            self.assertEqual(one.called("reset"), [userservice._vmid])

    def test_destroy_deletes_without_stopping(self) -> None:
        with fixtures.patched_opennebula() as one:
            userservice = self._setup(one)
            state = self._run(userservice, userservice.deploy_for_user(models.User()))
            self.assertEqual(state, types.states.TaskState.FINISHED)
            vmid = userservice._vmid

            with mock.patch("uds.core.services.generics.dynamic.service.DeferredDeletionWorker") as worker:
                state = userservice.destroy()
                # The deferred deletion has been requested, so it is done when no longer in progress
                with mock.patch.object(userservice.service(), "is_deletion_in_progress", return_value=False):
                    state = self._run(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            worker.add.assert_called_once_with(userservice.service(), f"VM:{vmid}")
            self.assertEqual(one.called("stop"), [])

    def test_console_connection(self) -> None:
        with fixtures.patched_opennebula():
            userservice = self._setup(None)  # type: ignore
            userservice._vmid = "3"
            connection = types.services.ConsoleConnectionInfo(
                type="spice",
                address="host",
                port=5900,
                secure_port=0,
                cert_subject="",
                ticket=types.services.ConsoleConnectionTicket(value="ticket"),
            )

            with mock.patch.object(
                userservice.service().provider(), "get_console_connection", return_value=connection
            ) as get_console:
                self.assertEqual(userservice.get_console_connection(), connection)

            get_console.assert_called_once_with("3")

"""Registry tests: code-defined action types are discoverable."""

import unittest

from uds.mutability import (
    all_type_ids,
    get as registry_get,
)
from uds.mutability.base import ActionOperation, EntityDescriptor, MutableActionType
from uds.mutability.registry import register
from uds.mutability.types.meta_pools import MetaPoolMember
from uds.mutability.types.providers import ProviderUpdate
from uds.mutability.types.services import ServiceUpdate
from uds.mutability.types.service_pools import ServicePoolGroup


class SupportedOperationsTest(unittest.TestCase):
    """Write operations are derived from the implemented hooks, never declared."""

    def test_single_write_family_derives_update(self) -> None:
        self.assertEqual(ProviderUpdate.supported_operations(), frozenset({ActionOperation.UPDATE}))
        self.assertEqual(ServiceUpdate.supported_operations(), frozenset({ActionOperation.UPDATE}))

    def test_relation_family_derives_all_write_verbs(self) -> None:
        # Reads are not operations: only the implemented op_* hooks derive.
        self.assertEqual(
            ServicePoolGroup.supported_operations(),
            frozenset({ActionOperation.SET, ActionOperation.ADD, ActionOperation.DELETE}),
        )

    def test_read_override_publishes_a_read_not_an_operation(self) -> None:
        # MetaPoolMember implements only op_set; its read view is not a
        # binding, it publishes the curated get_* tool through readable().
        self.assertEqual(MetaPoolMember.supported_operations(), frozenset({ActionOperation.SET}))
        self.assertIsNot(MetaPoolMember.read, EntityDescriptor.read)
        self.assertTrue(MetaPoolMember.readable())

    def test_write_only_family_is_not_readableView(self) -> None:
        # ProviderUpdate inherits the generic read but does not override it,
        # so readable() is False: no get_* tool is generated for it.
        self.assertFalse(ProviderUpdate.readable())
        self.assertIs(ProviderUpdate.read, EntityDescriptor.read)

    def test_registration_rejects_a_family_with_no_hooks(self) -> None:
        # The rejecting base op_* stubs are not implementations: a family
        # that overrides no op_* and is not readable has nothing to
        # register. The class is only passed around (never instantiated),
        # so the abstract hooks stay unimplemented.
        class _Nothing(MutableActionType):
            type_id = "nothing"

        self.assertEqual(_Nothing.supported_operations(), frozenset())
        self.assertFalse(_Nothing.readable())
        with self.assertRaises(ValueError):
            register(_Nothing)

    def test_full_ids_match_derivation(self) -> None:
        # Every full id in the registry is its root's derived operation.
        for full_id in all_type_ids():
            root, _, verb = full_id.rpartition(".")
            factory = registry_get(full_id)
            assert factory is not None
            operation = ActionOperation(verb)
            self.assertIn(operation, type(factory()).supported_operations(), full_id)
            self.assertEqual(root, type(factory()).type_id)


class MutabilityRegistryTest(unittest.TestCase):
    """The code-defined registry exposes provider.update."""

    def test_provider_update_is_registered(self) -> None:
        found = registry_get("provider.update")
        # The registry stores factories; call sites instantiate per use
        assert found is not None
        self.assertIs(type(found()), ProviderUpdate)
        self.assertIn("provider.update", all_type_ids())

    def test_service_update_is_registered(self) -> None:
        found = registry_get("service.update")
        assert found is not None
        self.assertIs(type(found()), ServiceUpdate)
        self.assertIn("service.update", all_type_ids())

    def test_unknown_type_returns_none(self) -> None:
        self.assertIsNone(registry_get("provider.destroy_everything"))


if __name__ == "__main__":
    unittest.main()

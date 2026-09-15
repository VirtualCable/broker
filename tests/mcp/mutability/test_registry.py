"""Registry tests: code-defined action types are discoverable."""

import unittest

from uds.mutability import (
    all_type_ids,
    get as registry_get,
)
from uds.mutability.types.providers import ProviderUpdate
from uds.mutability.types.services import ServiceUpdate


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

"""Registry tests: code-defined action types are discoverable."""

import unittest

from uds.mutability import (
    all_types,
    get as registry_get,
)
from uds.mutability.types.providers import ProviderUpdate
from uds.mutability.types.services import ServiceUpdate


class MutabilityRegistryTest(unittest.TestCase):
    """The code-defined registry exposes provider.update."""

    def test_provider_update_is_registered(self) -> None:
        found = registry_get("provider.update")
        # The registry stores classes; call sites instantiate per use
        self.assertIs(found, ProviderUpdate)
        self.assertIn("provider.update", [t.type_id for t in all_types()])

    def test_service_update_is_registered(self) -> None:
        found = registry_get("service.update")
        self.assertIs(found, ServiceUpdate)
        self.assertIn("service.update", [t.type_id for t in all_types()])

    def test_unknown_type_returns_none(self) -> None:
        self.assertIsNone(registry_get("provider.destroy_everything"))


if __name__ == "__main__":
    unittest.main()

import io
from unittest import mock

import yaml
from django.core.management import call_command
from django.utils.translation import gettext_lazy

from tests.fixtures import services as services_fixtures
from tests.fixtures.modules.service.provider import TestProvider
from tests.utils.test import UDSTestCase


class TestTreeCommand(UDSTestCase):
    def test_tree_dumps_lazy_translations_as_plain_text(self) -> None:
        services_fixtures.create_db_assigned_userservices()
        out = io.StringIO()

        with mock.patch.object(TestProvider, "type_name", gettext_lazy("Lazy testing provider")):
            call_command("tree", stdout=out)

        dump = out.getvalue()
        self.assertNotIn("could not be processed", dump)
        self.assertIn("type_name: Lazy testing provider", dump)
        self.assertIn("state: Valid", dump)
        yaml.safe_load(dump)

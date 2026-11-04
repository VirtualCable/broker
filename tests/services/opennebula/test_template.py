"""
Author: Andres Schumann, aschumann at virtualcable dot es
"""

from unittest import mock

from uds.core import types
from uds.services.OpenNebula import on

from ...utils.helpers import limited_iterator
from ...utils.test import UDSTransactionTestCase
from . import fixtures


def _template(template_id: str, name: str) -> on.types.TemplateType:
    return on.types.TemplateType(template_id, name, 128, None)


class TestOpenNebulaTemplates(UDSTransactionTestCase):
    def test_published_templates_are_not_offered_as_base_template(self) -> None:
        with fixtures.patched_opennebula() as one:
            srvc = fixtures.create_service(fixtures.create_provider())
            pub = fixtures.create_publication(srvc)

            state = pub.publish()
            for _ in limited_iterator(lambda: state == types.states.TaskState.RUNNING, limit=64):
                state = pub.check_state()
            self.assertEqual(state, types.states.TaskState.FINISHED, pub._error_debug_info)

            published_name = one.names[pub.get_template_id()]
            api = mock.Mock()
            api.enum_templates.return_value = [
                _template("1", "empty-machine"),
                _template("2", published_name),
                _template("3", "UDSP_servicepool-1"),  # Published by previous versions
            ]

            names = [t.name for t in on.template.enumerate_templates(api)]

            self.assertEqual(names, ["empty-machine"])

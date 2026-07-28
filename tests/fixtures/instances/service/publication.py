"""
Publication placeholder for the in-tree test fixture.

Required so that ``TestServiceWithCache`` (``uses_cache_l2 = True``) can be
registered; the factory drops L2 services that lack a ``publication_type``.
"""

from uds.core import services


class TestPublication(services.Publication):
    """Empty Publication: no advance/finish hooks needed for the fixture."""

    type_name = "Test Publication"
    type_type = "TestPublication"
    type_description = "Test (and dummy) publication for the in-tree test fixture"
    icon_file = "publication.png"

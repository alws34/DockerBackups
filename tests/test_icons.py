"""Every app and destination needs a logo for the GUI."""

from app.api.icons import icon_url
from app.core.registry import create_default_registry
from app.destinations.registry import ALL_DESTINATIONS


def test_every_worker_and_destination_has_an_icon():
    kinds = [*create_default_registry().all(), *(d.destination_type for d in ALL_DESTINATIONS)]
    missing = [kind for kind in kinds if not icon_url(kind)]
    assert not missing, f"Add app/api/static/icons/<type>.svg for: {missing}"

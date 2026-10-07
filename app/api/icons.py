"""Logos for apps and destinations, shipped with the GUI so it works offline.

Most come from the dashboard-icons project (Apache-2.0, see static/icons/); add
``static/icons/<type>.svg`` (or .png) when adding a worker or destination.
"""

from pathlib import Path

_ICONS = Path(__file__).parent / "static" / "icons"


def icon_url(kind: str) -> str:
    """URL of the logo for a worker or destination type, or "" if there is none."""
    for ext in ("svg", "png"):
        if (_ICONS / f"{kind}.{ext}").is_file():
            return f"/static/icons/{kind}.{ext}"
    return ""

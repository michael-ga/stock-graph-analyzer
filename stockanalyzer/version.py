"""Release number shown in the app: ``MAJOR.MINOR.PATCH``, read from the ``VERSION`` file.

PATCH goes up by one with every push (``python scripts/bump_version.py``). This is
the number the owner tracks; the image revision (``APP_VERSION``, a git SHA) stays
the exact build identifier used by the deploy health check.
"""
from __future__ import annotations

import re
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
_SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def release(path: Path = VERSION_FILE) -> str:
    """The release number, or ``unknown`` when the file is missing or malformed."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return text if _SEMVER.fullmatch(text) else "unknown"


def bump_patch(version: str) -> str:
    """``1.0.7`` -> ``1.0.8``. Raises ValueError for anything but MAJOR.MINOR.PATCH."""
    match = _SEMVER.fullmatch(version.strip())
    if match is None:
        raise ValueError(f"not a MAJOR.MINOR.PATCH version: {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"

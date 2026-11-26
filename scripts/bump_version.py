"""Raise the release number's PATCH by one: 1.0.0 -> 1.0.1.

Run it once per push, before committing:

    python scripts/bump_version.py

Prints the new version. Pass --check to only print the current one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockanalyzer.version import VERSION_FILE, bump_patch, release  # noqa: E402


def main(argv: list[str]) -> int:
    if "--check" in argv:
        print(release())
        return 0
    current = release()
    if current == "unknown":
        print(f"{VERSION_FILE} is missing or not MAJOR.MINOR.PATCH", file=sys.stderr)
        return 1
    new = bump_patch(current)
    VERSION_FILE.write_text(new + "\n", encoding="utf-8")
    print(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

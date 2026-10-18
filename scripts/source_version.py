"""Fingerprint shipped source consistently across Windows, Linux and source ZIPs."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def source_fingerprint(root: Path) -> str:
    paths = [root / name for name in (
        "app.py", "replay_page.py", "ui_common.py", "Dockerfile",
        "requirements.txt", "requirements.lock", "pyproject.toml",
    )]
    for directory in ("stockanalyzer", "scripts", "alembic", "docker"):
        paths.extend(path for path in (root / directory).rglob("*")
                     if path.is_file() and path.suffix in (".py", ".sh"))
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        if path.exists():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
            digest.update(b"\0")
    return digest.hexdigest()[:16]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    fingerprint = source_fingerprint(Path(__file__).resolve().parents[1])
    if args.write:
        args.write.write_text(fingerprint + "\n")
    else:
        print(fingerprint)


if __name__ == "__main__":
    main()

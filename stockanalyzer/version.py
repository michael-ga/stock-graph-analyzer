"""Decision-time build identity, including installations from downloaded ZIPs."""
from __future__ import annotations

import os
from pathlib import Path


def app_version() -> str:
    declared = os.getenv("APP_VERSION", "development")
    try:
        fingerprint = (Path(__file__).resolve().parents[1] / ".source-version").read_text().strip()
    except OSError:
        return declared
    if declared in ("development", "unknown"):
        return f"source-{fingerprint}"
    if declared.endswith("+local"):
        return f"{declared}.{fingerprint}"
    return declared

"""Pure helpers for legacy migration tests."""
from __future__ import annotations
import json
from pathlib import Path


def read_symbol_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    value = json.loads(path.read_text())
    if isinstance(value, dict):
        value = value.get("tickers", value.get("symbols", []))
    if not isinstance(value, list):
        raise ValueError("Legacy symbol file must contain a list")
    return sorted({str(item).strip().upper() for item in value if str(item).strip()})

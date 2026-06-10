"""Provider selection / graceful fallback."""
from __future__ import annotations

from stockanalyzer.data.providers import (
    TwelveDataProvider,
    YFinanceProvider,
    get_provider,
)


def test_twelvedata_without_key_falls_back_to_yfinance(monkeypatch):
    monkeypatch.delenv("TWELVEDATA_KEY", raising=False)
    # Explicitly requesting twelvedata with no key must NOT raise — falls back.
    assert isinstance(get_provider("twelvedata"), YFinanceProvider)
    assert isinstance(get_provider(None), YFinanceProvider)
    assert isinstance(get_provider("yfinance"), YFinanceProvider)


def test_twelvedata_used_when_key_set(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_KEY", "dummy-key")
    assert isinstance(get_provider("twelvedata"), TwelveDataProvider)
    assert isinstance(get_provider(None), TwelveDataProvider)
    # yfinance can still be forced even with a key present.
    assert isinstance(get_provider("yfinance"), YFinanceProvider)

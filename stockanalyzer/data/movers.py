"""Market movers — today's most-active US stocks (Yahoo predefined screener).

`most_active(10)` returns the highest-volume tickers with quote basics so the
dashboard can suggest candidates worth a swing look. Parsing is pure
(`parse_movers`) so it's testable without the network; the fetch degrades to an
empty list on any provider hiccup.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mover:
    symbol: str
    name: str
    price: float | None
    change_pct: float | None
    volume: int | None
    market_cap: float | None


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_movers(payload: dict | None, count: int = 10) -> list[Mover]:
    """Extract Movers from a Yahoo screener payload ({'quotes': [...]})."""
    out: list[Mover] = []
    for q in (payload or {}).get("quotes", []):
        if not isinstance(q, dict):
            continue
        sym = str(q.get("symbol") or "").strip().upper()
        if not sym:
            continue
        out.append(Mover(
            symbol=sym,
            name=str(q.get("shortName") or q.get("longName") or ""),
            price=_f(q.get("regularMarketPrice")),
            change_pct=_f(q.get("regularMarketChangePercent")),
            volume=_i(q.get("regularMarketVolume")),
            market_cap=_f(q.get("marketCap")),
        ))
        if len(out) >= count:
            break
    return out


def most_active(count: int = 10) -> list[Mover]:
    """Today's most-active US stocks by volume. [] when the screener is down."""
    try:
        import yfinance as yf

        payload = yf.screen("most_actives", count=count)
    except Exception:
        return []
    return parse_movers(payload, count)

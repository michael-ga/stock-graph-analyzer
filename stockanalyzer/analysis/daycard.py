"""Day-trader snapshot: the few numbers a day trader needs for a quick decision,
computed from data the dashboard already fetched (no extra network) so the card
renders instantly.

What it answers, all relative to the *current* price:
  • Session range — today's high/low *from trade start* (the latest day's bars).
  • Battle zones — the price nodes where the most volume changed hands, i.e.
    where buyers and sellers fought hardest (a one-bin volume-by-price profile).
  • Day bias — volume-weighted % bullish vs bearish across the session.
  • Support / resistance — historical swing levels (from every timeframe's
    ``rep.levels``) merged with the session extremes, split by side of price.
  • Next target — the nearest resistance above, and how far (%) price must travel.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.market_session import is_intraday
from ..data.schema import Timeframe


@dataclass
class BattleZone:
    price: float
    volume_pct: float          # share of session volume that traded at this node (0..100)


@dataclass
class DayCard:
    current_price: float
    session_high: float | None
    session_low: float | None
    session_range_pct: float | None        # (high-low)/low * 100
    range_position_pct: float | None       # where price sits in today's range (0=low,100=high)
    day_change_pct: float | None           # session open → current price, %
    bull_pct: float | None                 # volume-weighted % of the session that was bullish
    bear_pct: float | None
    bias_label: str                        # "Bullish" / "Bearish" / "Balanced"
    supports: list[float] = field(default_factory=list)     # below price, nearest first
    resistances: list[float] = field(default_factory=list)  # above price, nearest first
    battle_zones: list[BattleZone] = field(default_factory=list)
    next_target: float | None = None       # nearest resistance above
    next_target_pct: float | None = None   # % from current price to next_target
    next_support: float | None = None      # nearest support below
    next_support_pct: float | None = None
    n_session_bars: int = 0
    intraday: bool = False                 # whether the session frame is true intraday


# Frames preferred for the intraday session view, shortest first.
_INTRADAY_ORDER = (Timeframe.D1, Timeframe.D5, Timeframe.M1)


def _current_price(reports: dict) -> float | None:
    for tf in Timeframe:
        rep = reports.get(tf)
        if rep is not None:
            lc = rep.meta.get("last_close")
            if lc:
                return float(lc)
    return None


def _intraday_frame(reports: dict) -> pd.DataFrame | None:
    """Shortest frame that is genuinely intraday; falls back to the shortest frame
    available so the card still degrades gracefully on daily-only data."""
    for tf in _INTRADAY_ORDER:
        rep = reports.get(tf)
        if rep is not None and is_intraday(rep.df):
            return rep.df
    for tf in _INTRADAY_ORDER:
        rep = reports.get(tf)
        if rep is not None:
            return rep.df
    return None


def _session_bars(df: pd.DataFrame) -> pd.DataFrame:
    """The latest trading day's bars only — 'from trade start'."""
    if not isinstance(df.index, pd.DatetimeIndex) or df.empty:
        return df
    last_day = df.index[-1].normalize()
    day = df[df.index.normalize() == last_day]
    return day if len(day) else df


def _bull_bear(day: pd.DataFrame) -> tuple[float, float]:
    """Volume-weighted split of the session into bullish vs bearish bars. Falls
    back to a plain bar count when volume is missing."""
    up = day["close"] >= day["open"]
    vol = day["volume"].fillna(0.0)
    total = float(vol.sum())
    if total > 0:
        bull = float(vol[up].sum()) / total * 100.0
    else:
        bull = float(up.mean()) * 100.0 if len(day) else 50.0
    bull = round(max(0.0, min(100.0, bull)), 1)
    return bull, round(100.0 - bull, 1)


def _battle_zones(day: pd.DataFrame, n_bins: int = 12, top: int = 2) -> list[BattleZone]:
    """One-pass volume-by-price profile: bucket each bar's volume at its typical
    price, then surface the heaviest buckets — the prices where the fight was
    strongest (the point of control and runner-up node)."""
    lo = float(day["low"].min())
    hi = float(day["high"].max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return []
    centers = (np.linspace(lo, hi, n_bins + 1)[:-1] + np.linspace(lo, hi, n_bins + 1)[1:]) / 2
    typical = (day["high"] + day["low"] + day["close"]) / 3.0
    vol = day["volume"].fillna(0.0)
    weights = vol if float(vol.sum()) > 0 else pd.Series(1.0, index=day.index)

    bucket = np.zeros(n_bins)
    for tp, w in zip(typical.to_numpy(), weights.to_numpy()):
        b = int((tp - lo) / (hi - lo) * n_bins)
        bucket[min(max(b, 0), n_bins - 1)] += w
    denom = bucket.sum()
    if denom <= 0:
        return []

    order = sorted(np.argsort(bucket)[-top:], key=lambda i: -bucket[i])
    return [BattleZone(round(float(centers[i]), 2), round(bucket[i] / denom * 100.0, 1))
            for i in order if bucket[i] > 0]


def _dedup(prices: list[float], tol: float) -> list[float]:
    """Merge near-identical level prices (within tol) into one."""
    out: list[float] = []
    for p in sorted(prices):
        if out and abs(p - out[-1]) <= tol:
            out[-1] = (out[-1] + p) / 2.0
        else:
            out.append(p)
    return out


def _levels(reports: dict, price: float,
            session_high: float | None, session_low: float | None) -> tuple[list[float], list[float]]:
    """Every timeframe's swing levels + the session extremes, deduped and split
    into supports (below) and resistances (above) relative to the current price."""
    cands: list[float] = []
    for rep in reports.values():
        cands += [lv.price for lv in getattr(rep, "levels", [])]
    if session_high:
        cands.append(session_high)
    if session_low:
        cands.append(session_low)

    tol = max(price * 0.004, 0.01)
    deduped = _dedup([c for c in cands if c and c > 0], tol)

    resistances = sorted(round(p, 2) for p in deduped if p > price * 1.0005)
    supports = sorted((round(p, 2) for p in deduped if p < price * 0.9995), reverse=True)
    return supports, resistances


def _bias_label(bull_pct: float | None, day_change_pct: float | None) -> str:
    if bull_pct is not None:
        if bull_pct >= 58:
            return "Bullish"
        if bull_pct <= 42:
            return "Bearish"
        return "Balanced"
    if day_change_pct is not None:
        return "Bullish" if day_change_pct > 0 else "Bearish" if day_change_pct < 0 else "Balanced"
    return "—"


def build_day_card(reports: dict, current_price: float | None = None) -> DayCard | None:
    """Assemble the day-trader card from already-computed timeframe reports.

    Returns ``None`` only when there is no usable price/level data at all.
    """
    if not reports:
        return None
    price = current_price if (current_price and current_price > 0) else _current_price(reports)
    if not price or price <= 0:
        return None

    intr = _intraday_frame(reports)
    session_high = session_low = session_range_pct = range_position_pct = None
    day_change_pct = bull_pct = bear_pct = None
    battle: list[BattleZone] = []
    n_bars = 0
    intraday = False

    if intr is not None and len(intr):
        day = _session_bars(intr)
        if len(day):
            intraday = is_intraday(intr)
            session_high = round(float(day["high"].max()), 2)
            session_low = round(float(day["low"].min()), 2)
            if session_low > 0:
                session_range_pct = round((session_high / session_low - 1) * 100, 2)
            if session_high > session_low:
                pos = (price - session_low) / (session_high - session_low) * 100
                range_position_pct = round(max(0.0, min(100.0, pos)), 1)
            open_px = float(day["open"].iloc[0])
            if open_px > 0:
                day_change_pct = round((price / open_px - 1) * 100, 2)
            bull_pct, bear_pct = _bull_bear(day)
            battle = _battle_zones(day)
            n_bars = len(day)

    supports, resistances = _levels(reports, price, session_high, session_low)
    next_target = resistances[0] if resistances else None
    next_support = supports[0] if supports else None

    return DayCard(
        current_price=round(price, 2),
        session_high=session_high,
        session_low=session_low,
        session_range_pct=session_range_pct,
        range_position_pct=range_position_pct,
        day_change_pct=day_change_pct,
        bull_pct=bull_pct,
        bear_pct=bear_pct,
        bias_label=_bias_label(bull_pct, day_change_pct),
        supports=supports[:4],
        resistances=resistances[:4],
        battle_zones=battle,
        next_target=next_target,
        next_target_pct=(round((next_target / price - 1) * 100, 2) if next_target else None),
        next_support=next_support,
        next_support_pct=(round((next_support / price - 1) * 100, 2) if next_support else None),
        n_session_bars=n_bars,
        intraday=intraday,
    )

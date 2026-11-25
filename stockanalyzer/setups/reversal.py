"""Intraday reversal-at-support day-trade decisions — pure and testable.

A *family* of "fast reversal" setups: price pulls back into a support level inside
the day's range and turns up; take a quick ~3% aim (capped just under the nearest
resistance) with a hard stop just below support. Written the same way as
``setups/orb.py`` — a pure as-of function the live radar and
``daytrade_backtest.py`` share verbatim (no drift), plus a plain config list
(``REVERSAL_VARIANTS``) that BOTH the live bots and the backtest ``--variants``
sweep iterate. Everything is a pure function of the day card + data + an explicit
as-of timestamp — no Streamlit state, no ``now()`` — so it replays bar-by-bar.

Two risk axes give the starter family; the trade book (idea/cohort level) then
proves which wins — nothing here is re-weighted or culled on one session:
  • entry_mode: "touch"     — aggressive: fire on the first tag of support.
                "confirmed" — conservative: wait for a bullish reclaim of support.
  • trend_mode: "with_trend"     — dip-buy only when the daily trend is up.
                "strong_support" — allow any trend, but demand high-confidence
                                    support (a volume battle-zone / stacked levels).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..data.market_session import _to_et
from ..session import market_phase
from ..analysis.daycard import DayCard
from ..analysis.signals import Direction

# Thresholds — single source of truth (app + backtest import these).
REV_TARGET_PCT   = 0.03     # profit aim: ~3% above entry
REV_MIN_AIM      = 0.025    # go/no-go: skip if the entry→target aim < 2.5%
SUPPORT_BAND_PCT = 0.004    # "at support" = within 0.4% of a support level
RANGE_POS_MAX    = 35.0     # only buy in the lower 35% of the day's range
STOP_BUFFER      = 0.002    # hard stop = support × (1 − 0.2%)
STRONG_CONF_MIN  = 2        # "strong support" = ≥2 independent confirmations
GAP_DOWN_PCT     = -0.02    # a gap-down ≤ −2% is a knife: with_trend stands aside
CONFIRM_MIN_PCT  = 0.001    # "confirmed" needs a close ≥0.1% back above support


@dataclass(frozen=True)
class ReversalConfig:
    """One row of the variant matrix — the knobs the family sweeps over."""
    name: str
    trend_mode: str      # "with_trend" | "strong_support"
    entry_mode: str      # "touch" | "confirmed"
    # Since 2026-11-10 (the *-vol rows only; the defaults are the original rule):
    stop_mode: str = "support"           # "support" | "vol" (also ≥ vol_mult × bar range)
    vol_mult: float = 1.5
    min_rr: float | None = None          # skip when the R:R left is below this
    last_entry: str | None = None        # "HH:MM": no entry after this bar


@dataclass(frozen=True)
class ReversalDecision:
    """Every gate's outcome is visible so callers can explain why it did/didn't
    fire (mirrors orb.GapGoDecision)."""
    fired: bool
    reason: str
    variant: str
    at_support: bool
    support_conf: int
    trend_ok: bool
    reversed_up: bool
    aim_ok: bool
    gap_pct: float | None = None
    support: float | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    rr: float | None = None


# The live variants. Extend by adding a row; both the live bots and the backtest
# sweep pick it up automatically (the ORB no-drift rule). Started as 2 risk axes x 2
# = 4 variants; on 2026-11-02 rev-trend-touch (−11.8 R, 0% win in Sep–Oct) and
# rev-strong-confirm (sign flips by regime) were retired on 59 sessions x 12 tickers
# (algolab/screens/reversal_2026_11_02, LEARNINGS 2026-11-02).
#
# The two *-vol rows (pre-registered 2026-11-10, H18,
# algolab/screens/daytrade_variants_2026_11_10/PREREGISTRATION.md): the original stop
# sat 0.4% under support, inside one 5-minute bar of noise (0.6 bars median), and 37%
# of the stopped-out touches later reached the full target. Each is its twin above
# with the stop at least 1.5 median bar ranges away, R:R ≥ 1.5 and no entry after
# 11:00. They trade live since 2026-11-25 to collect data (H18's keep/kill rule:
# > 0 net of cost at ≥ 30 dates after 2026-10-30), not because they passed it.
REVERSAL_VARIANTS: list[ReversalConfig] = [
    ReversalConfig("rev-trend-confirm",  "with_trend",     "confirmed"),
    ReversalConfig("rev-strong-touch",   "strong_support", "touch"),
    ReversalConfig("rev-trend-vol",      "with_trend",     "confirmed",
                   stop_mode="vol", min_rr=1.5, last_entry="11:00"),
    ReversalConfig("rev-strong-vol",     "strong_support", "touch",
                   stop_mode="vol", min_rr=1.5, last_entry="11:00"),
]

# Replay/A-B-only rows that no live bot trades. Empty since the *-vol rows went live.
REVERSAL_CANDIDATES: list[ReversalConfig] = []


def median_bar_range(intraday_df: pd.DataFrame | None) -> float | None:
    """Median high-low of the frame's regular-session bars (the noise of one bar)."""
    if intraday_df is None or not len(intraday_df):
        return None
    idx = pd.DatetimeIndex(intraday_df.index)
    wall = idx.tz_convert("America/New_York") if idx.tz is not None else idx
    hhmm = wall.strftime("%H:%M")
    reg = intraday_df[(hhmm >= "09:30") & (hhmm < "16:00")]
    if not len(reg):
        return None
    med = float((reg["high"] - reg["low"]).median())
    return med if med > 0 else None


def _support_candidates(card: DayCard) -> list[float]:
    """Every price that can act as intraday support: mapped support levels, the
    session low, and the volume battle-zone nodes (where the fight was heaviest)."""
    cands = list(card.supports or [])
    if card.session_low is not None:
        cands.append(card.session_low)
    cands += [bz.price for bz in (card.battle_zones or [])]
    return sorted({round(c, 4) for c in cands if c and c > 0})


def _nearest_support(card: DayCard, price: float) -> float | None:
    """The nearest support at/just-below price (a small band above price is allowed
    so an exact touch of a dropped-into-the-deadband level still registers)."""
    below = [c for c in _support_candidates(card) if c <= price * (1 + SUPPORT_BAND_PCT)]
    return max(below) if below else None


def _support_confidence(card: DayCard, support: float | None, band: float) -> int:
    """How many independent things back this support within ``band``: stacked
    mapped levels + a volume battle-zone node + the session low. Higher = stronger."""
    if support is None:
        return 0
    conf = sum(1 for s in (card.supports or []) if abs(s - support) <= band)
    conf += sum(1 for bz in (card.battle_zones or []) if abs(bz.price - support) <= band)
    if card.session_low is not None and abs(card.session_low - support) <= band:
        conf += 1
    return conf


def _reversed_up(intraday_df: pd.DataFrame | None, support: float, band: float) -> bool:
    """The latest session bar tagged support (its low reached the band) and closed
    back above it as an up-bar — a bullish reversal print."""
    if intraday_df is None or intraday_df.empty:
        return False
    last = intraday_df.iloc[-1]
    tagged = float(last["low"]) <= support + band
    reclaimed = float(last["close"]) >= support * (1 + CONFIRM_MIN_PCT)
    bullish = float(last["close"]) >= float(last["open"])
    return bool(tagged and reclaimed and bullish)


def reversal_signal(card: DayCard | None, intraday_df: pd.DataFrame | None,
                    price: float | None, higher_trend: Direction | None,
                    prev_close: float | None, asof_ts,
                    cfg: ReversalConfig) -> ReversalDecision:
    """Decide whether ``cfg``'s reversal-at-support entry fires right now.

    Guard order (each returns a plain-English reason): at support in the lower day
    range → trend/strength gate → reversal print (confirmed mode) → non-degenerate
    geometry → the ~3% go/no-go aim (capped under the nearest resistance).
    """
    def _no(reason: str, *, at_support=False, support_conf=0, trend_ok=False,
            reversed_up=False, aim_ok=False, gap_pct=None, support=None):
        return ReversalDecision(False, reason, cfg.name, at_support, support_conf,
                                trend_ok, reversed_up, aim_ok, gap_pct=gap_pct,
                                support=support)

    if card is None or price is None or price <= 0:
        return _no("no day card / price")
    if market_phase(asof_ts) == "opening_range":
        return _no("opening-range freeze (first 15 min)")
    if cfg.last_entry and _to_et(asof_ts).strftime("%H:%M") > cfg.last_entry:
        return _no(f"past the last entry bar ({cfg.last_entry})")

    support = _nearest_support(card, price)
    band = (support or price) * SUPPORT_BAND_PCT
    rng_ok = card.range_position_pct is None or card.range_position_pct <= RANGE_POS_MAX
    at_support = bool(support is not None
                      and abs(price - support) <= band and rng_ok)

    session_open = (price / (1 + card.day_change_pct / 100.0)
                    if card.day_change_pct is not None else None)
    gap_pct = (session_open / prev_close - 1) if (session_open and prev_close) else None
    conf = _support_confidence(card, support, band)

    if not at_support:
        why = ("no support at/below price in the lower day range" if support is None
               else f"price ${price:.2f} not within {SUPPORT_BAND_PCT*100:.1f}% of "
                    f"support ${support:.2f} (or too high in today's range)")
        return _no(why, support=support, support_conf=conf, gap_pct=gap_pct)

    if cfg.trend_mode == "with_trend":
        gap_down = gap_pct is not None and gap_pct <= GAP_DOWN_PCT
        trend_ok = (higher_trend == Direction.BULL) and not gap_down
        if higher_trend != Direction.BULL:
            trend_why = "daily trend not up"
        elif gap_down:                 # don't dip-buy into a gap-down knife
            trend_why = f"gap-down {gap_pct*100:.1f}% — standing aside"
        else:
            trend_why = ""
    else:  # strong_support: any trend, but the level must be proven
        # The knife guard applies here too (2026-11-02): a proven level does not make
        # a gap-down ≤ −2% safe to dip-buy (screen + holdout in algolab/LEARNINGS.md).
        gap_down = gap_pct is not None and gap_pct <= GAP_DOWN_PCT
        trend_ok = conf >= STRONG_CONF_MIN and not gap_down
        trend_why = (f"gap-down {gap_pct*100:.1f}% — standing aside" if gap_down
                     else f"support only {conf} confirmation(s) (need ≥{STRONG_CONF_MIN})")
    if not trend_ok:
        return _no(trend_why, at_support=True, support=support, support_conf=conf,
                   gap_pct=gap_pct)

    if cfg.entry_mode == "confirmed":
        reversed_up = _reversed_up(intraday_df, support, band)
        if not reversed_up:
            return _no("no reversal print yet — need a bullish reclaim of support",
                       at_support=True, support=support, support_conf=conf,
                       trend_ok=True, gap_pct=gap_pct)
    else:  # touch: the first tag is the trigger
        reversed_up = True

    entry = float(price)
    stop = round(support * (1 - STOP_BUFFER), 4)
    if cfg.stop_mode == "vol":
        rng = median_bar_range(intraday_df)
        if rng is None:
            return _no("no bar range for a volatility stop", at_support=True,
                       support=support, support_conf=conf, trend_ok=True,
                       reversed_up=reversed_up, gap_pct=gap_pct)
        stop = round(min(stop, entry - cfg.vol_mult * rng), 4)
    if stop >= entry:
        return _no("degenerate geometry — stop ≥ entry", at_support=True,
                   support=support, support_conf=conf, trend_ok=True,
                   reversed_up=reversed_up, gap_pct=gap_pct)

    raw_target = entry * (1 + REV_TARGET_PCT)
    res_cap = card.next_target * 0.995 if card.next_target else None
    target = min(raw_target, res_cap) if res_cap else raw_target
    aim = target / entry - 1
    if aim < REV_MIN_AIM:
        return _no(f"aim {aim*100:.1f}% < {REV_MIN_AIM*100:.1f}% — resistance too "
                   f"close to run a clean {REV_TARGET_PCT*100:.0f}%",
                   at_support=True, support=support, support_conf=conf,
                   trend_ok=True, reversed_up=reversed_up, gap_pct=gap_pct)

    target = round(target, 4)
    rr = round((target - entry) / (entry - stop), 2)
    if cfg.min_rr is not None and rr < cfg.min_rr:
        return _no(f"R:R {rr:.2f} < {cfg.min_rr:g} with the volatility stop",
                   at_support=True, support=support, support_conf=conf, trend_ok=True,
                   reversed_up=reversed_up, aim_ok=True, gap_pct=gap_pct)
    return ReversalDecision(
        True,
        f"reversal at ${support:.2f} ({conf} conf) — aim {aim*100:.1f}% to ${target:.2f}",
        cfg.name, True, conf, True, reversed_up, True, gap_pct=gap_pct,
        support=support, entry=entry, stop=stop, target=target, rr=rr)

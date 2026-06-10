"""Swing trade plan (rule-based, no AI) — honest-target edition.

Calibrated against three real failure cases (INTC / MSFT / NOK):

- **Honest targets.** A target is the next mapped resistance (sold just under the
  wall), capped by the volatility budget `dailyATR × √horizon_days` — never a
  fixed % floor inflated through ceilings.
- **Plan kinds.** `immediate` (room to the wall), `breakout_wait` (no room — wait
  for a close above the wall, target the next one), `no_trade` (chop / nothing
  worth doing). Each carries a one-line `guidance` sentence.
- **Vol-aware stops.** A swing stop must survive overnight noise: at least
  `stop_atr_mult × dailyATR` (daily bars), not the intraday chart's ATR.
- **Setup tiers.** With-trend setups carry full weight; counter-trend setups
  (trend-change-early, oversold bounce) are first-wall scalps at half weight and
  can't reach GO on the standard pace against a sub-neutral investor read.
- **Calibrated 0–100 score** with a visible checklist: multi-frame MA/MACD (5D /
  6M / 1Y — the 6M frame has real daily-bar history; rows that can't be computed
  show "n/a" at half denominator weight instead of silently vanishing), engine
  bias agreement, RSI sanity, post-gap chase, overextension, strategy conflict,
  plus info rows (earnings proximity, Street target, fast-mover).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..analysis.engine import TimeframeReport
from ..analysis.signals import Direction
from ..data.schema import Timeframe
from ..strategy import SWING_PACE, PaceTuning, SwingPace
from .usecase import UseCase

_MIN_RR = 2.0
_BREAKOUT_ROOM_FACTOR = 0.75   # breakout leg may be slightly under min_move (trigger-confirmed)
_CHASE_PCT = 5.0               # a ≥5% daily move in the last 3 daily bars = chase risk
_EXTENSION_PCT = 10.0          # price >10% from the 6M sma20 = rubber band stretched

# Signal names that confirm a bullish swing entry.
_BULL_CONFIRM = {"hammer", "bullish_engulfing", "double_bottom",
                 "rsi_bullish_divergence", "inverse_head_and_shoulders",
                 "macd_bull_cross", "premarket_gap_up", "stoch_bull"}
_BEAR_CONFIRM = {"shooting_star", "bearish_engulfing", "double_top",
                 "rsi_bearish_divergence", "head_and_shoulders",
                 "macd_bear_cross", "premarket_gap_down", "stoch_bear"}
_FAST = {"premarket_gap_up", "premarket_gap_down", "volume_spike",
         "afterhours_move_up", "afterhours_move_down"}

# Setup tiers: with-trend setups are full weight; counter-trend ones are
# bottom-fishing — legitimate only as first-wall scalps with extra caution.
_COUNTERTREND_SETUPS = {"Trend-change reversal (early)", "Oversold reversal at support",
                        "Trend-change reversal (down)", "Overbought reversal at resistance"}


@dataclass(frozen=True)
class SwingCheck:
    """One row in the swing-score breakdown (plain-English guide).

    `na=True` means the input couldn't be computed (not enough history): the row
    contributes half its weight to the denominator and zero earned — visible
    honesty instead of silent score inflation. `weight=0` rows are informational.
    """
    name: str
    ok: bool
    detail: str
    weight: int
    na: bool = False


@dataclass
class SwingPlan:
    setup: str
    bias: Direction
    entry: float
    entry_note: str
    stop: float
    stop_pct: float                 # signed distance to stop (%)
    target1: float
    target1_pct: float              # signed distance to target (%)
    target2: float | None
    rr: float                       # reward:risk for target1
    risk_pct: float                 # |stop distance| %
    go: bool
    light: str                      # "go" / "forming" / "no"
    confidence: str
    reasons: list[str] = field(default_factory=list)
    horizon: str = "days to ~2 weeks"
    fast_mover: bool = False
    score: int = 0                  # 0..100 — calibrated quality score
    score_label: str = ""           # Strong / Good / Weak — wait / Avoid
    checks: list[SwingCheck] = field(default_factory=list)
    kind: str = "immediate"         # immediate / breakout_wait / no_trade
    guidance: str = ""              # the one-line expert sentence
    trigger: float | None = None    # breakout_wait: the wall a close must clear
    daily_atr_pct: float = 0.0      # daily volatility used for risk sizing
    atr_source: str = ""


def _last(df, col: str, default: float | None = None) -> float | None:
    if col not in df:
        return default
    s = df[col].dropna()
    return float(s.iloc[-1]) if not s.empty else default


def _rising(df, col: str, lookback: int = 10) -> bool:
    s = df[col].dropna()
    return len(s) > lookback and s.iloc[-1] > s.iloc[-lookback]


# --------------------------------------------------------------------------- #
# Daily volatility — the risk yardstick for every horizon.
# --------------------------------------------------------------------------- #
_DAILY_FRAMES = (Timeframe.M6, Timeframe.M1, Timeframe.YTD, Timeframe.Y1)


def _daily_atr(all_reports, df, price: float) -> tuple[float, str]:
    """(daily ATR as fraction of price, source label). Prefers the 6M frame
    (126 daily bars — stable ATR(14)); falls back through other daily frames,
    then the decision chart itself (intraday — flagged as such)."""
    if all_reports:
        for tf in _DAILY_FRAMES:
            rep = all_reports.get(tf)
            if rep is None:
                continue
            atr = _last(rep.df, "atr")
            ref = rep.meta.get("last_close") or price
            if atr and ref:
                return atr / ref, f"{tf.value} daily bars"
    atr = _last(df, "atr")
    if atr and price:
        return atr / price, "decision chart (intraday — may understate)"
    return 0.02, "default 2%"


# --------------------------------------------------------------------------- #
# Honest target geometry (long side; mirrored for shorts).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Geometry:
    kind: str                  # immediate / breakout_wait / no_trade
    entry: float
    stop: float
    target: float
    trigger: float | None      # breakout_wait: wall to close beyond
    note: str                  # extra guidance fragment


def _long_geometry(price, walls, supports, atr_abs, tuning: PaceTuning,
                   countertrend: bool) -> _Geometry:
    budget = atr_abs / price * math.sqrt(tuning.budget_days)     # fraction
    cap = min(tuning.target_cap, budget)
    buffer = max(0.001 * price, 0.02 * atr_abs)
    s1 = supports[0] if supports else None

    def _stop_for(entry: float, wall_below: float | None) -> float:
        # Pure volatility stop: sized to survive a day of noise. Nearby structure
        # is informational; a tighter "structural" stop inside the noise band is
        # exactly the NOK failure mode.
        del wall_below
        return max(0.01, entry - tuning.stop_atr_mult * atr_abs)

    if not walls:                                        # blue sky — volatility budget
        entry = price
        return _Geometry("immediate", entry, _stop_for(entry, s1),
                         entry * (1 + cap), None,
                         "no mapped ceiling above — target set from the volatility budget")

    r1 = walls[0]
    room1 = r1 / price - 1
    if room1 >= tuning.min_move:                         # honest room to the first wall
        entry = price
        target = min(r1 * 0.995, entry * (1 + cap))
        return _Geometry("immediate", entry, _stop_for(entry, s1), target, None,
                         f"sell just under the ${r1:.2f} wall")

    if countertrend:                                     # bounce scalps don't get promoted
        return _Geometry("no_trade", price, _stop_for(price, s1),
                         max(r1 * 0.995, price), None,
                         f"bounce room to ${r1:.2f} is only {room1*100:+.1f}% — too small")

    # No room — scan the ladder for the first viable breakout leg.
    for i in range(min(len(walls), 3)):
        trigger_wall = walls[i]
        entry = trigger_wall + buffer
        nxt = walls[i + 1] if i + 1 < len(walls) else None
        target = (min(nxt * 0.995, entry * (1 + cap)) if nxt is not None
                  else entry * (1 + cap))
        room = target / entry - 1
        if room >= _BREAKOUT_ROOM_FACTOR * tuning.min_move:
            stop = min(trigger_wall * 0.998, entry - tuning.stop_atr_mult * atr_abs)
            return _Geometry("breakout_wait", entry, max(0.01, stop), target,
                             trigger_wall,
                             f"wait for a close above ${trigger_wall:.2f}")
    return _Geometry("no_trade", price, _stop_for(price, s1),
                     max(r1 * 0.995, price), None,
                     "walls are stacked too close — chop zone")


def _short_geometry(price, walls, supports, atr_abs, tuning: PaceTuning,
                    countertrend: bool) -> _Geometry:
    budget = atr_abs / price * math.sqrt(tuning.budget_days)
    cap = min(tuning.target_cap, budget)
    buffer = max(0.001 * price, 0.02 * atr_abs)
    r1 = walls[0] if walls else None                     # resistance above (stop side)

    def _stop_for(entry: float, wall_above: float | None) -> float:
        del wall_above
        return entry + tuning.stop_atr_mult * atr_abs    # pure volatility stop

    if not supports:
        entry = price
        return _Geometry("immediate", entry, _stop_for(entry, r1),
                         entry * (1 - cap), None,
                         "no mapped floor below — target set from the volatility budget")

    s1 = supports[0]
    room1 = 1 - s1 / price
    if room1 >= tuning.min_move:
        entry = price
        target = max(s1 * 1.005, entry * (1 - cap))
        return _Geometry("immediate", entry, _stop_for(entry, r1), target, None,
                         f"cover just above the ${s1:.2f} floor")

    if countertrend:
        return _Geometry("no_trade", price, _stop_for(price, r1),
                         min(s1 * 1.005, price), None,
                         f"drop room to ${s1:.2f} is only {room1*100:.1f}% — too small")

    for i in range(min(len(supports), 3)):
        trigger_wall = supports[i]
        entry = trigger_wall - buffer
        nxt = supports[i + 1] if i + 1 < len(supports) else None
        target = (max(nxt * 1.005, entry * (1 - cap)) if nxt is not None
                  else entry * (1 - cap))
        room = 1 - target / entry
        if room >= _BREAKOUT_ROOM_FACTOR * tuning.min_move:
            stop = max(trigger_wall * 1.002, entry + tuning.stop_atr_mult * atr_abs)
            return _Geometry("breakout_wait", entry, stop, target, trigger_wall,
                             f"wait for a close below ${trigger_wall:.2f}")
    return _Geometry("no_trade", price, _stop_for(price, r1),
                     min(s1 * 1.005, price), None,
                     "floors are stacked too close — chop zone")


# --------------------------------------------------------------------------- #
# Calibrated score.
# --------------------------------------------------------------------------- #
# Backtest-calibrated (120 points / 12 tickers): pure trend-alignment weighting
# was ANTI-predictive (low-score oversold points won 64% vs 43% for high scores),
# so alignment carries less weight and "rebound value" (buying low in the range
# with a soft RSI) earns points of its own.
_TF_CHECKS = (   # (timeframe, label, MA column, MA weight, MACD weight)
    (Timeframe.D5, "5D", "sma20", 4, 3),
    (Timeframe.M6, "6M", "sma50", 5, 4),
    (Timeframe.Y1, "1Y", "sma200", 4, 2),
)
_BIAS_FRAMES = (Timeframe.D1, Timeframe.D5, Timeframe.M1)


def _swing_score(bias: Direction, setup: str | None, setup_present: bool,
                 countertrend: bool, rr: float, geom: _Geometry, walls, supports,
                 fast: bool, df, all_reports, daily_atr_pct: float,
                 context: dict) -> tuple[int, str, list[SwingCheck]]:
    bull = bias == Direction.BULL
    side = "above" if bull else "below"
    checks: list[SwingCheck] = []

    setup_w = 8 if countertrend else 15
    setup_detail = (f"{setup} — countertrend, scalp tier" if (setup_present and countertrend)
                    else (setup or "no clean entry pattern right now"))
    checks.append(SwingCheck("Valid setup found", setup_present, setup_detail, setup_w))
    checks.append(SwingCheck("Reward ≥ 2× risk (honest target, vol-aware stop)",
                             rr >= _MIN_RR, f"R:R {rr:.1f}:1", 12))

    # Clear path: no mapped level strictly between entry and target.
    lo, hi = sorted((geom.entry, geom.target))
    between = [w for w in (list(walls) + list(supports)) if lo * 1.001 < w < hi * 0.999]
    checks.append(SwingCheck("Clear path — no walls between entry and target",
                             not between,
                             ("clear air" if not between else
                              ", ".join(f"${w:.2f}" for w in between[:3]) + " in the way"),
                             8))

    # Rebound value — the backtest's winning cluster: entries taken low, not chased.
    rsi_now = _last(df, "rsi")
    if rsi_now is not None:
        ok = rsi_now <= 45 if bull else rsi_now >= 55
        checks.append(SwingCheck(
            "Buying low — soft RSI" if bull else "Selling high — firm RSI", ok,
            f"RSI {rsi_now:.0f} ({'≤45 earns it' if bull else '≥55 earns it'})", 9))
    win = df.tail(60)
    if len(win) >= 20:
        lo60 = float(win["low"].min())
        hi60 = float(win["high"].max())
        if hi60 > lo60:
            pos = (geom.entry - lo60) / (hi60 - lo60)
            ok = pos <= 0.5 if bull else pos >= 0.5
            checks.append(SwingCheck(
                "Room to recover — lower half of the 60-bar range" if bull else
                "Room to fall — upper half of the 60-bar range", ok,
                f"price sits at {pos*100:.0f}% of the range", 9))

    # Multi-frame MA + MACD (6M carries the daily-bar history; n/a rows stay visible).
    def _frame_rows(rep_df, label: str, ma_col: str, w_ma: int, w_macd: int):
        price_f = _last(rep_df, "close")
        ma = _last(rep_df, ma_col)
        if price_f is None or ma is None:
            checks.append(SwingCheck(f"{label}: price {side} {ma_col.upper()}", False,
                                     "n/a — not enough history", w_ma, na=True))
        else:
            ok = price_f >= ma if bull else price_f <= ma
            checks.append(SwingCheck(f"{label}: price {side} {ma_col.upper()}", ok,
                                     f"{price_f:.2f} vs {ma:.2f}", w_ma))
        macd_v, sig = _last(rep_df, "macd"), _last(rep_df, "macd_signal")
        if macd_v is None or sig is None:
            checks.append(SwingCheck(f"{label}: MACD {side} signal", False,
                                     "n/a — not enough history", w_macd, na=True))
        else:
            ok = macd_v >= sig if bull else macd_v <= sig
            checks.append(SwingCheck(f"{label}: MACD {side} signal", ok,
                                     f"{macd_v:.3f} vs {sig:.3f}", w_macd))

    if all_reports:
        for tf, label, ma_col, w_ma, w_macd in _TF_CHECKS:
            rep = all_reports.get(tf)
            if rep is None and tf == Timeframe.M6:       # fall back to 1M if 6M absent
                rep = all_reports.get(Timeframe.M1)
                label = "1M"
            if rep is not None:
                _frame_rows(rep.df, label, ma_col, w_ma, w_macd)
    else:
        _frame_rows(df, "chart", "sma20", 12, 10)

    # Engine bias agreement — brings RSI/candles/volume in via the engine's own read.
    if all_reports:
        vals = []
        for tf in _BIAS_FRAMES:
            rep = all_reports.get(tf)
            if rep is not None:
                vals.append((tf.value, rep.bias_score))
        if vals:
            aligned = sum(1 for _, b in vals if (b > 0.1 if bull else b < -0.1))
            need = 2 if len(vals) >= 2 else 1
            checks.append(SwingCheck(
                "Engine bias agrees (1D/5D/1M)", aligned >= need,
                " · ".join(f"{t} {b:+.2f}" for t, b in vals), 6))

    # RSI sanity — don't buy overbought / short oversold.
    rsi_dec = _last(df, "rsi")
    rsi_m1 = None
    if all_reports and all_reports.get(Timeframe.M1) is not None:
        rsi_m1 = _last(all_reports[Timeframe.M1].df, "rsi")
    if rsi_dec is not None:
        ok = (rsi_dec <= 70 and (rsi_m1 is None or rsi_m1 <= 75)) if bull else \
             (rsi_dec >= 30 and (rsi_m1 is None or rsi_m1 >= 25))
        detail = f"decision RSI {rsi_dec:.0f}" + (f" · 1M RSI {rsi_m1:.0f}" if rsi_m1 else "")
        checks.append(SwingCheck("RSI not at an extreme", ok, detail, 4))

    # No chase: a ≥5% daily move in the last 3 daily bars = post-news spike risk.
    daily_rep = None
    if all_reports:
        for tf in (Timeframe.M6, Timeframe.M1, Timeframe.Y1):
            if all_reports.get(tf) is not None:
                daily_rep = all_reports[tf]
                break
    if daily_rep is not None:
        closes = daily_rep.df["close"].dropna()
        chase = False
        if len(closes) >= 4:
            rets = closes.pct_change().iloc[-3:] * 100
            chase = bool((rets.abs() >= _CHASE_PCT).any())
        checks.append(SwingCheck(
            "No chase — no ≥5% daily move in the last 3 days", not chase,
            ("calm tape" if not chase else
             f"a {rets.abs().max():.1f}% day just happened — retest risk"), 6))

        # Overextension vs the 6M 20-day average.
        sma20_d = _last(daily_rep.df, "sma20")
        price_d = _last(daily_rep.df, "close")
        if sma20_d and price_d:
            ext = (price_d / sma20_d - 1) * 100
            ok = ext <= _EXTENSION_PCT if bull else ext >= -_EXTENSION_PCT
            checks.append(SwingCheck(
                "Not overextended vs 20-day average", ok,
                f"{ext:+.1f}% from the daily 20-MA", 5))

    # Strategy conflict — swinging against the app's own long-term read.
    inv_pct = context.get("investor_pct")
    if inv_pct is not None:
        ok = inv_pct >= 45 if bull else inv_pct <= 55
        checks.append(SwingCheck(
            "No conflict with the long-term read", ok,
            f"investor conviction {inv_pct}% bullish", 4))

    # --- informational rows (weight 0 — shown, never scored) -----------------
    edays = context.get("earnings_days")
    if edays is not None:
        checks.append(SwingCheck("Earnings proximity", edays > context.get(
            "earnings_guard_days", 14),
            f"earnings in {edays} day{'s' if edays != 1 else ''}", 0))
    tgt = context.get("analyst_target")
    if tgt:
        ok = tgt >= geom.entry if bull else tgt <= geom.entry
        checks.append(SwingCheck("Street sanity", ok,
                                 f"analyst mean target ${tgt:.2f} vs entry ${geom.entry:.2f}", 0))
    checks.append(SwingCheck("Fast-mover energy (info)", fast,
                             "gap/volume spike present — moves fast both ways" if fast
                             else "no unusual energy", 0))

    denom = sum((c.weight / 2 if c.na else c.weight) for c in checks if c.weight)
    earned = sum(c.weight for c in checks if c.weight and c.ok and not c.na)
    pct = round(earned / denom * 100) if denom else 0
    aim_pct = abs(geom.target / geom.entry - 1) * 100
    if aim_pct < 3.0:
        pct = min(pct, 40)
    label = ("Strong" if pct >= 75 else "Good" if pct >= 55
             else "Weak — wait" if pct >= 35 else "Avoid")
    return pct, label, checks


# --------------------------------------------------------------------------- #
def build_swing_plan(report: TimeframeReport, usecase: UseCase,
                     pace: SwingPace = SwingPace.STANDARD,
                     price_override: float | None = None,
                     all_reports: dict | None = None,
                     context: dict | None = None) -> SwingPlan | None:
    """Build the honest swing plan.

    price_override: live tick recompute. all_reports: Timeframe→report for the
    multi-frame score + daily ATR. context: {investor_pct, earnings_days,
    analyst_target} — optional external sanity inputs.
    """
    if price_override and price_override > 0:
        price = float(price_override)
    else:
        price = float(report.meta.get("last_close", 0.0))
    if price <= 0:
        return None
    df = report.df
    names = {s.name for s in report.signals}
    fast = bool(names & _FAST)
    tuning = SWING_PACE[pace]
    context = dict(context or {})
    context.setdefault("earnings_guard_days", tuning.earnings_guard_days)

    atr_frac, atr_src = _daily_atr(all_reports, df, price)
    atr_abs = atr_frac * price

    if usecase == UseCase.SELL:
        return _build_side(report, price, df, names, fast, tuning, all_reports,
                           context, atr_abs, atr_frac, atr_src, short=True, own=False)
    return _build_side(report, price, df, names, fast, tuning, all_reports,
                       context, atr_abs, atr_frac, atr_src, short=False,
                       own=(usecase == UseCase.OWN))


def _detect_long_setup(report, price, df, names, atr_abs) -> str | None:
    sma50 = _last(df, "sma50")
    sma200 = _last(df, "sma200")
    ema20 = _last(df, "ema20")
    rsi = _last(df, "rsi") or 50.0
    supports = sorted((lv.price for lv in report.levels if lv.price < price), reverse=True)
    resistances = sorted(lv.price for lv in report.levels if lv.price > price)
    s1 = supports[0] if supports else None
    r1 = resistances[0] if resistances else None
    uptrend = sma50 is not None and price >= sma50
    above_200 = sma200 is None or price >= sma200
    bull_conf = [s for s in report.signals if s.name in _BULL_CONFIRM]
    tc = report.trend_change

    if tc.likely and tc.direction == Direction.BULL and above_200:
        return "Trend-change reversal (early)"
    if ("premarket_gap_up" in names) or ("macd_bull_cross" in names and "volume_spike" in names):
        return "Momentum / gap"
    if r1 is not None and "volume_spike" in names and (price - r1) <= 0 and (r1 - price) <= 0.5 * atr_abs:
        return "Breakout (volume)"
    if rsi <= 40 and bull_conf and above_200:
        return "Oversold reversal at support"
    if uptrend and ema20 is not None and abs(price - ema20) <= atr_abs and _rising(df, "ema20"):
        return "Pullback to 20-EMA"
    if uptrend and s1 is not None and (price - s1) <= atr_abs:
        return "Support test (uptrend)"
    return None


def _detect_short_setup(report, price, df, names, atr_abs) -> str | None:
    sma50 = _last(df, "sma50")
    rsi = _last(df, "rsi") or 50.0
    resistances = sorted(lv.price for lv in report.levels if lv.price > price)
    r1 = resistances[0] if resistances else None
    downtrend = sma50 is not None and price <= sma50
    bear_conf = [s for s in report.signals if s.name in _BEAR_CONFIRM]
    tc = report.trend_change
    if tc.likely and tc.direction == Direction.BEAR:
        return "Trend-change reversal (down)"
    if ("premarket_gap_down" in names) or ("macd_bear_cross" in names and "volume_spike" in names):
        return "Momentum / gap (down)"
    if rsi >= 60 and bear_conf:
        return "Overbought reversal at resistance"
    if downtrend and r1 is not None and (r1 - price) <= atr_abs:
        return "Rally into resistance (downtrend)"
    return None


def _build_side(report, price, df, names, fast, tuning: PaceTuning, all_reports,
                context, atr_abs, atr_frac, atr_src, short: bool, own: bool) -> SwingPlan:
    bias = Direction.BEAR if short else Direction.BULL
    walls = sorted(lv.price for lv in report.levels if lv.price > price)
    supports = sorted((lv.price for lv in report.levels if lv.price < price), reverse=True)

    setup = (_detect_short_setup(report, price, df, names, atr_abs) if short
             else _detect_long_setup(report, price, df, names, atr_abs))
    countertrend = setup in _COUNTERTREND_SETUPS
    sma50 = _last(df, "sma50")
    sma200 = _last(df, "sma200")
    strong_downtrend = (not short and sma50 is not None and price < sma50
                        and sma200 is not None and price < sma200)
    setup_present = setup is not None and not strong_downtrend

    geom = (_short_geometry(price, walls, supports, atr_abs, tuning, countertrend) if short
            else _long_geometry(price, walls, supports, atr_abs, tuning, countertrend))

    entry, stop, target1 = geom.entry, geom.stop, geom.target
    risk = abs(entry - stop)
    reward = abs(target1 - entry)
    rr = reward / risk if risk > 0 else 0.0
    aim = reward / entry  # fraction

    score, score_label, checks = _swing_score(
        bias, setup, setup_present, countertrend, rr, geom, walls, supports,
        fast, df, all_reports, atr_frac, context)

    # GO gating: only an immediate plan, with-trend tier (or sanctioned scalp),
    # honest R:R, a worthwhile aim, and no earnings landmine inside the horizon.
    inv_pct = context.get("investor_pct")
    tier_ok = (not countertrend) or (inv_pct is None or inv_pct >= 45) or \
              (tuning.budget_days <= 3)     # fast pace may scalp first-wall bounces
    edays = context.get("earnings_days")
    earnings_block = edays is not None and edays <= tuning.earnings_guard_days
    go = (geom.kind == "immediate" and setup_present and tier_ok
          and rr >= _MIN_RR and aim >= tuning.min_move
          and not strong_downtrend and not earnings_block)
    light = "go" if go else ("forming" if (setup_present or geom.kind == "breakout_wait")
                             else "no")

    # --- guidance: the one-line expert sentence ---
    d = "short" if short else "long"
    if go:
        guidance = (f"Enter {d} near ${entry:.2f} now · stop ${stop:.2f} "
                    f"({(stop/entry-1)*100:+.1f}%) · {geom.note} → target ${target1:.2f} "
                    f"({(target1/entry-1)*100:+.1f}%).")
    elif geom.kind == "breakout_wait":
        guidance = (f"No room to the next {'floor' if short else 'ceiling'} — "
                    f"{geom.note}, then target ${target1:.2f} "
                    f"({abs(target1/entry-1)*100:.1f}% from the trigger).")
        if tuning.stop_atr_mult * atr_frac > abs(target1 / entry - 1):
            guidance += (f" Note: daily volatility ~{atr_frac*100:.1f}% exceeds that room — "
                         "size small or skip.")
    elif geom.kind == "no_trade":
        up_w = f"${walls[0]:.2f}" if walls else "the next high"
        dn_w = f"${supports[0]:.2f}" if supports else "the next low"
        guidance = (f"No swing here ({geom.note}). Watch a break above {up_w} "
                    f"or a slide under {dn_w}.")
    elif earnings_block:
        guidance = f"Earnings in {edays} days — inside the {tuning.horizon} horizon; stand aside."
    elif not setup_present:
        guidance = "No valid setup — wait for a pullback, an oversold bounce, or a breakout to line up."
    elif rr < _MIN_RR:
        guidance = (f"Setup present but honest R:R is only {rr:.1f}:1 (need ≥2) — "
                    f"wait for a better entry or more room.")
    else:
        guidance = f"Aim {aim*100:.1f}% is below the {tuning.min_move*100:.0f}% minimum — not worth the risk."

    # --- reasons ---
    reasons: list[str] = []
    conf_set = _BEAR_CONFIRM if short else _BULL_CONFIRM
    confirms = [s for s in report.signals if s.name in conf_set]
    if setup and setup.startswith("Trend-change"):
        for r in report.trend_change.reasons[:2]:
            reasons.append(f"✓ {r}")
    for s in confirms[:3]:
        reasons.append(f"✓ {s.evidence}")
    if countertrend and setup_present:
        reasons.append("⚠️ Countertrend setup — a bounce scalp against the prevailing move; "
                       "first-wall target only, size small.")
    if rr > 4 and countertrend:
        reasons.append("⚠️ R:R above 4:1 on a countertrend setup is usually a math artifact — "
                       "treat with suspicion.")
    reasons.append(f"Daily volatility ~{atr_frac*100:.1f}%/day (from {atr_src}) sizes the stop.")
    reasons.append(f"Entry ${entry:.2f} · Stop ${stop:.2f} ({(stop/entry-1)*100:+.1f}%) · "
                   f"Target ${target1:.2f} ({(target1/entry-1)*100:+.1f}%) · R:R {rr:.1f}:1.")
    if own:
        reasons.insert(0, "Managing an existing position: use the stop to protect and the target to trim.")

    # Second target: next wall beyond target1 (honest as well).
    if short:
        beyond = [s for s in supports if s < target1 * 0.995]
        target2 = beyond[0] if beyond else None
    else:
        beyond = [w for w in walls if w > target1 * 1.005]
        target2 = beyond[0] if beyond else None

    return SwingPlan(
        setup=setup or "No setup", bias=bias, entry=round(entry, 2),
        entry_note=geom.note,
        stop=round(stop, 2), stop_pct=round((stop / entry - 1) * 100, 1),
        target1=round(target1, 2), target1_pct=round((target1 / entry - 1) * 100, 1),
        target2=round(target2, 2) if target2 else None, rr=round(rr, 1),
        risk_pct=round(abs(stop / entry - 1) * 100, 1), go=go, light=light,
        confidence=_confidence(go, rr, len(confirms)), reasons=reasons,
        horizon=tuning.horizon, fast_mover=fast,
        score=score, score_label=score_label, checks=checks,
        kind=geom.kind, guidance=guidance, trigger=geom.trigger,
        daily_atr_pct=round(atr_frac * 100, 1), atr_source=atr_src,
    )


def _confidence(go: bool, rr: float, confirms: int) -> str:
    if go and rr >= 3 and confirms >= 2:
        return "high"
    if go:
        return "moderate"
    return "low"

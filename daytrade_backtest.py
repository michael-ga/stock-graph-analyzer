"""Intraday day-trade backtest — why did / didn't a setup execute on a given day.

Unlike ``backtest.py`` (swing, daily bars, 10-day horizon), this replays a single
trading day **bar by bar** on 5-minute data and, at each bar, reconstructs the
exact live radar decision AS OF that moment (no lookahead) to answer: for the
breakout / gap-and-go / volume setups, did it fire — and if not, **which gate
blocked it**.

Fidelity notes (parity with the live radar):
- The live 1D frame is only the last ~2 days of 5-min bars, so each as-of slice is
  trailing-2-days up to the bar (`_asof_intraday`).
- The swing guards (`overext`/`leg_spent`, "no chase", ADX, Bollinger) read the
  DAILY frame, whose last bar live is *today's partial bar*. We rebuild that partial
  bar from the session so far (`_asof_daily`) — this is what makes a +8% gap trip the
  2σ Bollinger veto, so the diagnosis is faithful.
- The Gap-and-Go decision reuses `analysis.orb` verbatim (same code as the app).

Run:
  .venv\\Scripts\\python.exe daytrade_backtest.py INTC AMD [--date YYYY-MM-DD]
  .venv\\Scripts\\python.exe daytrade_backtest.py INTC AMD --variants
  ...add --verbose for the full per-bar gate + score dump.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from stockanalyzer.analysis import orb
from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.analysis.indicators import relative_volume
from stockanalyzer.data.providers import get_provider
from stockanalyzer.data.schema import Timeframe, validate_ohlcv
from stockanalyzer.explain.swing import build_swing_plan
from stockanalyzer.explain.usecase import UseCase
from stockanalyzer.strategy import SwingPace
from stockanalyzer.verdict.aggregate import build_verdict

_ET = "America/New_York"
_REG_START, _OR_END, _REG_END = "09:30", "09:45", "16:00"

# Setups that are momentum plays — a mean-reversion/value veto on one of these is a
# "contradiction" (the thing the setup-isolation bypass would target).
_MOMENTUM_SETUPS = {"gap_and_go_orb", "Momentum / gap", "Breakout (volume)"}
# Score checks that are mean-reversion / value in spirit.
_MEANREV_CHECKS = ("Bollinger", "overextended", "No chase", "Buying low",
                   "Trend strength")


# --------------------------------------------------------------------------- #
# Data fetch (5-min + 30-min intraday, plus daily frames).
# --------------------------------------------------------------------------- #
def _yf_intraday(ticker: str, interval: str, period: str = "1mo") -> pd.DataFrame:
    """Raw intraday OHLCV from yfinance (with pre/post), ET index."""
    import yfinance as yf

    df = yf.download(ticker, period=period, interval=interval, auto_adjust=False,
                     progress=False, threads=False, prepost=True)
    if df is None or len(df) == 0:
        raise RuntimeError(f"no {interval} data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    idx = pd.DatetimeIndex(df.index)
    df.index = idx.tz_convert(_ET) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(_ET)
    return validate_ohlcv(df[["open", "high", "low", "close", "volume"]])


def _naive_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """ET wall-clock dates as a tz-naive index (so it compares to the daily frames)."""
    idx = index.tz_localize(None) if index.tz is not None else index
    return idx.normalize()


def _trading_days(intraday5: pd.DataFrame) -> list[pd.Timestamp]:
    """Distinct ET dates that actually opened a regular session (have a 09:30 bar)."""
    reg = intraday5.between_time(_REG_START, _REG_END, inclusive="left")
    return sorted(set(_naive_dates(reg.index)))


# --------------------------------------------------------------------------- #
# As-of frame reconstruction (no lookahead).
# --------------------------------------------------------------------------- #
def _asof_intraday(intraday: pd.DataFrame, t: pd.Timestamp, days: int) -> pd.DataFrame:
    """Bars up to and including t, trailing `days` calendar days (mirrors the live
    fixed-period intraday window)."""
    lo = t - pd.Timedelta(days=days)
    return intraday[(intraday.index > lo) & (intraday.index <= t)]


def _day_bars(intraday: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """All bars whose ET date == day (day is a tz-naive normalized date)."""
    return intraday[_naive_dates(intraday.index) == day]


def _session_upto(intraday5: pd.DataFrame, day: pd.Timestamp, t: pd.Timestamp) -> pd.DataFrame:
    """Regular-session bars of `day` from the open through t."""
    d = _day_bars(intraday5, day).between_time(_REG_START, _REG_END, inclusive="left")
    return d[d.index <= t]


def _asof_daily(daily_prior: pd.DataFrame, session_bars: pd.DataFrame,
                day: pd.Timestamp) -> pd.DataFrame:
    """Daily frame = completed prior days + today's PARTIAL bar built from the
    session so far. This is what the live daily frame looks like intraday, so the
    Bollinger/overextension/chase guards see the gap (the whole point)."""
    if session_bars.empty:
        return daily_prior
    today = pd.DataFrame({
        "open": [float(session_bars["open"].iloc[0])],
        "high": [float(session_bars["high"].max())],
        "low": [float(session_bars["low"].min())],
        "close": [float(session_bars["close"].iloc[-1])],
        "volume": [float(session_bars["volume"].sum())],
    }, index=[day])
    return pd.concat([daily_prior, today])


def _asof_reports(intraday5, intraday30, daily, day, t) -> dict:
    """The full multi-timeframe view the radar would have had at bar t."""
    sess = _session_upto(intraday5, day, t)
    reports: dict = {}
    d1 = _asof_intraday(intraday5, t, days=2)
    if len(d1) >= 20:
        reports[Timeframe.D1] = analyze_timeframe(d1)
    d5 = _asof_intraday(intraday30, t, days=5)
    if len(d5) >= 20:
        reports[Timeframe.D5] = analyze_timeframe(d5)
    for tf in (Timeframe.M1, Timeframe.M6, Timeframe.Y1):
        base = daily.get(tf)
        if base is None:
            continue
        prior = base[_naive_dates(base.index) < day]
        frame = _asof_daily(prior, sess, day)
        if len(frame) >= 20:
            reports[tf] = analyze_timeframe(frame)
    return reports


def _decision_rep(reports: dict):
    """Same preference the radar uses (D5 → D1 → M1)."""
    for tf in (Timeframe.D5, Timeframe.D1, Timeframe.M1):
        if reports.get(tf) is not None:
            return reports[tf]
    return None


# --------------------------------------------------------------------------- #
# Per-bar diagnosis.
# --------------------------------------------------------------------------- #
@dataclass
class BarTrace:
    t: str
    price: float
    setup: str
    kind: str
    go: bool
    score: int
    rr: float
    block: str                       # binding swing gate (plain English)
    gap_fired: bool
    gap_reason: str
    gap_in_window: bool = False
    gap_broke: bool = False
    rvol: float | None = None
    contradiction: str | None = None  # momentum setup vetoed by a mean-reversion gate
    lost: list[tuple[str, int]] = field(default_factory=list)   # (check, weight) failed
    earned: int = 0
    denom: int = 0


def _swing_block(plan) -> str:
    """The binding reason a non-GO swing plan didn't fire, from its own fields."""
    if plan.go:
        return "GO"
    g = plan.guidance.lower()
    if plan.kind == "breakout_wait":
        return "armed breakout (not immediate) — waits for a close past the trigger"
    if plan.kind == "no_trade":
        if "2σ" in plan.guidance or "spent" in g:
            return "no_trade: leg spent / 2σ-extended (Bollinger blowoff)"
        if "reach" in g or "away" in g:
            return "no_trade: workable wall beyond the horizon's reach"
        return f"no_trade: {plan.guidance[:60]}"
    if "volume" in g:
        return "vol_ok: breakout needs volume confirmation (rvol ≤ 1.5×)"
    if "indicator says don't" in g or "bollinger" in g:
        return "veto: overextended / adverse indicator (Bollinger / ADX / MACD)"
    if "r:r" in g or "risk" in g:
        return f"rr < {plan.rr:.1f} below the 1.5 floor"
    if "minimum" in g:
        return "aim below the pace minimum move"
    if "earnings" in g:
        return "earnings inside the horizon"
    if "crash" in g:
        return "crash-rebound regime"
    if plan.setup == "No setup":
        return "no valid setup detected"
    return "blocked (see guidance)"


def _contradiction(plan) -> str | None:
    """Flag a momentum setup vetoed by a mean-reversion / value gate."""
    if plan.setup not in _MOMENTUM_SETUPS:
        return None
    # Geometry-level veto (leg_spent) shows as no_trade + a 2σ guidance.
    if plan.kind == "no_trade" and ("2σ" in plan.guidance or "spent" in plan.guidance.lower()):
        return f"{plan.setup} vetoed by the 2σ Bollinger 'leg spent' rule"
    failed = [c.name for c in plan.checks
              if not c.ok and not c.na and any(k in c.name for k in _MEANREV_CHECKS)]
    if failed:
        return f"{plan.setup} loses points to mean-reversion gates: {', '.join(failed[:3])}"
    return None


def _score_breakdown(plan) -> tuple[int, int, list[tuple[str, int]]]:
    """(earned, denom, [(failed_check, weight_lost), ...]) behind the headline score."""
    denom = sum(int(c.weight / 2) if c.na else c.weight for c in plan.checks if c.weight)
    earned = sum(c.weight for c in plan.checks if c.weight and c.ok and not c.na)
    lost = sorted(((c.name, c.weight) for c in plan.checks
                   if c.weight and not c.ok and not c.na), key=lambda x: -x[1])
    return earned, denom, lost


def _eval_bar(reports, orange, price, t) -> BarTrace | None:
    dec = _decision_rep(reports)
    if dec is None:
        return None
    inv = round((build_verdict(reports).score + 1) / 2 * 100)
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, price_override=price,
                            all_reports=reports, context={"investor_pct": inv})
    if plan is None:
        return None
    gap = orb.gap_and_go_signal(orange, reports.get(Timeframe.D1).df if reports.get(Timeframe.D1) else None,
                                price, t)
    earned, denom, lost = _score_breakdown(plan)
    return BarTrace(
        t=t.strftime("%H:%M"), price=round(price, 2), setup=plan.setup,
        kind=plan.kind, go=plan.go, score=plan.score, rr=plan.rr,
        block=_swing_block(plan), gap_fired=gap.fired, gap_reason=gap.reason,
        gap_in_window=gap.in_window, gap_broke=gap.broke_or_high, rvol=gap.rvol,
        contradiction=_contradiction(plan), lost=lost, earned=earned, denom=denom)


# --------------------------------------------------------------------------- #
# One ticker-day.
# --------------------------------------------------------------------------- #
def run_day(ticker: str, intraday5, intraday30, daily, day, verbose: bool) -> list[BarTrace]:
    m1 = daily.get(Timeframe.M1)
    prior = m1[m1.index.normalize() < day] if m1 is not None else None
    prev_close = float(prior["close"].iloc[-1]) if prior is not None and len(prior) else None

    sess_full = _day_bars(intraday5, day).between_time(
        _REG_START, _REG_END, inclusive="left")
    if sess_full.empty:
        print(f"  {ticker}: no regular-session bars for {day.date()}")
        return []

    # Opening range (fixed once 09:45 passes).
    or_slice = _asof_intraday(intraday5, sess_full.index[-1], days=2)
    orange = orb.opening_range(or_slice, prev_close, day.replace(hour=10))
    gap_txt = (f"gap {orange.gap_pct * 100:+.1f}% (open ${orange.open:.2f} vs prev "
               f"${prev_close:.2f}) · OR ${orange.low:.2f}–${orange.high:.2f}"
               if orange else "opening range unavailable")
    print(f"\n=== {ticker} — {day.date()} — {gap_txt} ===")

    traces: list[BarTrace] = []
    # Replay 09:45 → 15:55.
    bars = sess_full[sess_full.index.time >= pd.Timestamp(_OR_END).time()]
    for t, row in bars.iterrows():
        price = float(row["close"])
        reports = _asof_reports(intraday5, intraday30, daily, day, t)
        tr = _eval_bar(reports, orange, price, t)
        if tr is not None:
            traces.append(tr)
    _print_day_summary(ticker, orange, traces, verbose)
    return traces


def _print_day_summary(ticker, orange, traces, verbose):
    if not traces:
        print("  (no evaluable bars)")
        return
    go = [t for t in traces if t.go]
    gap = [t for t in traces if t.gap_fired]
    contra = [t for t in traces if t.contradiction]

    # Per-setup verdicts.
    print("  SWING GO:", f"fired {len(go)}/{len(traces)} bars"
          if go else f"never fired — binding gate: {_top(traces, 'block')}")
    if gap:
        g0 = gap[0]
        print(f"  GAP-AND-GO: FIRED at {g0.t} — {g0.gap_reason}")
    else:
        # Why not? show the ORB reason distribution.
        print(f"  GAP-AND-GO: never fired — {_gap_reason_summary(orange, traces)}")
    if contra:
        print(f"  ⚠ CONTRADICTION: {contra[0].contradiction} "
              f"(on {len(contra)}/{len(traces)} bars)")

    # Peak-score bar: the day's best case + its score breakdown (the "40%" dump).
    best = max(traces, key=lambda t: t.score)
    print(f"  peak score {best.score}% at {best.t} (setup: {best.setup}, kind: {best.kind})")
    print(f"    score = {best.earned}/{best.denom} weighted pts. Top weight LOST:")
    for name, w in best.lost[:6]:
        print(f"      −{w:>2}  {name}")

    if verbose:
        print("  per-bar trace:")
        hdr = f"    {'t':<6}{'px':>8} {'setup':<26}{'kind':<13}{'go':<3}{'score':>5}  gap  block"
        print(hdr)
        for t in traces:
            print(f"    {t.t:<6}{t.price:>8.2f} {t.setup[:25]:<26}{t.kind:<13}"
                  f"{('Y' if t.go else '-'):<3}{t.score:>4}%  "
                  f"{('Y' if t.gap_fired else '-'):<4} {t.block[:46]}")


def _top(traces, attr) -> str:
    from collections import Counter
    c = Counter(getattr(t, attr) for t in traces)
    name, n = c.most_common(1)[0]
    return f"{name} ({n}/{len(traces)} bars)"


def _gap_reason_summary(orange, traces) -> str:
    if orange is None:
        return "no opening range"
    if not orange.gap_up:
        return f"gap {(orange.gap_pct or 0) * 100:+.1f}% < 2% threshold"
    inwin = [t for t in traces if t.gap_in_window]
    if not inwin:
        return "no bars inside the 09:45-11:30 window"
    broke = [t for t in inwin if t.gap_broke]
    if not broke:
        hi = max(t.price for t in inwin)
        return (f"price never broke OR-high ${orange.high:.2f} in the window "
                f"(best in-window close ${hi:.2f})")
    b0 = broke[0]
    return (f"broke OR-high at {b0.t} but rvol {(b0.rvol or 0):.1f}x < "
            f"{orb.MIN_BREAKOUT_RVOL:g}x (no volume confirmation)")


# --------------------------------------------------------------------------- #
# Relaxed day-trade bot variants (counterfactual ranges, HARD R:R).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Variant:
    name: str
    rr_floor: float           # HARD — target is set at exactly this R
    gap_min: float
    rvol_min: float
    orb_window_end: str       # "HH:MM" ET
    bollinger_bypass_min: int
    no_chase_bypass: bool


def _default_variants() -> list[Variant]:
    out = [Variant("baseline (live ORB)", 2.0, 0.02, 1.5, "11:30", 0, False)]
    for rr in (1.5, 2.0, 2.5):
        for rvol in (1.5, 1.2, 1.0):
            for gap in (0.02, 0.015):
                out.append(Variant(
                    f"rr≥{rr} rvol≥{rvol} gap≥{gap*100:.1f}%", rr, gap, rvol,
                    "12:30", 45, True))
    return out


def _variant_trade(v: Variant, intraday5, prev_close, day) -> dict | None:
    """Independent relaxed day-trade bot: enter on the first ORB-high break that
    meets the (relaxed) entry conditions; target set at the HARD rr_floor; stop at
    OR-low. Judge over the rest of the day."""
    sess = _day_bars(intraday5, day).between_time(
        _REG_START, _REG_END, inclusive="left")
    if sess.empty or prev_close is None:
        return None
    or_win = sess.between_time(_REG_START, _OR_END, inclusive="left")
    if or_win.empty:
        return None
    or_high, or_low = float(or_win["high"].max()), float(or_win["low"].min())
    day_open = float(sess["open"].iloc[0])
    gap = day_open / prev_close - 1
    if gap < v.gap_min:
        return None
    window = sess[(sess.index.time >= pd.Timestamp(_OR_END).time())
                  & (sess.index.time <= pd.Timestamp(v.orb_window_end).time())]
    for t, row in window.iterrows():
        if float(row["close"]) < or_high:           # not broken out yet (close basis, like live)
            continue
        rvol = relative_volume(_asof_intraday(intraday5, t, days=2))   # 2-day frame, like live
        if rvol is not None and rvol < v.rvol_min:
            continue
        entry = or_high                              # fill at the breakout level
        stop = round(or_low * (1 - orb.ORB_STOP_BUFFER), 4)
        if stop >= entry:
            return None
        target = round(entry + v.rr_floor * (entry - stop), 4)
        rest = sess[sess.index > t]
        status, r = _judge_intraday(rest, entry, stop, target, v.rr_floor)
        return {"t": t.strftime("%H:%M"), "entry": entry, "stop": stop,
                "target": round(target, 2), "rr": v.rr_floor, "status": status,
                "r": r, "rvol": rvol}
    return None


def _judge_intraday(rest, entry, stop, target, rr_floor) -> tuple[str, float]:
    """Stop-first conservative judging over the remaining bars; EOD close otherwise."""
    for _, row in rest.iterrows():
        if float(row["low"]) <= stop:
            return "stop", -1.0
        if float(row["high"]) >= target:
            return "target", rr_floor
    if len(rest):
        r = (float(rest["close"].iloc[-1]) - entry) / (entry - stop)
        return "eod", round(r, 2)
    return "open", 0.0


def run_variants(tickers, per_ticker_data) -> None:
    print("\n" + "=" * 74)
    print("RELAXED DAY-TRADE VARIANTS — hard R:R, relaxed entry (counterfactual)")
    print("=" * 74)
    variants = _default_variants()
    hdr = f"{'variant':<34}{'fires':>6}{'wins':>6}{'WR':>6}{'expR':>7}"
    print(hdr + "   INTC/AMD detail")
    print("-" * len(hdr))
    for v in variants:
        trades = []
        detail = []
        for tk in tickers:
            d = per_ticker_data[tk]
            for day in d["days"]:
                tr = _variant_trade(v, d["i5"], d["prev_close"].get(day), day)
                if tr:
                    trades.append(tr)
                    detail.append(f"{tk} {tr['status']} {tr['r']:+.1f}R")
        done = [t for t in trades if t["status"] in ("target", "stop", "eod")]
        wins = [t for t in done if t["r"] > 0]
        wr = f"{len(wins)/len(done)*100:.0f}%" if done else "n/a"
        exp = f"{np.mean([t['r'] for t in done]):+.2f}" if done else "n/a"
        print(f"{v.name:<34}{len(trades):>6}{len(wins):>6}{wr:>6}{exp:>7}   "
              + " · ".join(detail[:4]))
    print("\nEntry = ORB-high break; stop = OR-low; target set at the HARD rr_floor. "
          "'baseline' = the current live ORB (rr 2, rvol 1.5, gap 2%, 11:30 cutoff).")


# --------------------------------------------------------------------------- #
def _load(ticker: str, provider) -> dict:
    i5 = _yf_intraday(ticker, "5m", "1mo")
    i30 = _yf_intraday(ticker, "30m", "1mo")
    daily = {tf: provider.fetch_cached(ticker, tf) for tf in
             (Timeframe.M1, Timeframe.M6, Timeframe.Y1)}
    for tf, df in daily.items():                     # normalize to tz-naive dates
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
    tdays = _trading_days(i5)
    m1 = daily[Timeframe.M1]
    prev_close = {}
    for day in tdays:
        prior = m1[_naive_dates(m1.index) < day]
        prev_close[day] = float(prior["close"].iloc[-1]) if len(prior) else None
    return {"i5": i5, "i30": i30, "daily": daily, "days": tdays, "prev_close": prev_close}


def main() -> None:
    import sys
    try:                                   # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tickers", nargs="*", default=["INTC", "AMD"])
    ap.add_argument("--date", help="ET trading day YYYY-MM-DD (default: most recent)")
    ap.add_argument("--variants", action="store_true", help="run the relaxed-variant sweep")
    ap.add_argument("--verbose", action="store_true", help="full per-bar trace")
    args = ap.parse_args()
    tickers = [t.upper() for t in (args.tickers or ["INTC", "AMD"])]

    provider = get_provider("yfinance")
    per_ticker = {}
    for tk in tickers:
        try:
            per_ticker[tk] = _load(tk, provider)
        except Exception as exc:
            print(f"{tk}: load failed ({exc})")

    target = pd.Timestamp(args.date).normalize() if args.date else None
    for tk in tickers:
        if tk not in per_ticker:
            continue
        d = per_ticker[tk]
        day = target if target is not None else (d["days"][-1] if d["days"] else None)
        if day is None or day not in d["days"]:
            avail = ", ".join(str(x.date()) for x in d["days"][-5:])
            print(f"{tk}: {day.date() if day is not None else 'no'} not in data (recent: {avail})")
            continue
        run_day(tk, d["i5"], d["i30"], d["daily"], day, args.verbose)

    if args.variants:
        run_variants([t for t in tickers if t in per_ticker], per_ticker)


if __name__ == "__main__":
    main()

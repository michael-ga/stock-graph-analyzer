"""Bar-by-bar backtest of the context-based setups over real 5-min history.

    .venv/Scripts/python.exe -m stockanalyzer.setups.harness SNXX SNDQ NBIZ --days 40
    .venv/Scripts/python.exe -m stockanalyzer.setups.harness SNXX --setup silver_bullet --trades
    .venv/Scripts/python.exe -m stockanalyzer.setups.harness SNXX --days 20 --offset 40   # older 20

For every regular-session bar of every ticker-day it rebuilds the as-of context
(today's bars up to that bar, yesterday's session, today's pre-market, daily bars
before today) and asks each variant's ``signal``. The first fire per variant per
day is the trade. It is judged stop-first over the rest of the session and
flattened at the 15:55 bar close (``replay.judge_intraday``, the same judge as the
day-trade backtest and the replay), minus a flat per-share round-trip spread.

Read the output with the guardrails in ``algolab/LEARNINGS.md``: distinct days,
not rows; both halves; the best day removed. yfinance serves 5-min bars for the
trailing 60 sessions only.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from stockanalyzer import replay
from stockanalyzer.analysis import asof
from stockanalyzer.data.schema import Timeframe

from . import SETUPS
from .base import SetupContext, SetupDecision

SPREAD_PER_SHARE = 0.02     # round trip, the flat cost the book charges (store/manage)
MIN_SESSION_BARS = 60       # of 78; fewer = half-day or an illiquid tape, skipped
_PRE_START = "04:00"


def load(ticker: str, provider=None) -> dict:
    """60 sessions of 5-min bars (with pre/post) + a year of daily bars."""
    if provider is None:
        from stockanalyzer.data.providers import get_provider
        provider = get_provider("yfinance")
    i5 = replay.yf_intraday(ticker, "5m", replay.INTRADAY_PERIOD)
    daily = provider.fetch_cached(ticker, Timeframe.Y1).copy()
    if isinstance(daily.index, pd.DatetimeIndex) and daily.index.tz is not None:
        daily.index = daily.index.tz_localize(None)
    return {"i5": i5, "daily": daily, "days": asof.trading_days(i5)}


def _regular(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.between_time(asof.REG_START, asof.REG_END, inclusive="left")


def judge(dec: SetupDecision, sess: pd.DataFrame, j: int,
          spread: float = SPREAD_PER_SHARE) -> dict:
    """Outcome of a decision that fired on bar ``j`` of ``sess``, in R."""
    if dec.filled_intrabar and float(sess["low"].iloc[j]) <= dec.stop:
        status, r = "stop", -1.0                # resting order filled, same bar stopped
    else:
        status, r = replay.judge_intraday(sess.iloc[j + 1:], dec.entry, dec.stop,
                                          dec.target, dec.rr)
    cost = spread / (dec.entry - dec.stop)
    return {"status": status, "gross_r": float(r), "net_r": float(r) - cost,
            "risk_pct": (dec.entry - dec.stop) / dec.entry * 100}


def run_day(hist: dict, i: int, variants: list[tuple], spread: float = SPREAD_PER_SHARE,
            ticker: str = "") -> list[dict]:
    """Step one session bar by bar; the first fire per (setup, variant) is a trade."""
    i5, days = hist["i5"], hist["days"]
    day = days[i]
    allbars = asof.day_bars(i5, day)
    sess = _regular(allbars)
    if len(sess) < MIN_SESSION_BARS:
        return []
    pre = allbars.between_time(_PRE_START, asof.REG_START, inclusive="left")
    prev = _regular(asof.day_bars(i5, days[i - 1])) if i > 0 else None
    daily = hist["daily"][asof.naive_dates(hist["daily"].index) < day]
    prev_close = float(daily["close"].iloc[-1]) if len(daily) else None

    pending = list(variants)
    trades = []
    for j in range(len(sess)):
        if not pending:
            break
        ctx = SetupContext(sess.iloc[:j + 1], prev, pre, prev_close, daily)
        for mod, cfg in list(pending):
            dec = mod.signal(ctx, cfg)
            if dec.fired:
                trades.append({"ticker": ticker, "day": day, "t": sess.index[j].strftime("%H:%M"),
                               "setup": mod.NAME, "variant": cfg.name, "entry": dec.entry,
                               "stop": dec.stop, "target": dec.target, "rr": dec.rr,
                               **judge(dec, sess, j, spread)})
                pending.remove((mod, cfg))
    return trades


def run(histories: dict, variants: list[tuple], days: int = 40, offset: int = 0,
        spread: float = SPREAD_PER_SHARE) -> pd.DataFrame:
    """Trades for the ``days`` sessions ending ``offset`` sessions before the latest."""
    rows = []
    for tk, hist in histories.items():
        n = len(hist["days"])
        hi = n - offset
        for i in range(max(0, hi - days), hi):
            rows += run_day(hist, i, variants, spread, ticker=tk)
    return pd.DataFrame(rows)


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    """Per variant: n, distinct days, win rate, avg gross/net R, total, both halves of
    the date range, the average with the single best day removed, per ticker."""
    out = []
    if trades.empty:
        return pd.DataFrame(out)
    dates = sorted(trades["day"].unique())
    mid = dates[len(dates) // 2]
    for (setup, variant), g in trades.groupby(["setup", "variant"], sort=False):
        by_day = g.groupby("day")["net_r"].sum()
        rest = g[g["day"] != by_day.idxmax()]
        out.append({
            "setup": setup, "variant": variant, "n": len(g), "days": g["day"].nunique(),
            "win_pct": round((g["net_r"] > 0).mean() * 100),
            "avg_gross": round(g["gross_r"].mean(), 2), "avg_net": round(g["net_r"].mean(), 2),
            "total_net": round(g["net_r"].sum(), 1),
            "half1": round(g[g["day"] < mid]["net_r"].mean(), 2),
            "half2": round(g[g["day"] >= mid]["net_r"].mean(), 2),
            "no_best_day": round(rest["net_r"].mean(), 2) if len(rest) else np.nan,
            "per_ticker": " ".join(f"{tk}:{x['net_r'].mean():+.2f}({len(x)})"
                                   for tk, x in g.groupby("ticker")),
        })
    return pd.DataFrame(out)


def random_entry_null(histories: dict, trades: pd.DataFrame, reps: int = 200,
                      spread: float = SPREAD_PER_SHARE, seed: int = 7,
                      start: str = "09:35", end: str = "15:00") -> np.ndarray:
    """Average net R of ``reps`` fake books: every trade re-entered at a random
    bar's close on the same ticker-day, with the same risk % and R target. If the
    setup's timing beats this, the pattern adds something beyond its geometry."""
    rng = np.random.default_rng(seed)
    sessions, means = {}, []
    for tr in trades.itertuples():
        if (tr.ticker, tr.day) not in sessions:
            s = _regular(asof.day_bars(histories[tr.ticker]["i5"], tr.day))
            t = np.asarray(s.index.strftime("%H:%M"))
            sessions[(tr.ticker, tr.day)] = (s, np.flatnonzero((t >= start) & (t <= end)))
    for _ in range(reps):
        nets = []
        for tr in trades.itertuples():
            s, idx = sessions[(tr.ticker, tr.day)]
            j = int(rng.choice(idx))
            e = float(s["close"].iloc[j])
            risk = e * tr.risk_pct / 100
            dec = SetupDecision(True, "", tr.setup, tr.variant, entry=e, stop=e - risk,
                                target=e + tr.rr * risk, rr=tr.rr)
            nets.append(judge(dec, s, j, spread)["net_r"])
        means.append(np.mean(nets))
    return np.array(means)


def coverage(hist: dict) -> float:
    """Median regular-session bars per day (78 = every 5-min bar traded)."""
    reg = _regular(hist["i5"])
    return float(reg.groupby(asof.naive_dates(reg.index)).size().median()) if len(reg) else 0.0


def main(argv=None) -> None:
    import sys
    try:                                   # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--setup", action="append", choices=sorted(SETUPS),
                    help="limit to these setups (repeatable; default all)")
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--offset", type=int, default=0,
                    help="skip this many most recent sessions (holdout windows)")
    ap.add_argument("--spread", type=float, default=SPREAD_PER_SHARE)
    ap.add_argument("--trades", action="store_true", help="list every trade")
    ap.add_argument("--null", type=int, default=0, metavar="REPS",
                    help="also run a random-entry null with this many repetitions")
    a = ap.parse_args(argv)

    mods = [SETUPS[s] for s in (a.setup or sorted(SETUPS))]
    variants = [(m, cfg) for m in mods for cfg in m.VARIANTS]
    histories = {}
    for tk in (t.upper() for t in a.tickers):
        try:
            hist = load(tk)
        except Exception as exc:
            print(f"{tk}: load failed ({exc})")
            continue
        bars = coverage(hist)
        if bars < MIN_SESSION_BARS:
            print(f"⚠ {tk}: only ~{bars:.0f}/78 five-minute bars trade per session — "
                  f"too illiquid to backtest intraday (and to fill); skipped")
            continue
        histories[tk] = hist

    trades = run(histories, variants, a.days, a.offset, a.spread)
    if trades.empty:
        print("no trades")
        return
    first, last = trades["day"].min().date(), trades["day"].max().date()
    print(f"{', '.join(histories)} · {a.days} sessions (offset {a.offset}) · trades "
          f"{first}..{last} · spread ${a.spread:.2f}/share round trip\n")
    pd.set_option("display.width", 200)
    print(summarize(trades).to_string(index=False))
    print("\ndays = distinct dates (two legs of one pair on a date count once). "
          "half1/half2 split the date range; no_best_day drops the best date.")
    if a.null:
        print(f"\nrandom-entry null ({a.null} reps; same ticker-day, risk % and R target):")
        for (setup, variant), g in trades.groupby(["setup", "variant"], sort=False):
            sims = random_entry_null(histories, g, a.null, a.spread)
            actual = g["net_r"].mean()
            print(f"  {variant:<20} actual {actual:+.2f}R · null {sims.mean():+.2f} "
                  f"± {sims.std():.2f} · p(null ≥ actual) = {np.mean(sims >= actual):.3f}")
    if a.trades:
        cols = ["ticker", "day", "t", "variant", "entry", "stop", "target", "rr",
                "status", "gross_r", "net_r", "risk_pct"]
        print("\n" + trades.sort_values(["variant", "day", "ticker"])[cols]
              .to_string(index=False, float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()

"""Re-validate the live watcher's only "tested" setup with its own coded definitions.

The setup (``watch_setups.gap_check``, from the 2026-10-23 two-year study): a FAILED
GAP-UP (open ≥ +2% over yesterday's close, the first hour 09:30–10:30 closes below
the open) → buy the inverse ETF; a RECLAIMED GAP-DOWN (open ≤ −2%, first hour closes
above the open) → buy the long ETF. Only when SPY's previous close is below its
50-day mean (a down market) and at least ``BREADTH_MIN`` of the 20-name basket do
the same that day. Entry at 10:30, stop beyond the first-hour high (low), target =
yesterday's close (the gap fill), out at the close.

The study was 51 stocks × 488 sessions with its own definitions; the watcher codes
an approximation (20-name basket, ≥ 3 of 20). This re-runs the watcher's version on
two years of hourly bars (yfinance keeps 730 days of 60-minute bars; 5-minute bars
only 60 days). The first hourly bar IS the 09:30–10:30 first hour, so the rule maps
exactly; the stop-first check inside an hour is coarser than on 5-minute bars.

    PYTHONPATH=. .venv/Scripts/python.exe algolab/screens/gap_setup_revalidation_2026_11_10/revalidate.py [--refresh]

Instruments: the 20 basket names plus the watcher's underlyings, each traded through
a hypothetical daily-reset 2x ETF (long or inverse). $300 stake, $3 round trip.
Pre-registered read: the edge "holds" if, in down-market + breadth days, avg $ per
trade > 0 in both halves of the two years and without the best date, and > 0 for at
least 60% of instruments that traded. Exploratory comparisons (up market, no breadth
filter) are printed for context only.
"""
from __future__ import annotations

import argparse
import pickle
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / ".local" / "gap-reval"
BASKET = ("AAPL MSFT NVDA AMZN META GOOGL AVGO TSLA AMD MU INTC QCOM ORCL CRM NFLX ADBE TXN "
          "AMAT LRCX KLAC").split()                      # == watch_setups.BASKET
EXTRA = ["SNDK", "NBIS", "SMH"]                          # the watcher's other underlyings
GAP, BREADTH_MIN, LEV, STAKE, FEE = 0.02, 3, 2.0, 300.0, 3.0


def load(t: str, refresh: bool) -> pd.DataFrame | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    pk = CACHE / f"{t}_1h.pkl"
    if pk.exists() and not refresh:
        return pickle.loads(pk.read_bytes())
    import yfinance as yf
    df = yf.download(t, period="729d", interval="60m", auto_adjust=False, progress=False,
                     threads=False, prepost=False)
    if df is None or not len(df):
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index = pd.DatetimeIndex(df.index).tz_convert("America/New_York")
    pk.write_bytes(pickle.dumps(df))
    return df


def sessions(df: pd.DataFrame) -> pd.DataFrame:
    """One row per day: open, first-hour high/low/close, the close, prev close, plus
    the bars after 10:30 for the stop-first walk."""
    rows = []
    days = df.groupby(df.index.date)
    prev = None
    for d, g in days:
        g = g.between_time("09:30", "15:59")
        if len(g) < 6 or g.index[0].strftime("%H:%M") != "09:30":
            prev = float(g["close"].iloc[-1]) if len(g) else prev
            continue
        fh = g.iloc[0]
        rows.append(dict(day=pd.Timestamp(d), open=float(fh.open), fh_high=float(fh.high),
                         fh_low=float(fh.low), fh_close=float(fh.close),
                         close=float(g["close"].iloc[-1]), prev=prev, rest=g.iloc[1:]))
        prev = float(g["close"].iloc[-1])
    out = pd.DataFrame(rows)
    return out[out.prev.notna()].set_index("day")


def etf_ret(side: int, pc: float, e: float, x: float) -> float:
    """Return of a daily-reset 2x ETF (side +1 long, -1 inverse) from underlying e to x."""
    start = 1 + side * LEV * (e / pc - 1)
    end = 1 + side * LEV * (x / pc - 1)
    return end / start - 1


def trade(row, kind: str) -> dict | None:
    side = -1 if kind == "fade" else 1
    e = row.fh_close                                     # the 10:30 entry ≈ the first-hour close
    stop = row.fh_high * 1.002 if side < 0 else row.fh_low * 0.998
    tgt = row.prev
    if (side < 0 and not tgt < e < stop) or (side > 0 and not stop < e < tgt):
        return None
    x, why = row.close, "close"
    for _, b in row.rest.iterrows():                     # stop first inside each hour
        if (side < 0 and b.high >= stop) or (side > 0 and b.low <= stop):
            x, why = stop, "stop"
            break
        if (side < 0 and b.low <= tgt) or (side > 0 and b.high >= tgt):
            x, why = tgt, "target"
            break
    r = etf_ret(side, row.prev, e, x)
    return dict(etf_ret_pct=r * 100, usd=STAKE * r - FEE, exit=why)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    spy = load("SPY", a.refresh)
    spy_d = spy.groupby(spy.index.date)["close"].last()
    spy_d.index = pd.to_datetime(spy_d.index)
    # regime known at the open: yesterday's close vs the 50 closes before and including it
    down = (spy_d < spy_d.rolling(50).mean()).shift(1)
    S = {}
    for t in sorted(set(BASKET + EXTRA)):
        df = load(t, a.refresh)
        if df is not None and len(df):
            S[t] = sessions(df)
    pat = {}
    for t, s in S.items():
        g = s.open / s.prev - 1
        pat[t] = pd.DataFrame({"fade": (g >= GAP) & (s.fh_close < s.open),
                               "reclaim": (g <= -GAP) & (s.fh_close > s.open)})
    breadth = {k: pd.concat([pat[t][k].rename(t) for t in BASKET if t in pat], axis=1).sum(axis=1)
               for k in ("fade", "reclaim")}
    rows = []
    for t, s in S.items():
        for day, row in s.iterrows():
            for kind in ("fade", "reclaim"):
                if not pat[t].at[day, kind]:
                    continue
                res = trade(row, kind)
                if res is None:
                    continue
                rows.append(dict(ticker=t, day=day, kind=kind, down=bool(down.get(day, False)),
                                 breadth=int(breadth[kind].get(day, 0)), **res))
    T = pd.DataFrame(rows)
    if T.empty:
        print("no trades"); return
    T["regime"] = np.where(T.down, "down market", "up market")
    T["breadth_ok"] = T.breadth >= BREADTH_MIN
    days = sorted(T.day.unique()); mid = days[len(days) // 2]
    print(f"{len(S)} instruments · {T.day.min():%Y-%m-%d} → {T.day.max():%Y-%m-%d} · "
          f"{len(T)} pattern trades · $300 on a 2x ETF, $3 round trip\n")

    def stat(g):
        by = g.groupby("day").usd.sum()
        t = by.mean() / (by.std(ddof=1) / np.sqrt(len(by))) if len(by) > 2 and by.std(ddof=1) > 0 else np.nan
        tk = g.groupby("ticker").usd.mean()
        return pd.Series({"n": len(g), "dates": len(by), "win%": round(100 * (g.usd > 0).mean()),
                          "avg ETF %": round(g.etf_ret_pct.mean(), 2), "avg $": round(g.usd.mean(), 2),
                          "half1 $": round(g[g.day < mid].usd.mean(), 2),
                          "half2 $": round(g[g.day >= mid].usd.mean(), 2),
                          "no best date $": round(g[g.day != by.idxmax()].usd.mean(), 2) if len(by) > 1 else np.nan,
                          "date t": round(t, 2),
                          "% tickers > 0": round(100 * (tk > 0).mean())})
    pd.set_option("display.width", 220)
    print("=== THE CODED SETUP: down market + breadth ≥ 3/20 (the pre-registered read) ===")
    print(T[T.down & T.breadth_ok].groupby("kind").apply(stat).to_string())
    print("\n=== context (exploratory): every regime × breadth cell ===")
    print(T.groupby(["kind", "regime", "breadth_ok"]).apply(stat).to_string())
    print("\nexit reasons in the coded setup:")
    print(T[T.down & T.breadth_ok].groupby(["kind", "exit"]).usd.agg(["size", "mean"]).round(2).to_string())
    out = ROOT / ".local" / "gap-reval" / "trades.csv"
    T.drop(columns=[]).to_csv(out, index=False)
    print(f"\ntrades written to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

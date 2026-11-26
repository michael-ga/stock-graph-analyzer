"""End-of-day Murphy reversal / swing scanner (daily bars). Run at 15:45 or later for a final read; earlier runs use
today's partial bar and say so.

    python scan_swing.py --watch SNDK=SNXX:2,SNDQ:-2 --watch NBIS=NBIZ:-2 --watch INTC=INTW:2 [--also DD,TBBB]

Bullish reversal evidence (Murphy ch. 4, 5, 10): key reversal day (new 20-day low, close above yesterday's close,
upper part of the range), selling climax (that, on >= 2x volume), island reversal bottom, daily RSI bullish divergence,
MACD histogram turning up below zero. Bearish mirrors for tops. Each signal is a CANDIDATE for tomorrow:
the plan buys only on a break of today's high (or sells on a break of today's low) after 10:00, never at the close.
None of these are backtested on our data. Every result goes to the live ledger to be graded.
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from common import *
sys.path.insert(0, ROOT)
import numpy as np
from stockanalyzer.analysis.indicators import rsi, macd

ap = argparse.ArgumentParser()
ap.add_argument("--watch", action="append", default=[])
ap.add_argument("--also", default="", help="extra underlyings without ETFs, comma separated")
a = ap.parse_args()
W = parse_watch(a.watch) if a.watch else {}
for t in filter(None, a.also.upper().split(",")): W.setdefault(t, {})
partial = now().time() < dt.time(15, 45) and in_session()


def daily(t):
    d = yf.Ticker(t).history(period="1y", auto_adjust=False)
    d.index = d.index.tz_convert("America/New_York").tz_localize(None).normalize()
    return d


spy = daily("SPY"); spy_c = spy.Close
regime = "down (SPY < 50-day)" if spy_c.iloc[-1] < spy_c.rolling(50).mean().iloc[-1] else "up (SPY > 50-day)"
v = vix_read()
emit(INFO, f"swing scan {'(PARTIAL bar: final read after 15:45)' if partial else '(end of day)'}", f"market regime {regime}",
     f"VIX {v['level']} ({v['chg']:+.1f}%)" if v else "VIX n/a")


def pivots(s, kind, k=3):
    x = s.values; return [i for i in range(k, len(x) - k) if x[i] == (x[i - k:i + k + 1].min() if kind == "low" else x[i - k:i + k + 1].max())]


for u, etfs in W.items():
    d = daily(u)
    if len(d) < 60: continue
    c, h, l, vol = d.Close, d.High, d.Low, d.Volume
    t = len(d) - 1; r = rsi(c, 14); m = macd(c)
    hist = m["hist"] if "hist" in m else m.iloc[:, -1]
    avgv = vol.iloc[-21:-1].mean(); vx = vol.iloc[t] / avgv if avgv else np.nan
    if partial and in_session():                         # scale today's volume to a full day
        mins = (now().hour * 60 + now().minute) - 570; vx = vx * 390 / max(mins, 30)
    pos = (c.iloc[t] - l.iloc[t]) / (h.iloc[t] - l.iloc[t]) if h.iloc[t] > l.iloc[t] else 0.5
    sma50, sma200 = c.rolling(50).mean().iloc[t], c.rolling(200).mean().iloc[t] if len(c) >= 200 else np.nan
    bull, bear = [], []
    # key reversal / selling climax
    if l.iloc[t] < l.iloc[t - 20:t].min() and c.iloc[t] > c.iloc[t - 1] and pos >= 0.6:
        bull.append(("SELLING CLIMAX" if vx >= 2 else "KEY REVERSAL UP") + f" (new 20-day low {l.iloc[t]:.2f}, closed {pos:.0%} up the range, vol {vx:.1f}x)")
    if h.iloc[t] > h.iloc[t - 20:t].max() and c.iloc[t] < c.iloc[t - 1] and pos <= 0.4:
        bear.append(("BUYING CLIMAX" if vx >= 2 else "KEY REVERSAL DOWN") + f" (new 20-day high {h.iloc[t]:.2f}, closed {pos:.0%} up the range, vol {vx:.1f}x)")
    # island reversals: a gap one way within 10 days, then today gaps clear of every bar since
    for g in range(t - 10, t):
        if h.iloc[g] < l.iloc[g - 1] and l.iloc[t] > h.iloc[g:t].max():
            bull.append(f"ISLAND REVERSAL BOTTOM (gap down {d.index[g]:%m-%d}, today's low {l.iloc[t]:.2f} above the island high {h.iloc[g:t].max():.2f})"); break
        if l.iloc[g] > h.iloc[g - 1] and h.iloc[t] < l.iloc[g:t].min():
            bear.append(f"ISLAND REVERSAL TOP (gap up {d.index[g]:%m-%d}, today's high {h.iloc[t]:.2f} under the island low {l.iloc[g:t].min():.2f})"); break
    # daily RSI divergence at a fresh extreme (today or the last 3 days vs a pivot in the prior 40 days)
    lows = [i for i in pivots(l, "low") if i >= t - 40]; highs = [i for i in pivots(h, "high") if i >= t - 40]
    j = int(np.argmin(l.values[t - 3:t + 1])) + t - 3
    if lows and l.iloc[j] < l.iloc[lows[-1]] and r.iloc[j] > r.iloc[lows[-1]] + 2 and lows[-1] < j - 3:
        bull.append(f"RSI BULLISH DIVERGENCE (low {l.iloc[j]:.2f} under {l.iloc[lows[-1]]:.2f}, RSI {r.iloc[j]:.0f} vs {r.iloc[lows[-1]]:.0f})")
    j2 = int(np.argmax(h.values[t - 3:t + 1])) + t - 3
    if highs and h.iloc[j2] > h.iloc[highs[-1]] and r.iloc[j2] < r.iloc[highs[-1]] - 2 and highs[-1] < j2 - 3:
        bear.append(f"RSI BEARISH DIVERGENCE (high {h.iloc[j2]:.2f} over {h.iloc[highs[-1]]:.2f}, RSI {r.iloc[j2]:.0f} vs {r.iloc[highs[-1]]:.0f})")
    # MACD histogram turning
    if hist.iloc[t] < 0 and hist.iloc[t] > hist.iloc[t - 1] and hist.iloc[t - 1] <= hist.iloc[t - 2]:
        bull.append("MACD histogram turning up below zero")
    if hist.iloc[t] > 0 and hist.iloc[t] < hist.iloc[t - 1] and hist.iloc[t - 1] >= hist.iloc[t - 2]:
        bear.append("MACD histogram turning down above zero")
    trend = ("above" if c.iloc[t] > sma50 else "below") + " 50-day, " + ("above" if c.iloc[t] > sma200 else "below") + " 200-day"
    hi20, lo20 = h.iloc[t - 20:t + 1].max(), l.iloc[t - 20:t + 1].min()
    head = f"{u} {c.iloc[t]:.2f} ({100*(c.iloc[t]/c.iloc[t-1]-1):+.1f}%) · RSI {r.iloc[t]:.0f} · {trend}"
    if not bull and not bear:
        emit(INFO, head, "no reversal evidence"); continue
    for side, ev in (("long", bull), ("short", bear)):
        if not ev: continue
        strength = "STRONG" if len(ev) >= 3 else "MODERATE" if len(ev) == 2 else "WEAK"
        with_trend = (side == "long" and c.iloc[t] > sma200) or (side == "short" and c.iloc[t] < sma200)
        R = hi20 - lo20                                   # Murphy: a reversal retraces 33-50% of the prior swing
        if side == "long":
            trig, stop_u = h.iloc[t], l.iloc[t]
            t33, t50 = lo20 + 0.33 * R, lo20 + 0.50 * R
            if t33 <= trig: t33, t50 = trig + 0.33 * (trig - stop_u) * 3, trig + 0.5 * (trig - stop_u) * 3
        else:
            trig, stop_u = l.iloc[t], h.iloc[t]
            t33, t50 = hi20 - 0.33 * R, hi20 - 0.50 * R
            if t33 >= trig: t33, t50 = trig - 0.33 * (stop_u - trig) * 3, trig - 0.5 * (stop_u - trig) * 3
        plan_txt = (f"tomorrow after 10:00: {'buy' if side == 'long' else 'sell short'} only on a break {'above' if side == 'long' else 'below'} "
                    f"{trig:.2f} · stop beyond {stop_u:.2f} · targets {t33:.2f} / {t50:.2f}")
        etf = [(e, lv) for e, lv in etfs.items() if (lv > 0) == (side == "long")]
        if etf:
            e, lv = etf[0]; ec = yf.Ticker(e).history(period="5d").Close.iloc[-1]
            mp = lambda x: ec * (1 + lv * (x / c.iloc[t] - 1))
            plan_txt += f" · in {e}: trigger ~{mp(trig):.2f}, stop ~{min(mp(stop_u), mp(trig)*(1-MIN_STOP)):.2f}, targets ~{mp(t33):.2f} / ~{mp(t50):.2f}"
        tag = WATCH if strength == "WEAK" or partial else ACTION
        emit(tag, f"{strength} {'BULLISH' if side == 'long' else 'BEARISH'} REVERSAL CANDIDATE {u}" + ("" if with_trend else " (against the 200-day trend: smaller size)"),
             head, "evidence: " + "; ".join(ev), plan_txt, "a swing candidate, not a trade yet; not backtested")

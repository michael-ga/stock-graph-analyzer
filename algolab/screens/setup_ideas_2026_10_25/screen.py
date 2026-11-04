"""Screen all 12 pasted setup ideas on SNXX / SNDQ / NBIZ / NBIS(=NBIC proxy).

PRE-REGISTERED (written before any result was seen):
- Data: yfinance 5m (prepost) for the last 60 sessions. SCREEN = most recent 40
  sessions; HOLDOUT = the 20 before them, not looked at until the top 2 are picked.
- Long-only on every instrument; the inverse ETF of each pair supplies the short
  side (SNXX long = SNDK long, SNDQ long = SNDK short, NBIZ long = NBIS short,
  NBIS long = NBIC proxy; NBIC itself trades a median 9 of 78 regular 5-min bars).
- Common rules: entry at the trigger bar's close (stop orders at their level),
  stop buffer 0.1%, skip if target < 1R away, stop-first judging, flatten at the
  15:55 bar close, one trade per idea per instrument per day, $0.02/share round
  trip cost. No volume rules (the repo's rvol gauge is a known clock artifact).
- Selection rule for the top 2: >= 8 trades in the screen; avg net R > 0 in both
  20-day halves; avg net R > 0 with the best underlying-day removed; positive on
  both underlyings (SNDK pair, NBIS pair). Rank survivors by avg net R.

Run (results: algolab/LEARNINGS.md, 2026-10-25):
    python algolab/screens/setup_ideas_2026_10_25/fetch.py
    python algolab/screens/setup_ideas_2026_10_25/screen.py --part screen|holdout|all [--dump]
    python algolab/screens/setup_ideas_2026_10_25/null.py       # after --part all

I1 and I9 became stockanalyzer/setups/silver_bullet.py and double_bottom.py; the
harness there reproduces these trades. Two bugs were fixed before the picks were
final: a float-rounding skip of exact-1R targets in trade(), and I11 sizing its
legs with the whole day's bars (lookahead).
"""
from __future__ import annotations

import argparse
import math
import os
import pathlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

DATA = pathlib.Path(os.environ.get(
    "SETUP_SCREEN_DATA", pathlib.Path(__file__).resolve().parents[3] / ".local" / "setup-screen"))
TRADED = ["SNXX", "SNDQ", "NBIZ", "NBIS"]
UNDERLYING = {"SNXX": "SNDK", "SNDQ": "SNDK", "NBIZ": "NBIS", "NBIS": "NBIS"}
COST_PRICE = {"NBIS": 8.7}          # NBIS rows stand in for NBIC (~$8.7 2x ETF)
LEV = {"NBIS": 2.0}
BUF, MIN_RR, COST = 0.001, 1.0, 0.02


def hm(t):
    return t.strftime("%H:%M")


@dataclass
class Day:
    tk: str
    day: pd.Timestamp
    s: pd.DataFrame            # regular session 09:30..15:55
    pre: pd.DataFrame          # today's pre-market 04:00..09:25
    prev: pd.DataFrame | None  # previous regular session
    prev_close: float | None
    bias_up: bool | None       # prev daily close > SMA20
    atr: float | None          # daily ATR14 as of prev day


def load(tk):
    i5 = pd.read_pickle(DATA / f"{tk}_5m.pkl")
    d = pd.read_pickle(DATA / f"{tk}_1d.pkl")
    d.index = pd.DatetimeIndex(d.index).tz_localize(None).normalize()
    tr = pd.concat([d["high"] - d["low"], (d["high"] - d["close"].shift()).abs(),
                    (d["low"] - d["close"].shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    d["sma20"] = d["close"].rolling(20).mean()
    reg = i5.between_time("09:30", "15:55")
    pre = i5.between_time("04:00", "09:25")
    days = sorted({pd.Timestamp(x) for x in reg.index.tz_localize(None).normalize()})
    out, prev = [], None
    for day in days:
        s = reg[reg.index.tz_localize(None).normalize() == day]
        if len(s) < 60:                      # half-days / broken feeds
            prev = s if len(s) else prev
            continue
        p = pre[pre.index.tz_localize(None).normalize() == day]
        dp = d[d.index < day]
        pc = float(prev["close"].iloc[-1]) if prev is not None and len(prev) else None
        bias = (bool(dp["close"].iloc[-1] > dp["sma20"].iloc[-1])
                if len(dp) >= 20 and not math.isnan(dp["sma20"].iloc[-1]) else None)
        atr = float(dp["atr"].iloc[-1]) if len(dp) >= 15 else None
        out.append(Day(tk, day, s, p, prev, pc, bias, atr))
        prev = s
    return out


# --------------------------------------------------------------------------- #
def judge(s, k, entry, stop, target, *, trail_ema=None, fill_bar_stop_only=False):
    """Stop-first from bar k+1 (or bar k itself when a resting order filled on it)."""
    rows = s.iloc[k:] if fill_bar_stop_only else s.iloc[k + 1:]
    for j, (_, r) in enumerate(rows.iterrows()):
        if r["low"] <= stop:
            return "stop", stop
        if fill_bar_stop_only and j == 0:
            continue
        if target is not None and r["high"] >= target:
            return "target", target
        if trail_ema is not None and r["close"] < trail_ema.loc[r.name]:
            return "trail", float(r["close"])
    return "eod", float(s["close"].iloc[-1])


def trade(D, idea, k, entry, stop, target, **kw):
    if entry is None or stop is None or stop >= entry:
        return None
    if target is not None and (target - entry) < MIN_RR * (entry - stop) - 1e-9:
        return None
    status, px = judge(D.s, k, entry, stop, target, **kw)
    risk = entry - stop
    gross = (px - entry) / risk
    if D.tk in COST_PRICE:                   # NBIC proxy: cost at the ETF's price/leverage
        cost = (COST / COST_PRICE[D.tk]) / (LEV[D.tk] * risk / entry)
    else:
        cost = COST / risk
    return dict(tk=D.tk, und=UNDERLYING[D.tk], day=D.day, idea=idea, t=hm(D.s.index[k]),
                entry=entry, stop=stop, target=target, status=status,
                gross=gross, net=gross - cost, risk_pct=risk / entry * 100)


def window(s, a, b):
    t = s.index.strftime("%H:%M")
    return [i for i, x in enumerate(t) if a <= x <= b]


def med_range(df):
    return float((df["high"] - df["low"]).median())


def consolidation(s, k, n=12, atr=None):
    """(H, L, height) of the n bars before k when they form a tight range."""
    if k < n or atr is None:
        return None
    w = s.iloc[k - n:k]
    H, L = float(w["high"].max()), float(w["low"].min())
    return (H, L, H - L, w) if (H - L) <= 0.25 * atr else None


def vwap(s):
    tp = (s["high"] + s["low"] + s["close"]) / 3
    v = s["volume"].replace(0, np.nan).fillna(1.0)
    return (tp * v).cumsum() / v.cumsum()


# --------------------------------------------------------------------------- #
# The ideas (long form). Each returns a trade dict or None.
# --------------------------------------------------------------------------- #
def i1_silver_bullet(D):
    """ICT Silver Bullet, long mirror: bullish bias; a bullish FVG completes inside
    09:30-10:30 in the discount half of the session range; limit at its CE; stop
    under candle 2; target = nearest liquidity above giving >= 1R."""
    if not D.bias_up:
        return None
    s = D.s
    win = window(s, "09:30", "10:25")
    for k in win:
        if k < 2:
            continue
        b1, b2, b3 = s.iloc[k - 2], s.iloc[k - 1], s.iloc[k]
        if not (b3["low"] > b1["high"] and b2["close"] > b2["open"]):
            continue
        lo, hi = float(s["low"].iloc[:k + 1].min()), float(s["high"].iloc[:k + 1].max())
        ce = (b1["high"] + b3["low"]) / 2
        if ce > lo + 0.5 * (hi - lo):                 # premium -> skip
            continue
        stop = float(b2["low"]) * (1 - BUF)
        for f in range(k + 1, len(s)):
            if hm(s.index[f]) > "10:25":
                break
            if s["low"].iloc[f] <= ce:
                liq = [hi, *([D.pre["high"].max()] if len(D.pre) else [])]
                if D.prev is not None:
                    liq += [D.prev["high"].max(), D.prev.between_time("13:30", "15:55")["high"].max()]
                risk = ce - stop
                above = sorted(x for x in liq if x - ce >= MIN_RR * risk)
                if not above:
                    return None
                return trade(D, "I1 silver-bullet", f, ce, stop, above[0], fill_bar_stop_only=True)
        return None
    return None


def i2_judas_prev_pm(D):
    """Prev-day PM range (13:30-16:00) sweep: bullish bias; price takes the prev PM
    low; enter on the first close back above it (09:30-11:00); stop under the sweep
    low; target the prev PM high."""
    if not D.bias_up or D.prev is None:
        return None
    pm = D.prev.between_time("13:30", "15:55")
    pl, ph = float(pm["low"].min()), float(pm["high"].max())
    s, swept = D.s, False
    for k in window(s, "09:30", "10:55"):
        if s["low"].iloc[k] < pl:
            swept = True
        if swept and s["close"].iloc[k] > pl:
            stop = float(s["low"].iloc[:k + 1].min()) * (1 - BUF)
            return trade(D, "I2 judas-prevPM", k, float(s["close"].iloc[k]), stop, ph)
    return None


def i3_premarket_raid(D):
    """'London raid' adapted to stocks: today's pre-market range; 09:30-10:30 sweep
    of the pre-market low, then MSS = close above the high of the 3 bars before the
    sweep low; stop under the sweep low; target the pre-market high."""
    if len(D.pre) < 6:
        return None
    pl, ph = float(D.pre["low"].min()), float(D.pre["high"].max())
    s = D.s
    for k in window(s, "09:30", "10:25"):
        lows = s["low"].iloc[:k + 1]
        if lows.min() >= pl:
            continue
        j = int(np.argmin(lows.values))
        ref = float(s["high"].iloc[max(0, j - 3):j + 1].max()) if j > 0 else float(s["high"].iloc[0])
        if k > j and s["close"].iloc[k] > ref:
            return trade(D, "I3 premarket-raid", k, float(s["close"].iloc[k]),
                         float(lows.min()) * (1 - BUF), ph)
    return None


def i4_org_pm_reversion(D):
    """Opening-range gap + PM reversion: gap-down >= 1%; after 13:30 the lunch low
    (12:00-13:30) is swept and reclaimed by a close; target = the 09:30 open (into
    the gap); stop under the sweep low."""
    if D.prev_close is None:
        return None
    s = D.s
    o = float(s["open"].iloc[0])
    if o > D.prev_close * 0.99:
        return None
    lunch = s.between_time("12:00", "13:25")
    ll = float(lunch["low"].min())
    swept, low = False, None
    for k in window(s, "13:30", "15:00"):
        if s["low"].iloc[k] < ll:
            swept = True
        if swept:
            low = float(s["low"].iloc[window(s, "13:30", "15:00")[0]:k + 1].min())
            if s["close"].iloc[k] > ll:
                return trade(D, "I4 ORG-PM-reversion", k, float(s["close"].iloc[k]),
                             low * (1 - BUF), o)
    return None


def i5_lunch_raid(D):
    """NY lunch raid: lunch range 12:00-13:30; 13:30-14:30 sweep of the lunch low and
    a close back above it; stop under the sweep low; target the lunch high."""
    s = D.s
    lunch = s.between_time("12:00", "13:25")
    ll, lh = float(lunch["low"].min()), float(lunch["high"].max())
    w = window(s, "13:30", "14:25")
    swept = False
    for k in w:
        if s["low"].iloc[k] < ll:
            swept = True
        if swept and s["close"].iloc[k] > ll:
            low = float(s["low"].iloc[w[0]:k + 1].min())
            return trade(D, "I5 lunch-raid", k, float(s["close"].iloc[k]), low * (1 - BUF), lh)
    return None


def i6_prev_am_draw(D):
    """Prev AM session draw, bullish mirror: bias up; a close above the prev PM high
    (09:30-12:00); enter on the first pullback that tags the broken level and closes
    above it; stop under that bar's low; target the prev AM (09:30-12:00) high."""
    if not D.bias_up or D.prev is None:
        return None
    ph = float(D.prev.between_time("13:30", "15:55")["high"].max())
    ah = float(D.prev.between_time("09:30", "11:55")["high"].max())
    s, broke = D.s, None
    for k in window(s, "09:30", "11:55"):
        c, l = float(s["close"].iloc[k]), float(s["low"].iloc[k])
        if broke is None:
            if c > ph:
                broke = k
            continue
        if l <= ph * (1 + BUF) and c > ph:
            return trade(D, "I6 prevAM-draw", k, c, l * (1 - BUF), ah)
    return None


def i7_consolidation_break(D):
    """Range break: 12-bar range no taller than 0.25 x daily ATR; a full close above
    it that isn't a sprint (bar range <= 2x median); stop under the range low;
    target entry + 2 x range height."""
    s = D.s
    for k in window(s, "10:30", "15:00"):
        c = consolidation(s, k, atr=D.atr)
        if c is None:
            continue
        H, L, h, w = c
        bar = s.iloc[k]
        if bar["close"] > H and (bar["high"] - bar["low"]) <= 2 * med_range(w):
            e = float(bar["close"])
            return trade(D, "I7 range-break", k, e, L * (1 - BUF), e + 2 * h)
    return None


def i8_second_chance(D):
    """Break and retest: range as I7; after a close above it, a retest bar tags the
    range high (within 12 bars); enter on the first close above the retest bar's
    high (within 6 bars); stop under the retest low; target H + 2 x height."""
    s = D.s
    w = window(s, "10:30", "15:00")
    for k in w:
        c = consolidation(s, k, atr=D.atr)
        if c is None or s["close"].iloc[k] <= c[0]:
            continue
        H, L, h, _ = c
        for r in range(k + 1, min(k + 13, len(s))):
            if s["close"].iloc[r] < L:
                return None
            if s["low"].iloc[r] <= H * (1 + 0.0015):
                for e in range(r + 1, min(r + 7, len(s))):
                    if hm(s.index[e]) > "15:00":
                        return None
                    if s["close"].iloc[e] > s["high"].iloc[r]:
                        lo = float(s["low"].iloc[r:e + 1].min())
                        return trade(D, "I8 second-chance", e, float(s["close"].iloc[e]),
                                     lo * (1 - BUF), H + 2 * h)
                return None
        return None
    return None


def i9_extension_double_bottom(D):
    """Extension fade: a drop >= 3x the median bar range within 6 bars into a low
    P1; a second low P2 within 0.25 of that drop; buy-stop at the neckline (high
    between); stop under the lower low; target 1R."""
    s = D.s
    hi, lo = s["high"].values, s["low"].values
    for p1 in window(s, "09:50", "14:30"):
        if p1 < 24:
            base = med_range(s.iloc[:p1]) if p1 >= 6 else None
        else:
            base = med_range(s.iloc[p1 - 24:p1])
        if not base:
            continue
        drop = hi[max(0, p1 - 6):p1 + 1].max() - lo[p1]
        if drop < 3 * base or lo[p1] > lo[max(0, p1 - 6):p1 + 1].min():
            continue
        # P1 must be a pivot low (next 2 bars higher lows)
        if p1 + 2 >= len(s) or lo[p1 + 1] <= lo[p1] or lo[p1 + 2] <= lo[p1]:
            continue
        for p2 in range(p1 + 3, min(p1 + 19, len(s) - 1)):
            neck = hi[p1 + 1:p2].max()
            if lo[p2] < lo[p1] - 0.25 * drop:
                break
            if abs(lo[p2] - lo[p1]) <= 0.25 * drop and lo[p2 + 1] > lo[p2]:
                for e in range(p2 + 1, min(p2 + 13, len(s))):
                    if lo[e] < min(lo[p1], lo[p2]):
                        break
                    if hi[e] >= neck:
                        entry = max(neck, float(s["open"].iloc[e]))
                        stop = min(lo[p1], lo[p2]) * (1 - BUF)
                        return trade(D, "I9 extension-dbl-bottom", e, entry, stop,
                                     entry + (entry - stop), fill_bar_stop_only=True)
                break
        # only the first qualifying extension per day
        return None
    return None


def i10_failed_breakdown(D):
    """Failed range break, long: range as I7; a bar trades below the range low; a
    close back above it within 6 bars -> long; stop under the fakeout low; target
    the range high."""
    s = D.s
    for k in window(s, "10:30", "15:00"):
        c = consolidation(s, k, atr=D.atr)
        if c is None or s["low"].iloc[k] >= c[1]:
            continue
        H, L, h, _ = c
        for e in range(k, min(k + 7, len(s))):
            if hm(s.index[e]) > "15:00":
                return None
            if s["close"].iloc[e] > L:
                lo = float(s["low"].iloc[k:e + 1].min())
                return trade(D, "I10 failed-breakdown", e, float(s["close"].iloc[e]),
                             lo * (1 - BUF), H)
        return None
    return None


def i11_abc_ema9(D):
    """ABC scalp: leg A up from the session low (>= 3x median bar range), leg B
    retraces 38-79% without breaking the low; buy-stop at A's high; stop under B;
    exit on a close below the 5m EMA9 (or VWAP if VWAP is above the entry)."""
    s = D.s
    hi, lo = s["high"].values, s["low"].values
    ema9 = s["close"].ewm(span=9, adjust=False).mean()
    vw = vwap(s)
    for b in window(s, "10:00", "15:00"):
        if b < 6:
            continue
        base = med_range(s.iloc[:b])            # as of b — no lookahead
        i0 = int(np.argmin(lo[:b]))
        if b - i0 < 3:
            continue
        ia = i0 + int(np.argmax(hi[i0:b]))
        A, L0, B = hi[ia], lo[i0], lo[ia:b + 1].min()
        if ia <= i0 or ia >= b or A - L0 < 3 * base:
            continue
        ret = (A - B) / (A - L0)
        if not (0.382 <= ret <= 0.786) or lo[b] != B:
            continue
        for e in range(b + 1, min(b + 13, len(s))):
            if lo[e] < B:
                break
            if hi[e] >= A:
                entry = max(A, float(s["open"].iloc[e]))
                tgt = float(vw.iloc[e]) if vw.iloc[e] > entry else None
                if tgt is not None and tgt - entry < MIN_RR * (entry - B):
                    tgt = None
                return trade(D, "I11 ABC-ema9", e, entry, B * (1 - BUF), tgt,
                             trail_ema=ema9, fill_bar_stop_only=True)
        return None
    return None


def i12_res_becomes_support(D):
    """Prior resistance -> support: the latest session pivot high (2 bars each side)
    is broken by a close; within 12 bars a bar tags it and closes back above; stop
    under that wick; target the swing high made since the break."""
    s = D.s
    hi, lo, cl = s["high"].values, s["low"].values, s["close"].values
    for k in window(s, "10:00", "15:00"):
        piv = [i for i in range(2, k - 2) if hi[i] == hi[i - 2:i + 3].max()]
        if not piv:
            continue
        R = hi[piv[-1]]
        if not (cl[k] > R and cl[k - 1] <= R):
            continue
        for p in range(k + 1, min(k + 13, len(s))):
            if hm(s.index[p]) > "15:00":
                return None
            if lo[p] <= R * (1 + 0.0015):
                if cl[p] > R:
                    tgt = hi[k:p].max()
                    return trade(D, "I12 res->support", p, float(cl[p]), lo[p] * (1 - BUF), tgt)
                return None
        continue
    return None


IDEAS = [i1_silver_bullet, i2_judas_prev_pm, i3_premarket_raid, i4_org_pm_reversion,
         i5_lunch_raid, i6_prev_am_draw, i7_consolidation_break, i8_second_chance,
         i9_extension_double_bottom, i10_failed_breakdown, i11_abc_ema9,
         i12_res_becomes_support]


# --------------------------------------------------------------------------- #
def summarize(df, label):
    print(f"\n=== {label} ===")
    hdr = (f"{'idea':<24}{'n':>4}{'days':>5}{'WR':>5}{'avgR':>7}{'totR':>7}"
           f"{'H1':>7}{'H2':>7}{'-best':>7}{'SNDK':>7}{'NBIS':>7}  per-ticker avgR (n)")
    print(hdr)
    rows = []
    for idea, g in df.groupby("idea", sort=False):
        days = sorted(df["day"].unique())
        mid = days[len(days) // 2] if days else None
        n = len(g)
        ud = g.groupby(["und", "day"])["net"].sum()
        best = ud.idxmax() if len(ud) else None
        rest = g[~((g["und"] == best[0]) & (g["day"] == best[1]))] if best else g
        h1 = g[g["day"] < mid]["net"].mean()
        h2 = g[g["day"] >= mid]["net"].mean()
        sn = g[g["und"] == "SNDK"]["net"].mean()
        nb = g[g["und"] == "NBIS"]["net"].mean()
        per = " ".join(f"{tk}:{x['net'].mean():+.2f}({len(x)})" for tk, x in g.groupby("tk"))
        r = dict(idea=idea, n=n, days=len(ud), wr=(g["net"] > 0).mean() * 100,
                 avg=g["net"].mean(), tot=g["net"].sum(), h1=h1, h2=h2,
                 nobest=rest["net"].mean() if len(rest) else float("nan"), sndk=sn, nbis=nb)
        rows.append(r)
        print(f"{idea:<24}{n:>4}{r['days']:>5}{r['wr']:>4.0f}%{r['avg']:>+7.2f}{r['tot']:>+7.1f}"
              f"{h1:>+7.2f}{h2:>+7.2f}{r['nobest']:>+7.2f}{sn:>+7.2f}{nb:>+7.2f}  {per}")
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["screen", "holdout", "all"], default="screen")
    ap.add_argument("--ideas", default="")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()
    data = {tk: load(tk) for tk in TRADED}
    all_days = sorted({D.day for v in data.values() for D in v})
    screen_days, hold_days = set(all_days[-40:]), set(all_days[:-40])
    pick = {"screen": screen_days, "holdout": hold_days, "all": set(all_days)}[a.part]
    ideas = [f for f in IDEAS if not a.ideas or any(f.__name__.startswith(x) for x in a.ideas.split(","))]
    trades = []
    for tk, days in data.items():
        for D in days:
            if D.day not in pick:
                continue
            for f in ideas:
                t = f(D)
                if t:
                    trades.append(t)
    df = pd.DataFrame(trades)
    print(f"{a.part}: {min(pick).date()}..{max(pick).date()} ({len(pick)} sessions), "
          f"{len(df)} trades")
    res = summarize(df, a.part)
    if a.dump:
        pd.set_option("display.width", 200)
        print(df.sort_values(["idea", "day", "tk"]).to_string(index=False,
              columns=["idea", "tk", "day", "t", "entry", "stop", "target", "status", "gross", "net", "risk_pct"]))
    df.to_pickle(DATA / f"trades_{a.part}.pkl")
    if a.part == "screen":
        ok = res[(res.n >= 8) & (res.h1 > 0) & (res.h2 > 0) & (res.nobest > 0)
                 & (res.sndk > 0) & (res.nbis > 0)].sort_values("avg", ascending=False)
        print("\nPASS the pre-registered rule:", list(ok.idea) or "none")


if __name__ == "__main__":
    main()

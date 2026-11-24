"""Market-wide movers watcher: what is trending today, in the US and abroad, and what starts moving now.

Start-up prints one snapshot (other markets, US top gainers / losers / most active with relative volume,
and a board of liquid leveraged ETFs). Then it polls every --every seconds until 16:00 ET and prints a
tagged line only when something changes:
  👀 WATCH  a new name enters the top movers with |change| >= --min-chg % and time-adjusted rel. volume >= --min-rv
  👀 WATCH  a burst: any tracked name moves >= --burst % (leveraged ETFs: 2x that) within 15 minutes
  · INFO    every 15 min: the top 3 gainers / losers and the leaders of the leveraged board

Relative volume is time-adjusted: volume / (3-month average daily volume x share of the session elapsed).
Early and late in the day it overstates (volume is U-shaped), so treat it as a ranking, not a measurement.

  python watch_trending.py [--every 120] [--min-chg 5] [--min-rv 2] [--burst 2]
Data: Yahoo screeners (yfinance.screen) and 5-min bars. Read-only; places no orders.
"""
import argparse, time, sys, os, collections, datetime as dt, warnings
import yfinance as yf
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))
from common import now

ABROAD = {"^N225": "Nikkei", "^KS11": "KOSPI", "^HSI": "Hang Seng", "000001.SS": "Shanghai", "^TWII": "Taiwan",
          "^GDAXI": "DAX", "^FTSE": "FTSE 100", "^STOXX50E": "Euro Stoxx 50", "^AEX": "AEX",
          "ES=F": "S&P fut", "NQ=F": "Nasdaq fut", "BTC-USD": "Bitcoin", "CL=F": "Crude", "GC=F": "Gold"}
LEV = ["SOXL", "SOXS", "TQQQ", "SQQQ", "NVDL", "NVDD", "TSLL", "TSLQ", "AMDL", "DAMD", "SNXX", "SNDQ", "NBIZ",
       "INTW", "MSTU", "CONL", "LABU", "LABD", "FAS", "FAZ", "TNA", "TZA", "UVXY"]
SCREENS = {"day_gainers": "gainers", "day_losers": "losers", "most_actives": "active"}


def tag(kind, msg):
    print(f"[{now():%H:%M}] {kind} | {msg}", flush=True)


def session_frac():
    t = now(); m = (t.hour * 60 + t.minute) - (9 * 60 + 30)
    return min(max(m / 390, 0.05), 1.0)


def screens(count=25):
    out = {}
    for q, name in SCREENS.items():
        try:
            out[name] = yf.screen(q, count=count)["quotes"]
        except Exception as e:
            tag("· INFO", f"screener {q} failed: {e}"); out[name] = []
    return out


def rv(q):
    avg = q.get("averageDailyVolume3Month") or 0
    return (q.get("regularMarketVolume") or 0) / (avg * session_frac()) if avg else None


def fmt(q):
    r = rv(q); n = (q.get("shortName") or "")[:24]
    return f"{q['symbol']} {q.get('regularMarketChangePercent', 0):+.1f}% @ {q.get('regularMarketPrice', 0):.2f}" + \
           (f" · rv {r:.1f}x" if r else "") + f" · {n}"


def bars(tickers):
    d = yf.download(tickers, period="1d", interval="5m", group_by="ticker", progress=False, auto_adjust=False, threads=True)
    out = {}
    for t in tickers:
        try:
            x = d[t].dropna(subset=["Close"])
            if len(x): out[t] = x
        except Exception:
            pass
    return out


def abroad_snapshot():
    rows = []
    for t, name in ABROAD.items():
        try:
            h = yf.Ticker(t).history(period="5d", interval="1d")
            if len(h) >= 2:
                c, p = h.Close.iloc[-1], h.Close.iloc[-2]
                rows.append((abs(c / p - 1), f"{name} {(c / p - 1) * 100:+.1f}%" + (f" (last {h.index[-1]:%m-%d})" if h.index[-1].date() != now().date() else "")))
        except Exception:
            pass
    return [r for _, r in rows]


def lev_board(prev_close):
    b = bars(LEV); rows = []
    for t, x in b.items():
        pc = prev_close.get(t)
        if not pc: continue
        p = (x.High + x.Low + x.Close) / 3; vw = (p * x.Volume).cumsum() / x.Volume.cumsum().replace(0, float("nan"))
        rows.append((t, x.Close.iloc[-1], (x.Close.iloc[-1] / pc - 1) * 100, (x.Close.iloc[-1] / vw.iloc[-1] - 1) * 100 if vw.notna().any() else 0.0))
    return sorted(rows, key=lambda r: -abs(r[2]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=120)
    ap.add_argument("--min-chg", type=float, default=5.0)
    ap.add_argument("--min-rv", type=float, default=2.0)
    ap.add_argument("--burst", type=float, default=2.0)
    a = ap.parse_args()

    prev_close = {}
    for t in LEV:
        try:
            h = yf.Ticker(t).history(period="5d", interval="1d")
            if len(h) >= 2: prev_close[t] = h.Close.iloc[-2] if h.index[-1].date() == now().date() else h.Close.iloc[-1]
        except Exception:
            pass

    tag("· INFO", "trending watcher started · other markets: " + " · ".join(abroad_snapshot()))
    s = screens()
    for name in ("gainers", "losers", "active"):
        tag("· INFO", f"top {name}: " + " | ".join(fmt(q) for q in s[name][:8]))
    hot = [q for q in s["gainers"] + s["losers"] if abs(q.get("regularMarketChangePercent", 0)) >= a.min_chg and (rv(q) or 0) >= a.min_rv]
    if hot:
        tag("👀 WATCH", "big movers on heavy volume today: " + " | ".join(fmt(q) for q in sorted(hot, key=lambda q: -(rv(q) or 0))[:10]))
    lb = lev_board(prev_close)
    tag("· INFO", "leveraged ETFs: " + " · ".join(f"{t} {c:+.1f}% ({v:+.1f}% vs VWAP)" for t, _, c, v in lb[:10]))

    seen = {q["symbol"] for name in ("gainers", "losers") for q in s[name][:10]}
    hist = collections.defaultdict(collections.deque)     # symbol -> (time, price)
    burst_seen, last_info = {}, time.time()

    def remember(sym, px, lev=False):
        t = time.time(); h = hist[sym]; h.append((t, px))
        while h and t - h[0][0] > 15 * 60: h.popleft()
        if len(h) >= 2 and t - h[0][0] >= 4 * 60:
            mv = (px / h[0][1] - 1) * 100; th = a.burst * (2 if lev else 1)
            key = (sym, 1 if mv > 0 else -1)
            if abs(mv) >= th and t - burst_seen.get(key, 0) > 20 * 60:
                burst_seen[key] = t
                return mv, (t - h[0][0]) / 60
        return None

    while True:
        t = now()
        if (t.hour, t.minute) >= (16, 0):
            tag("· INFO", "16:00 ET: market closed, trending watcher stops"); return
        time.sleep(a.every)
        s = screens()
        for name in ("gainers", "losers"):
            for i, q in enumerate(s[name][:10]):
                sym = q["symbol"]; r = rv(q) or 0
                if sym not in seen and abs(q.get("regularMarketChangePercent", 0)) >= a.min_chg and r >= a.min_rv:
                    seen.add(sym); tag("👀 WATCH", f"NEW in top {name} (#{i + 1}): {fmt(q)}")
        for q in {q["symbol"]: q for name in s for q in s[name]}.values():
            b = remember(q["symbol"], q.get("regularMarketPrice") or 0)
            if b:
                tag("👀 WATCH", f"BURST {q['symbol']} {b[0]:+.1f}% in {b[1]:.0f} min · day {q.get('regularMarketChangePercent', 0):+.1f}% · "
                                 f"rv {rv(q) or 0:.1f}x · {(q.get('shortName') or '')[:24]}")
        lb = lev_board(prev_close)
        for sym, px, chg, vw in lb:
            b = remember(sym, px, lev=True)
            if b:
                tag("👀 WATCH", f"BURST {sym} {b[0]:+.1f}% in {b[1]:.0f} min · day {chg:+.1f}% · {vw:+.1f}% vs VWAP (leveraged ETF)")
        if time.time() - last_info >= 15 * 60:
            last_info = time.time()
            tag("· INFO", "top gainers: " + " | ".join(fmt(q) for q in s["gainers"][:3]) + " ‖ top losers: "
                + " | ".join(fmt(q) for q in s["losers"][:3]) + " ‖ lev: "
                + " · ".join(f"{t} {c:+.1f}%" for t, _, c, _ in lb[:4]))


if __name__ == "__main__":
    main()

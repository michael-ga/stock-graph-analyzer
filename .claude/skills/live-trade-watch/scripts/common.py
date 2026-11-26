"""Shared helpers for the live-trade-watch scripts: clock, data feeds, ETF mapping, sizing, message tags.

Data: Yahoo 5-min bars (price, VWAP, day low). If Yahoo's last bar is stale during the session (it froze for
30+ minutes on 2026-11-05), live prices come from Finnhub quotes (FINNHUB_KEY from the environment or .env)
and VWAP is held at Yahoo's last value until Yahoo resumes.
"""
import datetime as dt, json, os, sys, warnings
warnings.filterwarnings("ignore")
import yfinance as yf
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # emoji tags on a Windows console
except Exception:
    pass

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
STATE_DIR = os.path.join(ROOT, ".local", "live")
STATE = os.path.join(STATE_DIR, "state.json")
try:                                              # real US/Eastern, daylight time included
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                                 # Windows without the tzdata package
    from dateutil import tz as _tz
    ET = _tz.gettz("America/New_York")
FEE = float(os.environ.get("TRADE_FEE", "3.0"))   # round-trip fees in $
MIN_STOP = float(os.environ.get("MIN_STOP", "0.04"))  # minimum ETF stop distance (4%: 2x ETF on a volatile day)
SIZES = (300, 500)
RR_MIN = 1.5

# Message tags. Relay ACTION as a vivid block, WATCH as one line, INFO only if asked.
ACTION, WATCH, INFO = "🚨 ACTION", "👀 WATCH", "· INFO"


def now():
    return dt.datetime.now(ET)


def in_session(t=None):
    t = (t or now()).time()
    return dt.time(9, 30) <= t <= dt.time(16, 0)


def detached(args, **kw):
    """Start a helper (paper_trade.py) that outlives the scanner. The scanner runs under a Monitor that is killed and
    restarted every 30 minutes; a plain child process dies with it (11-11: both paper trades vanished without a result)."""
    import subprocess
    kw.setdefault("stdout", subprocess.DEVNULL); kw.setdefault("stderr", subprocess.DEVNULL); kw.setdefault("stdin", subprocess.DEVNULL)
    if os.name == "nt":
        base = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        for flags in (base | 0x01000000, base):          # 0x01000000 = CREATE_BREAKAWAY_FROM_JOB, refused if the job forbids it
            try:
                return subprocess.Popen(args, creationflags=flags, close_fds=True, **kw)
            except OSError:
                continue
    return subprocess.Popen(args, start_new_session=True, close_fds=True, **kw)


def alerts_log(day=None):
    """Every tagged line of the day, from every script, so a missed notification can be read back."""
    return os.path.join(STATE_DIR, f"alerts-{(day or now().date()):%Y-%m-%d}.log")


def emit(tag, *parts):
    line = f"[{now():%H:%M}] {tag} | " + " | ".join(str(p) for p in parts if p not in (None, ""))
    print(line, flush=True)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(alerts_log(), "a", encoding="utf-8") as f:
            f.write(f"{os.path.basename(sys.argv[0])}\t{line}\n")
    except Exception:
        pass


def load_env():
    if os.environ.get("FINNHUB_KEY"):
        return
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.startswith("FINNHUB_KEY="):
                os.environ["FINNHUB_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")


_fh = None


def finnhub():
    global _fh
    if _fh is None:
        load_env(); sys.path.insert(0, ROOT)
        from stockanalyzer.data.finnhub import FinnhubClient
        _fh = FinnhubClient()
    return _fh if _fh.available else None


def prev_close(t):
    try:
        d = yf.Ticker(t).history(period="5d", auto_adjust=False)
        d = d[d.index.tz_convert("America/New_York").date < now().date()]
        if len(d): return float(d.Close.iloc[-1])
    except Exception:
        pass
    q = finnhub() and finnhub().quote(t)
    return float(q.prev_close) if q and q.prev_close else None


def bars(t):
    """Today's regular-session 5-min bars with a running VWAP column, or None."""
    try:
        y = yf.Ticker(t).history(period="1d", interval="5m")
        if y.empty: return None
        y.index = y.index.tz_convert("America/New_York")
        y = y[y.index.date == now().date()].between_time("09:30", "15:55")
        if y.empty: return None
        tp = (y.High + y.Low + y.Close) / 3
        y["vwap"] = (tp * y.Volume).cumsum() / y.Volume.cumsum()
        return y
    except Exception:
        return None


def quote(t):
    f = finnhub()
    if f:
        q = f.quote(t)
        if q and q.price: return float(q.price)
    try:
        return float(yf.Ticker(t).fast_info.last_price)
    except Exception:
        return None


def yahoo_stale(y, minutes=12):
    """True when Yahoo's last 5-min bar is older than `minutes` during the session."""
    if y is None: return True
    last = y.index[-1].to_pydatetime().replace(tzinfo=None)
    return in_session() and (now().replace(tzinfo=None) - last).total_seconds() / 60 > minutes


def mapped(etf_prev, lev, und_prev, und_px):
    """ETF price implied by the underlying under the daily-reset leverage."""
    return etf_prev * (1 + lev * (und_px / und_prev - 1))


def plan(entry, stop, target):
    """Long the ETF: widen the stop to MIN_STOP if structure is tighter; sizes and $ risk/reward after fees."""
    if (entry - stop) / entry < MIN_STOP:
        stop = entry * (1 - MIN_STOP)
    out = dict(entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2))
    for cap in SIZES:
        sh = cap / entry
        out[f"s{cap}"] = [round(sh), round(sh * (entry - stop) + FEE), round(sh * (target - entry) - FEE)]
    sh = SIZES[0] / entry
    out["rr"] = round((sh * (target - entry) - FEE) / (sh * (entry - stop) + FEE), 2)
    out["take"] = out["rr"] >= RR_MIN
    return out


def write_state(**kw):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(kw, open(tmp, "w"), default=str)
    os.replace(tmp, STATE)


def _pivots(s, kind, k=2):
    """Indices of swing lows/highs: a bar lower/higher than the k bars on each side (only fully formed pivots)."""
    v = s.values; idx = []
    for i in range(k, len(v) - k):
        win = v[i - k:i + k + 1]
        if (kind == "low" and v[i] == win.min()) or (kind == "high" and v[i] == win.max()):
            idx.append(i)
    return idx


def context(y, ticker=None):
    """Murphy-style read of today's 5-min bars of the underlying (needs Yahoo bars with volume).

    rsi / adx (+DI vs -DI) / trend from the last two swing highs and lows / RSI divergence at the latest swing /
    volume of the last completed candle vs the session average.
    """
    sys.path.insert(0, ROOT)
    from stockanalyzer.analysis.indicators import rsi, adx
    out = dict(rsi=None, adx=None, trend="n/a", div=None, vol=None)
    if y is None or len(y) < 16:
        return out
    done = y.iloc[:-1]                                  # completed candles only
    r = rsi(done.Close, 14); a = adx(done.High, done.Low, done.Close, 14)
    out["rsi"] = round(float(r.iloc[-1]), 1)
    if a.adx.notna().any():
        out["adx"] = round(float(a.adx.iloc[-1]), 1)
        out["di"] = "down" if a.minus_di.iloc[-1] > a.plus_di.iloc[-1] else "up"
    # pivots before RSI has 14 bars of history (RSI pinned at 100/0) can't be compared
    lows = [i for i in _pivots(done.Low, "low") if i >= 15]; highs = [i for i in _pivots(done.High, "high") if i >= 15]
    if len(lows) >= 2 and len(highs) >= 2:
        hh = done.High.iloc[highs[-1]] > done.High.iloc[highs[-2]]; hl = done.Low.iloc[lows[-1]] > done.Low.iloc[lows[-2]]
        out["trend"] = "up (HH/HL)" if hh and hl else "down (LH/LL)" if not hh and not hl else "mixed"
    if len(lows) >= 2 and len(done) - lows[-1] <= 8:    # bullish divergence at a fresh swing low
        i, j = lows[-2], lows[-1]
        if done.Low.iloc[j] < done.Low.iloc[i] and r.iloc[j] > r.iloc[i] + 2:
            out["div"] = dict(kind="bullish", at=str(done.index[j])[11:16], price=round(float(done.Low.iloc[j]), 2),
                              prev=round(float(done.Low.iloc[i]), 2), rsi=round(float(r.iloc[j]), 1), rsi_prev=round(float(r.iloc[i]), 1))
    if len(highs) >= 2 and len(done) - highs[-1] <= 8:  # bearish divergence at a fresh swing high
        i, j = highs[-2], highs[-1]
        if done.High.iloc[j] > done.High.iloc[i] and r.iloc[j] < r.iloc[i] - 2:
            out["div"] = dict(kind="bearish", at=str(done.index[j])[11:16], price=round(float(done.High.iloc[j]), 2),
                              prev=round(float(done.High.iloc[i]), 2), rsi=round(float(r.iloc[j]), 1), rsi_prev=round(float(r.iloc[i]), 1))
    out["vol"] = tod_rvol(ticker, done.index[-1], done.Volume.iloc[-1]) if ticker else None
    return out


def vol_grade(x):
    """x = the candle's volume vs the SAME clock candle on prior sessions (tod_rvol)."""
    if x is None: return "volume n/a"
    return f"strong volume {x:.1f}x" if x >= 1.5 else f"weak volume {x:.1f}x" if x < 0.8 else f"normal volume {x:.1f}x"


_tod = {}


def tod_rvol(t, bar_time, volume):
    """Volume of the candle at ``bar_time`` vs the mean of the same clock candle over the
    prior 14 sessions. Intraday volume is U-shaped, so the old 'vs session average'
    called every midday candle weak and every opening candle strong (2026-11-10)."""
    key = (t, now().date())
    if key not in _tod:
        _tod[key] = None
        try:
            sys.path.insert(0, ROOT)
            from stockanalyzer.analysis.indicators import tod_volume_baseline
            h = yf.Ticker(t).history(period="1mo", interval="5m")
            h = h.rename(columns=str.lower)
            _tod[key] = tod_volume_baseline(h, now().replace(tzinfo=None))
        except Exception:
            pass
    base = _tod[key]
    if base is None or base.days < 3: return None
    m = base.bar.get(bar_time.strftime("%H:%M"))
    return round(float(volume) / m, 2) if m else None


def min_target_pct(stop_pct=MIN_STOP, cap=SIZES[0], rr=RR_MIN, fee=FEE):
    """The smallest ETF target move (%) that passes the R:R-after-fees gate at this stop."""
    return 100 * (rr * (cap * stop_pct + fee) + fee) / cap


def vix_read():
    """VIX level, % vs previous close, and % off today's high (fear easing when it falls back)."""
    try:
        d = yf.Ticker("^VIX").history(period="5d"); i = yf.Ticker("^VIX").history(period="1d", interval="5m")
        prev, last, hi = float(d.Close.iloc[-2]), float(i.Close.iloc[-1]), float(i.High.max())
        state = ("extreme" if last >= 30 or last / prev - 1 >= 0.20 else "elevated" if last >= 20 else "calm")
        return dict(level=round(last, 2), chg=round(100 * (last / prev - 1), 1), off_high=round(100 * (last / hi - 1), 1), state=state)
    except Exception:
        return None


def parse_watch(specs):
    """'SNDK=SNXX:2,SNDQ:-2' -> {'SNDK': {'SNXX': 2, 'SNDQ': -2}}"""
    out = {}
    for spec in specs:
        und, etfs = spec.split("=")
        out[und.upper()] = {e.split(":")[0].upper(): float(e.split(":")[1]) for e in etfs.split(",")}
    return out

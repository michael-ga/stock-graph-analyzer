"""Live setup watcher: one underlying drives its leveraged ETFs (long = lev > 0, short side = lev < 0).

    python watch_setups.py --watch SNDK=SNXX:2,SNDQ:-2 --watch NBIS=NBIZ:-2 --watch INTC=INTW:2 [--popup]

Rules, read on COMPLETED 5-min candles of the underlying. They live in stockanalyzer/setups/vwap_rules.py (one source
for this watcher and its backtest); every message carries the rule's evidence grade:
  FAILED-BOUNCE   high reaches VWAP and the candle closes below it with a rejection -> WATCH only (backtested negative)
  VWAP-RECLAIM    first close above VWAP after trading below it                       -> buy the long ETF ($300, untested)
  TREND-PULLBACK  ADX >= 30, a candle pulls back into the 20-EMA and closes back       -> untested
  OVERSOLD-BOUNCE day low >= 4% under VWAP, higher low, close over the bounce high      -> risky, untested
  FAILED GAP-UP (weak evidence, $300) / RECLAIMED GAP-DOWN (WATCH only, negative), 10:30-11:30
  NEW-LOW         close below the day's prior low on 1.2x volume                     -> WATCH only: don't chase
Signals the R:R gate rejects are summarized once per underlying every 15 minutes, not one line each.
Paper only: xfade-dbl-bottom on the ETFs' own candles starts paper_trade.py (no order; logged to paper_trades.csv).
Output lines are tagged ACTION / WATCH / INFO (see SKILL.md). State for the GUI goes to .local/live/state.json.
"""
import argparse, subprocess, sys, time, datetime as dt, os
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(__file__))
from common import *
sys.path.insert(0, ROOT)
import pandas as pd
from stockanalyzer.setups import double_bottom, vwap_rules as VR
from stockanalyzer.setups.base import SetupContext

ap = argparse.ArgumentParser()
ap.add_argument("--watch", action="append", required=True)
ap.add_argument("--popup", action="store_true", help="also show a Windows message box on ACTION (if the GUI isn't open)")
ap.add_argument("--short-exit", default="14:55", help="time stop for inverse (short) trades")
ap.add_argument("--long-exit", default="15:50")
ap.add_argument("--no-paper", action="store_true", help="don't paper-track xfade-dbl-bottom on the ETFs")
ap.add_argument("--no-paper-actions", action="store_true", help="don't paper-trade every ACTION entry ($300, logged to paper_trades.csv)")
a = ap.parse_args()
W = parse_watch(a.watch)
PREV = {t: prev_close(t) for u, e in W.items() for t in [u, *e]}
missing = [t for t, v in PREV.items() if not v]
if missing:
    sys.exit(f"no previous close for {missing}")
emit(INFO, "watcher started", ", ".join(f"{u} -> " + " ".join(f"{e}({l:+g}x)" for e, l in es.items()) for u, es in W.items()),
     "prev closes " + " ".join(f"{t} {v:.2f}" for t, v in PREV.items()))

st = {u: dict(last_bar=None, below=False, reclaimed=False, bucket=None, h=None, l=None, c=None, hist=[]) for u in W}
ref_vw, signals, sent, last_hb, feed = {}, [], set(), 0.0, "Yahoo"
ctx, vix, vix_t, vix_state = {}, None, 0.0, None
FB_COOLDOWN = dt.timedelta(minutes=30)       # one failed-bounce read per underlying per 30 min
fb_last = {}
skipped = defaultdict(Counter)                # underlying -> Counter(rule/why) since the last summary
SKIP_EVERY = 15 * 60
last_skip = time.time()


def etf_px(u, e, und_px, live):
    q = quote(e) if live else None
    return q or mapped(PREV[e], W[u][e], PREV[u], und_px)


def notes(u, side, vx):
    """Murphy checks attached to every signal: volume confirms, trend strength, divergence against, fear extreme."""
    c = ctx.get(u, {}); out, size_down = [vol_grade(vx if vx is not None else c.get("vol"))], False
    if vx is not None and vx < 0.8: size_down = True
    if c.get("adx") is not None:
        out.append(f"ADX {c['adx']:.0f} {'trending' if c['adx'] >= 25 else 'choppy' if c['adx'] < 20 else 'weak trend'} ({c.get('di', '?')})")
        if c["adx"] < 20: size_down = True
    d = c.get("div")
    if d and ((side == "short" and d["kind"] == "bullish") or (side == "long" and d["kind"] == "bearish")):
        out.append(f"{d['kind']} RSI divergence against this trade"); size_down = True
    if side == "short" and vix and vix["state"] == "extreme":
        out.append(f"VIX {vix['level']} extreme: bounce risk"); size_down = True
    return out, size_down


def act(u, e, sig, und_px, vw, lod, live, vx=None, exit_override=None, rule=None):
    """Turn an underlying signal into ETF orders. ``sig`` is a vwap_rules.UnderlyingSignal, or a
    (side, why, stop_und, tgt_und) tuple for the gap setup."""
    if isinstance(sig, VR.UnderlyingSignal):
        rule, side, why, stop_und, tgt_und = sig.rule, sig.side, sig.why, sig.stop_und, sig.target_und
    else:
        side, why, stop_und, tgt_und = sig
    grade, evidence = VR.EVIDENCE[rule]
    entry = etf_px(u, e, und_px, live)
    mnotes, size_down = notes(u, side, vx)
    why = f"{rule.upper()} {u}: " + why + " · " + " · ".join(mnotes)
    stop = mapped(PREV[e], W[u][e], PREV[u], stop_und) if stop_und else entry * (1 - MIN_STOP)
    p = plan(entry, min(stop, entry * (1 - MIN_STOP)), mapped(PREV[e], W[u][e], PREV[u], tgt_und))
    exit_t = exit_override or (a.short_exit if side == "short" else a.long_exit)
    tag = f"[{grade}: {evidence}]"
    eh, em = map(int, exit_t.split(":"))
    if (dt.datetime.combine(now().date(), dt.time(eh, em)) - now().replace(tzinfo=None)).total_seconds() < 45 * 60:
        skipped[u][f"{rule}: < 45 min to the {exit_t} time stop"] += 1
        return
    if not p["take"]:
        skipped[u][f"{rule}: R:R {p['rr']} < {RR_MIN}"] += 1
        return
    if rule in VR.WATCH_ONLY:
        emit(WATCH, f"{rule.upper()} {e} would be entry {p['entry']} stop {p['stop']} target {p['target']} R:R {p['rr']}",
             why, f"{tag}: WATCH only, no trade")
        return
    cap = (" ⚠️ RISKY: counter-trend bounce, $300 MAX" if rule == "oversold-bounce"
           else " ($300 max, counter-trend)" if side == "long" and rule != "reclaimed-gap"
           else " ($300 size: confirmations are weak)" if size_down else "")
    sig_ = dict(t=now().strftime("%H:%M:%S"), direction=("SHORT " if side == "short" else "LONG ") + u, etf=e, und=u, why=why,
                exit=exit_t, size=300 if (size_down or side == "long" or grade == VR.WEAK) else None,
                risky=rule == "oversold-bounce", tested=grade == VR.TESTED, grade=f"{grade}: {evidence}", **p)
    signals.append(sig_)
    emit(ACTION, f"BUY {e}{cap} {tag}", f"{side} {u}", f"1) BUY LIMIT {p['entry']}", f"2) STOP-MARKET {p['stop']}",
         f"3) SELL LIMIT {p['target']}", f"$300: {p['s300'][0]}sh -${p['s300'][1]}/+${p['s300'][2]}",
         f"$500: {p['s500'][0]}sh -${p['s500'][1]}/+${p['s500'][2]}", f"R:R {p['rr']}", f"why: {why}",
         f"skip if > {p['entry']*1.015:.2f}", f"sell by {exit_t}")
    if not a.no_paper_actions:                       # paper-trade the signal itself, so every ACTION gets a measured outcome
        lev = W[u][e]
        to_und = lambda etf_px_: und_px * (1 + (etf_px_ / p["entry"] - 1) / lev)
        stop_u, tgt_u = to_und(p["stop"]), to_und(p["target"])
        detached([sys.executable, os.path.join(os.path.dirname(__file__), "paper_trade.py"), "--und", u, "--etf", e,
                          "--lev", f"{lev:g}", "--side", side, "--stop-und", f"{stop_u:.4f}", "--targets-und", f"{tgt_u:.4f},{tgt_u:.4f}",
                          "--exit", exit_t, "--label", f"bot ACTION {rule} {now():%H:%M}: BUY {e} {p['entry']}",
                          "--bot", f"ACTION {rule} [{grade}] R:R {p['rr']}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if a.popup:
        msg = (f"BUY {e}  ({side} {u})  {grade}\n\n1) BUY LIMIT   {p['entry']}\n2) STOP-MARKET {p['stop']}\n3) SELL LIMIT  {p['target']}\n\n"
               f"$300 = {p['s300'][0]} sh  -${p['s300'][1]} / +${p['s300'][2]}\nR:R {p['rr']} | sell by {exit_t}\n\n{why}")
        subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "popup.py"), f"TRADE READY: BUY {e} @ {p['entry']}", msg])


def flush_skips(force=False):
    """One WATCH line per underlying for the signals the gate rejected since the last summary."""
    global last_skip
    if not force and time.time() - last_skip < SKIP_EVERY:
        return
    last_skip = time.time()
    for u, c in list(skipped.items()):
        if c:
            emit(WATCH, f"{u}: {sum(c.values())} signal(s) skipped in the last 15 min",
                 "; ".join(f"{k} ×{n}" for k, n in c.most_common(4)),
                 f"a $300 trade with a {MIN_STOP*100:.0f}% stop needs the target ≥ +{min_target_pct():.1f}% on the ETF")
        c.clear()


BASKET = "AAPL MSFT NVDA AMZN META GOOGL AVGO TSLA AMD MU INTC QCOM ORCL CRM NFLX ADBE TXN AMAT LRCX KLAC".split()
_spy = yf.Ticker("SPY").history(period="1y").Close
_spy = _spy[_spy.index.tz_convert("America/New_York").date < now().date()]   # completed days only, wherever we start
DOWN_MKT = bool(_spy.iloc[-1] < _spy.iloc[-50:].mean())          # yesterday's close vs its 50-day: today's regime
_breadth = {}


def first_hour(t):
    y = bars(t)
    if y is None: return None
    fh = y.between_time("09:30", "10:25")
    if len(fh) < 12: return None
    pc = PREV.get(t) or prev_close(t)
    return dict(open=float(fh.Open.iloc[0]), high=float(fh.High.max()), low=float(fh.Low.min()), close=float(fh.Close.iloc[-1]),
                gap=float(fh.Open.iloc[0] / pc - 1), prev=pc)


def breadth(kind):
    """How many of a 20-name basket show the same pattern today (the study needed >= 6 of 51 ~ 12%)."""
    if kind not in _breadth:
        n = 0
        for t in BASKET:
            f = first_hour(t)
            if f and ((kind == "fade" and f["gap"] >= 0.02 and f["close"] < f["open"]) or
                      (kind == "reclaim" and f["gap"] <= -0.02 and f["close"] > f["open"])): n += 1
        _breadth[kind] = n
    return _breadth[kind]


def gap_check(u, etfs, px, vw, lod, live):
    """FAILED GAP-UP (short) / RECLAIMED GAP-DOWN (long), 10:30 -> close. From the 2026-10-23 two-year study: only in a down
    market (SPY under its 50-day) and only when several stocks do it the same day. Stop = the other side of the first hour."""
    key = (u, "gap", now().date())
    if key in sent or now().time() < dt.time(10, 30) or now().time() > dt.time(11, 30): return
    f = first_hour(u)
    if not f: return
    sent.add(key)
    longs = [e for e, l in etfs.items() if l > 0]; shorts = [e for e, l in etfs.items() if l < 0]
    for kind, cond, side, ets, stop_u in (("fade", f["gap"] >= 0.02 and f["close"] < f["open"], "short", shorts, f["high"] * 1.002),
                                         ("reclaim", f["gap"] <= -0.02 and f["close"] > f["open"], "long", longs, f["low"] * 0.998)):
        if not cond: continue
        name = "FAILED GAP-UP" if kind == "fade" else "RECLAIMED GAP-DOWN"
        n = breadth(kind)
        why = (f"{name}: gapped {100*f['gap']:+.1f}% and the first hour closed {'below' if kind == 'fade' else 'above'} the open "
               f"({f['close']:.2f} vs {f['open']:.2f}); market {'down' if DOWN_MKT else 'UP'} (SPY vs 50-day); breadth {n}/20")
        if not DOWN_MKT or n < 3 or not ets:
            emit(WATCH, f"{u} {why}", "conditions of the gap setup not met (needs SPY < 50-day, breadth >= 3/20 and an ETF): no trade")
            continue
        act(u, ets[0], (side, why, stop_u, f["prev"]), px, vw, lod, live, None, exit_override="15:50",
            rule="failed-gap" if kind == "fade" else "reclaimed-gap")


def lower(y):
    return y.rename(columns=str.lower)


def trend_pullback_check(u, y, etfs, px, vw, lod, live):
    """Continuation in a strong trend (Raschke's 'Holy Grail'), plus a WATCH when a 1-hour consolidation breaks on volume."""
    if y is None or len(y) < 30: return
    done = lower(y.iloc[:-1])
    key = (u, "grail", str(done.index[-1]))
    if key in sent: return
    sig = VR.trend_pullback(done, vw, lod)
    longs = [x for x, l in etfs.items() if l > 0]; shorts = [x for x, l in etfs.items() if l < 0]
    if sig is not None:
        ets = shorts if sig.side == "short" else longs
        if ets:
            sent.add(key)
            act(u, ets[0], sig, px, vw, lod, live)
    # consolidation break (Murphy ch. 6 flags): the last 12 candles held a range; this candle closes outside it on volume
    from stockanalyzer.analysis.indicators import adx as _adx
    A = _adx(done.high, done.low, done.close, 14).iloc[-1]
    if not (A.adx >= 30): return
    down = A.minus_di > A.plus_di
    r = done.iloc[-1]; box = done.iloc[-13:-1]; avgv = done.volume.iloc[:-1].mean()
    if len(box) == 12 and avgv and r.volume >= 1.2 * avgv and (u, "flag", str(done.index[-1])) not in sent:
        if down and r.close < box.low.min():
            sent.add((u, "flag", str(done.index[-1])))
            emit(WATCH, f"BEAR FLAG BREAK {u}: closed {r.close:.2f} under the 1-hour range low {box.low.min():.2f} on {r.volume/avgv:.1f}x volume",
                 "continuation lower is likely, but don't chase the break: the entry is the first bounce back to the broken low that fails")
        if not down and r.close > box.high.max():
            sent.add((u, "flag", str(done.index[-1])))
            emit(WATCH, f"BULL FLAG BREAK {u}: closed {r.close:.2f} over the 1-hour range high {box.high.max():.2f} on {r.volume/avgv:.1f}x volume",
                 "continuation higher is likely: the entry is the first pullback to the broken high that holds")


def bounce_check(u, s, vw, px, live, etfs, lod):
    """OVERSOLD-BOUNCE (risky): vwap_rules.oversold_bounce on the completed candles; long ETF, target = VWAP."""
    longs = [e for e, l in etfs.items() if l > 0]
    h = s["hist"]
    if not longs or len(h) < 8: return
    vix_ease = vix is not None and vix.get("off_high", 0) <= -10
    sig, iH, reason = VR.oversold_bounce([c[1] for c in h], [c[2] for c in h], [c[3] for c in h], vw,
                                         vix_eased=vix_ease, late=now().time() >= dt.time(15, 0))
    if iH is None: return
    key = (u, "bounce", str(h[iH][0]))
    if key in sent: return
    sent.add(key)
    if sig is None:
        emit(WATCH, f"{u} {reason}", "OVERSOLD-BOUNCE not taken (needs an RSI divergence, VIX easing or after 15:00)")
        return
    act(u, longs[0], sig, px, vw, lod, live)


_prev_sess = {}


def paper_xfade(e):
    """Paper-track xfade-dbl-bottom on the ETF's own completed candles (H8: +0.24R on 2x ETFs, in-sample; it
    nets about -$1.28 a trade at $300 after fees, so it is paper only until new sessions say otherwise)."""
    key = (e, "xfade", now().date())
    if a.no_paper or key in sent: return
    y = bars(e)
    if y is None or len(y) < 11: return
    done = lower(y.iloc[:-1])[["open", "high", "low", "close", "volume"]]
    if e not in _prev_sess:
        try:
            d = yf.Ticker(e).history(period="5d", interval="5m")
            d.index = d.index.tz_convert("America/New_York")
            d = lower(d[d.index.date < now().date()].between_time("09:30", "15:55"))
            _prev_sess[e] = d[d.index.date == d.index.date.max()] if len(d) else None
        except Exception:
            _prev_sess[e] = None
    dec = double_bottom.signal(SetupContext(done, _prev_sess[e], None, PREV.get(e), None), double_bottom.VARIANTS[0])
    if not dec.fired: return
    sent.add(key)
    emit(WATCH, f"📄 PAPER xfade-dbl-bottom on {e}: {dec.reason}", f"model entry {dec.entry:.2f} stop {dec.stop:.2f} target {dec.target:.2f}",
         "paper only, not a trade call (in-sample +0.24R on 2x ETFs; about -$1.28 a trade at $300 after fees)")
    detached([sys.executable, os.path.join(os.path.dirname(__file__), "paper_trade.py"), "--und", e, "--etf", e, "--lev", "1",
                      "--side", "long", "--stop-und", f"{dec.stop:.4f}", "--targets-und", f"{dec.target:.4f},{dec.target:.4f}",
                      "--exit", "15:50", "--label", "xfade-dbl-bottom (H8, auto)", "--bot", "PAPER only"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


while True:
    t_now = now(); tick = {}
    for u, etfs in W.items():
        y = bars(u); s = st[u]
        stale = yahoo_stale(y)
        if y is not None and len(y) >= 3:
            ref_vw[u] = (float(y.vwap.iloc[-1]), float(y.Low.min()))
            if s["last_bar"] is None and s["bucket"] is None:      # first pass: was it below VWAP earlier today?
                s["below"] = bool((y.Close < y.vwap).iloc[:-1].any())
                s["hist"] = [(t, float(r_.High), float(r_.Low), float(r_.Close)) for t, r_ in y.iloc[:-1].iterrows()]
        if u not in ref_vw:
            continue
        vw, lod = ref_vw[u]
        live = stale and in_session()
        if not live and (y is None or len(y) < 3):
            continue
        if live:                                         # Finnhub fallback: synthetic 5-min candles from quotes
            feed = "Finnhub (VWAP held)"
            px = quote(u)
            if px is None: continue
            lod = min(lod, px); ref_vw[u] = (vw, lod)
            b = t_now.replace(minute=t_now.minute - t_now.minute % 5, second=0, microsecond=0)
            closed = None
            if s["bucket"] != b:
                if s["bucket"] is not None:
                    closed = (s["bucket"], s["o"], s["h"], s["l"], s["c"], None); s["hist"].append((s["bucket"], s["h"], s["l"], s["c"]))
                s.update(bucket=b, o=px, h=px, l=px, c=px)
            else:
                s.update(h=max(s["h"], px), l=min(s["l"], px), c=px)
        else:
            feed = "Yahoo"
            px = float(y.Close.iloc[-1])
            closed = None
            if s["last_bar"] != y.index[-2]:
                if s["last_bar"] is not None:
                    r = y.iloc[-2]
                    closed = (y.index[-2], float(r.Open), float(r.High), float(r.Low), float(r.Close), tod_rvol(u, y.index[-2], r.Volume))
                    vw = float(y.vwap.iloc[-2]); prior_low = float(y.Low.iloc[:-2].min()); avgv = y.Volume.iloc[:-2].mean()
                    ctx[u] = context(y, u); d = ctx[u].get("div")
                    if d and (u, d["kind"], d["at"]) not in sent:
                        sent.add((u, d["kind"], d["at"]))
                        if d["kind"] == "bullish":
                            emit(WATCH, f"BULLISH RSI DIVERGENCE {u}: low {d['price']} under {d['prev']} but RSI {d['rsi']} > {d['rsi_prev']}",
                                 "selling is weakening: take profit on inverse ETFs sooner, watch for a VWAP reclaim")
                        else:
                            emit(WATCH, f"BEARISH RSI DIVERGENCE {u}: high {d['price']} over {d['prev']} but RSI {d['rsi']} < {d['rsi_prev']}",
                                 "buying is weakening: tighten stops on longs, watch for a failed bounce")
                    if r.Close < prior_low and avgv and r.Volume > 1.2 * avgv:
                        emit(WATCH, f"NEW-LOW {u} {r.Close:.2f} (prior low {prior_low:.2f}) on {r.Volume/avgv:.1f}x volume",
                             "don't chase: wait for a bounce that fails at VWAP")
                    for e in etfs:
                        paper_xfade(e)
                s["last_bar"] = y.index[-2]
                done = y.iloc[:-1]
                s["hist"] = [(t, float(r_.High), float(r_.Low), float(r_.Close)) for t, r_ in done.iterrows()]
        if closed:
            bounce_check(u, s, vw, px, live, etfs, lod)
            if not live:
                trend_pullback_check(u, y, etfs, px, vw, lod, live)
            bt, o_, hi, l_, cl, vx = closed
            longs = [e for e, l in etfs.items() if l > 0]; shorts = [e for e, l in etfs.items() if l < 0]
            bar = pd.Series({"open": o_, "high": hi, "low": l_, "close": cl})
            fb = VR.failed_bounce(bar, vw, lod)
            if fb and shorts and (u not in fb_last or bt - fb_last[u] >= FB_COOLDOWN):
                fb_last[u] = bt
                act(u, shorts[0], fb, px, vw, lod, live, vx)
            rc = VR.vwap_reclaim(cl, vw, s["below"], s["reclaimed"], lod)
            if rc:
                s["reclaimed"] = True
                if longs:
                    act(u, longs[0], rc, px, vw, lod, live, vx)
                if shorts:
                    emit(ACTION, f"EXIT {', '.join(shorts)} if held", f"{u} closed above VWAP {vw:.2f}: the short idea failed")
            s["below"] = s["below"] or cl < vw
            if cl < vw * 0.995: s["reclaimed"] = False
        gap_check(u, etfs, px, vw, lod, live)
        if u not in ctx and y is not None:
            ctx[u] = context(y, u)
        tick[u] = dict(px=px, vw=vw, lod=lod, etfs={e: round(etf_px(u, e, px, live), 2) for e in etfs},
                       long=next((e for e, l in etfs.items() if l > 0), None), short=next((e for e, l in etfs.items() if l < 0), None),
                       ctx=ctx.get(u) if not live else dict(ctx.get(u, {}), stale=True))
    flush_skips()
    if time.time() - vix_t > 300:
        vix_t = time.time(); vix = vix_read() or vix
        if vix:
            if vix["state"] == "extreme" and vix_state != "extreme":
                emit(WATCH, f"VIX {vix['level']} ({vix['chg']:+.1f}% today): fear is extreme", "bounces get violent: take profit on shorts sooner, no new chasing")
            if vix_state == "extreme" and vix["off_high"] <= -10 and ("vixease", t_now.hour) not in sent:
                sent.add(("vixease", t_now.hour))
                emit(WATCH, f"VIX easing: {vix['off_high']:+.1f}% off today's high", "short-covering bounce risk: protect profits on inverse ETFs")
            vix_state = vix["state"]
    for hh, txt in ((a.short_exit, f"{a.short_exit} — TIME STOP for inverse/short trades: sell them now, no new shorts (15:00 bounce window)"),
                    ("15:45", "15:45 — close every leveraged position by 15:55 unless an overnight plan passed the close test")):
        h, m = map(int, hh.split(":"))
        if t_now.time() >= dt.time(h, m) and txt not in sent:
            sent.add(txt)
            if (t_now.hour * 60 + t_now.minute) - (h * 60 + m) <= 5:   # don't replay old reminders after a restart
                emit(ACTION, txt)
    write_state(time=t_now.strftime("%H:%M:%S"), feed=feed, tickers=tick, signals=signals[-10:], vix=vix)
    if time.time() - last_hb > 900:
        emit(INFO, "heartbeat", f"feed {feed}", " | ".join(f"{u} {d['px']:.2f} vw {d['vw']:.2f} low {d['lod']:.2f} [" +
             " ".join(f"{e} {v}" for e, v in d["etfs"].items()) + "]" for u, d in tick.items()))
        last_hb = time.time()
    if t_now.time() > dt.time(16, 1):
        flush_skips(force=True)
        emit(INFO, "market closed, watcher exiting"); break
    time.sleep(20)

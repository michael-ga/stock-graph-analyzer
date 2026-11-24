"""Open-position manager for a long ETF position (an inverse ETF counts as long the ETF).

    python track_position.py --etf SNDQ --und SNDK --lev -2 --entry 32.15 --shares 9 --stop 30.87 --target 35.53 --exit 14:55

ACTION alerts: stop or target touched; +1R reached (move the stop to breakeven + fees); half the open gain given back;
time stop. The underlying's 5-min close crossing VWAP against the trade is a WATCH (a plan level, not an exit):
"VWAP loss predicts the turn" was ruled out as a standalone exit on 59 sessions (algolab/LEARNINGS.md 2026-11-03).
Both outcomes are logged to algolab/live/vwap_exit_ledger.csv (the price when VWAP crossed vs the actual exit) so the
rule keeps being measured on live trades. Writes .local/live/position.json for the GUI. Exits when the stop, target
or time stop is hit.
"""
import argparse, csv, os, sys, time, datetime as dt, json
sys.path.insert(0, os.path.dirname(__file__))
from common import *

ap = argparse.ArgumentParser()
for k in ("etf", "und"): ap.add_argument(f"--{k}", required=True)
for k in ("lev", "entry", "shares", "stop", "target"): ap.add_argument(f"--{k}", type=float, required=True)
ap.add_argument("--exit", default="14:55")
a = ap.parse_args()
POS = os.path.join(STATE_DIR, "position.json")
risk = a.entry - a.stop
be_trigger, be_stop = a.entry + risk, a.entry + FEE / a.shares
stop, peak, sent, last_bar, last_hb = a.stop, a.entry, set(), None, 0.0
vwap_cross = None                              # (time, ETF price) when the underlying first crossed VWAP against us
eh, em = map(int, a.exit.split(":"))
emit(INFO, f"tracking {a.shares:g} {a.etf} @ {a.entry}", f"stop {a.stop} target {a.target} +1R {be_trigger:.2f} out by {a.exit}")


def once(key, *parts):
    if key not in sent:
        sent.add(key); emit(ACTION, *parts); return True
    return False


while True:
    px = quote(a.etf)
    if px is None:
        time.sleep(20); continue
    peak = max(peak, px); pnl = a.shares * (px - a.entry) - FEE
    nxt, urgent = f"Hold. Orders working: stop-market {stop:.2f}, sell limit {a.target:.2f}.", False
    if px <= stop and once("stop", f"STOP {stop:.2f} reached ({a.etf} {px:.2f})", "confirm the stop-market filled; if not, SELL at the bid now"):
        nxt, urgent = "Stopped out: confirm the fill.", True
    if px >= a.target and once("target", f"TARGET {a.target:.2f} reached ({a.etf} {px:.2f})", "confirm the sell limit filled; cancel the stop"):
        nxt, urgent = "Target hit: confirm the fill, cancel the stop.", True
    if px >= be_trigger and "be" not in sent:
        once("be", f"+1R reached ({px:.2f} >= {be_trigger:.2f})", f"MOVE the stop-market from {stop:.2f} to {be_stop:.2f} (breakeven + fees)")
        stop = be_stop
    if peak >= a.entry + 1.5 * risk and px <= a.entry + 0.5 * (peak - a.entry):
        once(f"give{round(peak, 1)}", f"gave back half the gain (peak {peak:.2f}, now {px:.2f})", "SELL at the bid to protect the profit")
        nxt, urgent = "Gave back half the gain: consider selling.", True
    y = bars(a.und)
    if y is not None and len(y) >= 3 and not yahoo_stale(y) and y.index[-2] != last_bar:
        if last_bar is not None:
            c, vw = float(y.Close.iloc[-2]), float(y.vwap.iloc[-2])
            against = c > vw if a.lev < 0 else c < vw
            if against and vwap_cross is None:
                vwap_cross = (now().strftime("%H:%M"), px)
                emit(WATCH, f"{a.und} 5-min {y.index[-2]:%H:%M} closed {c:.2f} {'ABOVE' if a.lev < 0 else 'BELOW'} VWAP {vw:.2f}",
                     f"plan level, not an exit: VWAP loss alone has no edge (LEARNINGS 2026-11-03); the stop-market {stop:.2f} is the exit",
                     f"logged ({a.etf} {px:.2f} now) to compare with the real exit")
                nxt = "Underlying crossed VWAP against you: plan level only, the stop is the exit."
        last_bar = y.index[-2]
    t = now().time()
    if t >= dt.time(eh, em) and once("time", f"TIME STOP {a.exit}", f"SELL {a.etf} now at the bid and cancel the open orders"):
        nxt, urgent = f"Time stop {a.exit}: sell now.", True
    elif t >= (dt.datetime.combine(dt.date.today(), dt.time(eh, em)) - dt.timedelta(minutes=10)).time():
        once("time10", f"10 minutes to the time stop ({a.exit})", f"plan to sell {a.etf}")
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(dict(etf=a.etf, shares=a.shares, entry=a.entry, px=px, pnl=round(pnl, 2), stop=round(stop, 2), target=a.target,
                   exit=a.exit, next=nxt, urgent=urgent), open(POS, "w"))
    if time.time() - last_hb > 600:
        emit(INFO, f"{a.etf} {px:.2f} (peak {peak:.2f})", f"net ${pnl:+.2f}", f"stop {stop:.2f} target {a.target}"); last_hb = time.time()
    if {"stop", "target", "time"} & sent:
        if vwap_cross is not None:              # both outcomes of the VWAP rule, for the ledger
            path = os.path.join(ROOT, "algolab", "live", "vwap_exit_ledger.csv"); new = not os.path.exists(path)
            row = dict(date=now().date(), etf=a.etf, und=a.und, entry=a.entry, vwap_cross_time=vwap_cross[0],
                       vwap_cross_px=round(vwap_cross[1], 4), exit_time=now().strftime("%H:%M"), exit_px=round(px, 4),
                       exit_reason=next(k for k in ("stop", "target", "time") if k in sent),
                       vwap_exit_better_usd=round(a.shares * (vwap_cross[1] - px), 2))
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(row)); w.writeheader() if new else None; w.writerow(row)
        emit(INFO, "position closed or time stop reached: tracker exiting"); os.remove(POS) if os.path.exists(POS) else None; break
    time.sleep(20)

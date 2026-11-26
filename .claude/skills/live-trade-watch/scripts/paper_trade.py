"""Paper-track an idea the user had (or skipped) and record the outcome next to the bot's decision.

    python paper_trade.py --und INTC --etf INTW --lev 2 --side long --stop-und 53.75 --targets-und 55.30,56.30 \
        --exit 15:50 --label "user: buy INTC on the VWAP reclaim" --bot "SKIP (R:R 1.16 on INTW)"

Entry = live quotes at start. Simulates on the underlying: stop first, then targets (half at target 1, rest at
target 2), otherwise exit at the time stop. Prints ACTION-free INFO lines and appends one row to
algolab/live/paper_trades.csv: the user's thinking vs the bot's call, with the real outcome.
"""
import argparse, csv, os, sys, time, datetime as dt
sys.path.insert(0, os.path.dirname(__file__))
from common import *

ap = argparse.ArgumentParser()
for k in ("und", "etf", "side", "label", "bot"): ap.add_argument(f"--{k}", required=True)
ap.add_argument("--lev", type=float, required=True)
ap.add_argument("--stop-und", type=float, required=True)
ap.add_argument("--targets-und", required=True)
ap.add_argument("--exit", default="15:50")
ap.add_argument("--usd", type=float, default=300)
a = ap.parse_args()
t1, t2 = (float(x) for x in a.targets_und.split(","))
long = a.side == "long"
e_und, e_etf = quote(a.und), quote(a.etf)
start = now(); eh, em = map(int, a.exit.split(":"))
emit(INFO, f"PAPER {a.side} {a.und} @ {e_und:.2f} (= {a.etf} {e_etf:.2f})", f"stop {a.stop_und} targets {t1}/{t2} exit {a.exit}",
     f"idea: {a.label}", f"bot: {a.bot}")
hi = lo = e_und; legs, rem, reason = [], 1.0, None
while True:
    px = quote(a.und)
    if px is None: time.sleep(30); continue
    hi, lo = max(hi, px), min(lo, px)
    fav = (lambda x: x >= t1) if long else (lambda x: x <= t1)
    if (px <= a.stop_und if long else px >= a.stop_und):
        legs.append((rem, px)); rem = 0; reason = reason or "stop"
    elif rem == 1.0 and fav(px):
        legs.append((0.5, px)); rem = 0.5; reason = "target 1"
        emit(INFO, f"PAPER {a.und} reached target 1 {t1} -> half out, stop to breakeven")
        a.stop_und = e_und
    elif rem > 0 and ((px >= t2) if long else (px <= t2)):
        legs.append((rem, px)); rem = 0; reason = "target 2"
    if rem > 0 and now().time() >= dt.time(eh, em):
        legs.append((rem, px)); rem = 0; reason = (reason + " + time stop") if reason else "time stop"
    if rem == 0: break
    time.sleep(30)
sgn = 1 if long else -1
und_ret = sum(w * sgn * (p / e_und - 1) for w, p in legs)
etf_ret = abs(a.lev) * und_ret                       # the ETF moves with the idea, times its leverage
usd = a.usd * etf_ret - (FEE / 2) * (1 + len(legs))   # one buy order + one order per exit leg
mfe = sgn * ((hi if long else lo) / e_und - 1); mae = sgn * ((lo if long else hi) / e_und - 1)
row = dict(date=start.date(), start=start.strftime("%H:%M"), end=now().strftime("%H:%M"), idea=a.label, bot=a.bot, und=a.und,
           side=a.side, entry_und=round(e_und, 2), etf=a.etf, entry_etf=round(e_etf, 2), exit=reason,
           und_ret_pct=round(100 * und_ret, 2), etf_ret_pct=round(100 * etf_ret, 2), usd_on_size=round(usd, 2),
           mfe_pct=round(100 * mfe, 2), mae_pct=round(100 * mae, 2),
           verdict=(("bot trade won" if usd > 0 else "bot trade lost") if a.bot.startswith("ACTION")
                    else "idea beat the bot's skip" if usd > 0 else "bot's skip was right"))
path = os.path.join(ROOT, "algolab", "live", "paper_trades.csv"); new = not os.path.exists(path)
with open(path, "a", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(row)); w.writeheader() if new else None; w.writerow(row)
emit(INFO, f"PAPER RESULT {a.und} {a.side}: exit {reason}", f"{a.und} {100*und_ret:+.2f}% / {a.etf} ~{100*etf_ret:+.2f}%",
     f"${usd:+.2f} on ${a.usd:.0f} after fees", f"best {100*mfe:+.2f}% worst {100*mae:+.2f}%", row["verdict"], "logged to algolab/live/paper_trades.csv")

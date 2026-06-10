"""Walk-forward backtest of the swing scoring system against real history.

For each ticker: load ~1Y of daily bars, auto-pick 7 interesting test points
(3 before the biggest 10-day up-moves, 2 before the biggest drops, 2 in chop),
rebuild the app's view AS OF each point (no lookahead: every frame is a slice
ending that day), run the real plan builder + score, then judge the advice
against what the market actually did next (stop-first conservative judging).

Run:  .venv\\Scripts\\python.exe backtest.py [TICKER ...]
"""
from __future__ import annotations

import sys

import numpy as np

from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.data.providers import get_provider
from stockanalyzer.data.schema import Timeframe
from stockanalyzer.explain.swing import build_swing_plan
from stockanalyzer.explain.usecase import UseCase
from stockanalyzer.papertrade import judge_outcome
from stockanalyzer.strategy import SwingPace
from stockanalyzer.verdict.aggregate import build_verdict

HORIZON = 10            # trading days a standard swing gets to play out
MIN_HISTORY = 60        # bars needed before a point qualifies
SPACING = 8             # min bars between chosen points
PICKS = (("UP", 4), ("DOWN", 3), ("CHOP", 3))   # 10 points per ticker

# Diverse regimes on purpose: momentum tech, steady mega-caps, decliners,
# high-vol speculative, non-tech — so conclusions aren't bull-tape artifacts.
DEFAULT_TICKERS = ["NOK", "INTC", "NVDA", "MSFT", "AAPL", "TSLA",
                   "SMCI", "PLTR", "PFE", "NKE", "JPM", "XOM"]


# --------------------------------------------------------------------------- #
def pick_points(df) -> list[tuple[int, str]]:
    """Test points: biggest forward rallies, biggest drops, and chop windows."""
    n = len(df)
    cands = []
    for t in range(MIN_HISTORY, n - HORIZON - 1):
        c = float(df["close"].iloc[t])
        fwd_up = float(df["high"].iloc[t + 1:t + 1 + HORIZON].max()) / c - 1
        fwd_dn = float(df["low"].iloc[t + 1:t + 1 + HORIZON].min()) / c - 1
        net = float(df["close"].iloc[t + HORIZON]) / c - 1
        cands.append((t, fwd_up, fwd_dn, net))

    chosen: list[tuple[int, str]] = []

    def _take(seq, label, k):
        for t, *_ in seq:
            if len([1 for c, _ in chosen if abs(c - t) < SPACING]) == 0:
                chosen.append((t, label))
                if len([1 for _, l in chosen if l == label]) >= k:
                    return

    for label, k in PICKS:
        if label == "UP":
            _take(sorted(cands, key=lambda x: -x[1]), label, k)
        elif label == "DOWN":
            _take(sorted(cands, key=lambda x: x[2]), label, k)
        else:
            _take(sorted(cands, key=lambda x: abs(x[3]) + (x[1] - x[3])), label, k)
    return sorted(chosen)


def judge_close_fill(bars, entry_plan, stop, target, trigger, horizon_days,
                     wait_days=None):
    """Counterfactual: breakout fills only on a CLOSE beyond the trigger (the
    rule the guidance actually states), at that close. Stop/target keep the
    plan's absolute levels; result % measured from the actual fill.
    wait_days controls how long the armed order stays valid (default horizon)."""
    if trigger is None:
        return judge_outcome(bars, entry_plan, stop, target, None, horizon_days)
    wait_days = wait_days or horizon_days
    waited = 0
    entry = None
    active_days = 0
    for high, low, close in bars:
        if entry is None:
            if close >= trigger:
                entry = close                      # filled at the trigger close
            else:
                waited += 1
                if waited >= wait_days:
                    return "not_triggered", 0.0
                continue
            active_days += 1                       # fill bar counts; judge next bars
            continue
        if low <= stop:
            return "stop_hit", round((stop / entry - 1) * 100, 1)
        if high >= target:
            return "target_hit", round((target / entry - 1) * 100, 1)
        active_days += 1
        if active_days >= horizon_days:
            return "expired", round((close / entry - 1) * 100, 1)
    return ("open", 0.0) if entry is None else ("open", round((bars[-1][2] / entry - 1) * 100, 1))


def as_of_reports(df, t: int) -> dict:
    """The multi-timeframe view the app would have had on day t (daily bars)."""
    out = {}
    for tf, lookback in ((Timeframe.M1, 22), (Timeframe.M6, 126), (Timeframe.Y1, 252)):
        sl = df.iloc[max(0, t + 1 - lookback):t + 1]
        if len(sl) >= 20:
            try:
                out[tf] = analyze_timeframe(sl)
            except Exception:
                pass
    return out


def evaluate_point(df, t: int, label: str) -> dict | None:
    reports = as_of_reports(df, t)
    dec = reports.get(Timeframe.M1)
    if dec is None:
        return None
    inv = round((build_verdict(reports).score + 1) / 2 * 100)
    plan = build_swing_plan(dec, UseCase.BUY, SwingPace.STANDARD,
                            all_reports=reports, context={"investor_pct": inv})
    if plan is None:
        return None

    c = float(df["close"].iloc[t])
    fwd_up = float(df["high"].iloc[t + 1:t + 1 + HORIZON].max()) / c - 1
    fwd_dn = float(df["low"].iloc[t + 1:t + 1 + HORIZON].min()) / c - 1
    net = float(df["close"].iloc[t + HORIZON]) / c - 1

    # Judge the plan as a real trade (breakout plans need their trigger first).
    bars = list(zip(df["high"].iloc[t + 1:t + 1 + 3 * HORIZON].astype(float),
                    df["low"].iloc[t + 1:t + 1 + 3 * HORIZON].astype(float),
                    df["close"].iloc[t + 1:t + 1 + 3 * HORIZON].astype(float)))
    status, result = judge_outcome(bars, plan.entry, plan.stop, plan.target1,
                                   trigger=plan.trigger, horizon_days=HORIZON)
    # Counterfactuals measured on the SAME data:
    c_status, c_result = judge_close_fill(bars, plan.entry, plan.stop, plan.target1,
                                          plan.trigger, HORIZON)
    w_status, w_result = judge_close_fill(bars, plan.entry, plan.stop, plan.target1,
                                          plan.trigger, HORIZON,
                                          wait_days=int(1.5 * HORIZON))
    relaxed_go = (plan.kind == "immediate" and plan.setup != "No setup"
                  and plan.rr >= 1.5)

    # Was the advice right?
    acted = plan.go or plan.kind == "breakout_wait"
    if plan.go:
        verdict = "WIN" if result > 0 else ("LOSS" if status == "stop_hit" else
                                            "WIN" if status == "target_hit" else
                                            ("WIN" if result > 0 else "LOSS"))
    elif plan.kind == "breakout_wait":
        if status == "not_triggered":
            verdict = "MISS" if fwd_up >= 0.08 else "OK-PASS"
        else:
            verdict = "WIN" if result > 0 else "LOSS"
    else:  # no_trade / forming without trigger
        if fwd_up >= 0.08 and net > 0.04:
            verdict = "MISS"          # stayed out of a real rally
        elif fwd_dn <= -0.06:
            verdict = "OK-AVOID"      # correctly out of a drop
        else:
            verdict = "OK-PASS"       # correctly out of chop

    return dict(
        date=str(df.index[t].date()), label=label, price=round(c, 2),
        kind=plan.kind, go=plan.go, score=plan.score, rr=plan.rr,
        setup=plan.setup[:26], status=status, result=result,
        c_status=c_status, c_result=c_result, relaxed_go=relaxed_go,
        w_status=w_status, w_result=w_result,
        fwd_up=round(fwd_up * 100, 1), fwd_dn=round(fwd_dn * 100, 1),
        net=round(net * 100, 1), verdict=verdict, acted=acted,
        guidance=plan.guidance[:90],
    )


def run(tickers: list[str]) -> None:
    provider = get_provider("yfinance")
    all_rows: list[dict] = []
    for tk in tickers:
        try:
            df = provider.fetch_cached(tk, Timeframe.Y1)
        except Exception as exc:
            print(f"{tk}: fetch failed ({exc})")
            continue
        print(f"\n=== {tk} — {len(df)} daily bars "
              f"({df.index[0].date()} .. {df.index[-1].date()}) ===")
        hdr = (f"{'date':<11}{'why':<5}{'px':>8}  {'kind':<14}{'go':<3}{'score':>5} "
               f"{'trade-result':<22}{'fwd10d hi/lo/net':<20} verdict")
        print(hdr)
        print("-" * len(hdr))
        for t, label in pick_points(df):
            row = evaluate_point(df, t, label)
            if row is None:
                continue
            row["ticker"] = tk
            all_rows.append(row)
            tr = f"{row['status']} {row['result']:+.1f}%"
            fwd = f"+{row['fwd_up']}/{row['fwd_dn']}/{row['net']:+}%"
            print(f"{row['date']:<11}{row['label']:<5}{row['price']:>8.2f}  "
                  f"{row['kind']:<14}{('Y' if row['go'] else '-'):<3}{row['score']:>4}% "
                  f"{tr:<22}{fwd:<20} {row['verdict']}")

    # ----- summary ------------------------------------------------------------
    print("\n" + "=" * 72)
    print("SUMMARY — score system vs reality")
    print("=" * 72)
    acted = [r for r in all_rows if r["acted"] and r["status"] not in ("open",)]
    traded = [r for r in acted if r["status"] in ("target_hit", "stop_hit", "expired")]
    if traded:
        wins = [r for r in traded if r["result"] > 0]
        print(f"Actionable signals taken: {len(traded)} | wins {len(wins)} "
              f"| win rate {len(wins)/len(traded)*100:.0f}% "
              f"| avg result {np.mean([r['result'] for r in traded]):+.1f}%")
    ups = [r for r in all_rows if r["label"] == "UP"]
    caught = [r for r in ups if r["verdict"] == "WIN" or
              (r["acted"] and r["result"] > 0)]
    print(f"Big rallies tested: {len(ups)} | engaged profitably: {len(caught)} "
          f"| missed: {len([r for r in ups if r['verdict']=='MISS'])}")
    downs = [r for r in all_rows if r["label"] == "DOWN"]
    avoided = [r for r in downs if r["verdict"] in ("OK-AVOID", "OK-PASS")
               or (not r["acted"])]
    print(f"Big drops tested: {len(downs)} | stayed out / protected: {len(avoided)} "
          f"| caught long into drop: {len([r for r in downs if r['verdict']=='LOSS'])}")
    chop = [r for r in all_rows if r["label"] == "CHOP"]
    print(f"Chop periods tested: {len(chop)} | correctly passed: "
          f"{len([r for r in chop if r['verdict'].startswith('OK')])}")

    # score correlation
    for lo, hi, name in ((70, 101, "score >=70"), (55, 70, "score 55-69"),
                         (0, 55, "score <55")):
        band = [r for r in all_rows if lo <= r["score"] < hi]
        if band:
            done = [r for r in band if r["status"] in ("target_hit", "stop_hit", "expired")]
            wr = (f"{len([r for r in done if r['result'] > 0]) / len(done) * 100:.0f}%"
                  if done else "n/a")
            print(f"{name}: n={len(band)} | trade win-rate {wr} | avg fwd-10d net "
                  f"{np.mean([r['net'] for r in band]):+.1f}% | "
                  f"avg max-up +{np.mean([r['fwd_up'] for r in band]):.1f}%")

    # --- counterfactual A: relaxed GO (rr >= 1.5, immediate, real setup) -------
    print("\nCOUNTERFACTUAL A — GO gate")
    cur_go = [r for r in all_rows if r["go"]]
    rel = [r for r in all_rows
           if r["relaxed_go"] and r["status"] in ("target_hit", "stop_hit", "expired")]
    print(f"  current rule (rr>=2):  GO fired {len(cur_go)} times")
    if rel:
        w = [r for r in rel if r["result"] > 0]
        print(f"  relaxed (rr>=1.5):     would fire {len(rel)} | win rate "
              f"{len(w)/len(rel)*100:.0f}% | avg {np.mean([r['result'] for r in rel]):+.1f}%")
    else:
        print("  relaxed (rr>=1.5):     would fire 0 times")

    # --- counterfactual C: GO-gate R:R threshold sweep (immediate plans) -------
    print("\nCOUNTERFACTUAL C — GO gate sweep (immediate plans with a real setup)")
    imm = [r for r in all_rows if r["kind"] == "immediate" and r["setup"] != "No setup"
           and r["status"] in ("target_hit", "stop_hit", "expired")]
    print(f"  pool: {len(imm)} judged immediate plans")
    hdr = f"  {'rule':<28}{'n':>4}{'WR':>6}{'avg':>8}{'sum':>8}  drop-traps"
    print(hdr)
    for rr_min in (1.2, 1.4, 1.6, 1.8, 2.0):
        for extra, name in ((None, f"rr>={rr_min}"),
                            (55, f"rr>={rr_min} & score>=55")):
            sel = [r for r in imm if r["rr"] >= rr_min
                   and (extra is None or r["score"] >= extra)]
            if not sel:
                print(f"  {name:<28}{0:>4}")
                continue
            w = [r for r in sel if r["result"] > 0]
            traps = len([r for r in sel if r["label"] == "DOWN" and r["result"] < 0])
            print(f"  {name:<28}{len(sel):>4}{len(w)/len(sel)*100:>5.0f}%"
                  f"{np.mean([r['result'] for r in sel]):>+7.1f}%"
                  f"{sum(r['result'] for r in sel):>+7.1f}%  {traps}")

    # --- counterfactual D: longer armed-window for breakout triggers -----------
    print("\nCOUNTERFACTUAL D — armed breakout window (10d wait vs 15d wait)")
    brk_all = [r for r in all_rows if r["kind"] == "breakout_wait"]
    for key_s, key_r, name in (("c_status", "c_result", "10-day wait"),
                               ("w_status", "w_result", "15-day wait")):
        done = [r for r in brk_all if r.get(key_s) in ("target_hit", "stop_hit", "expired")]
        if done:
            w = [r for r in done if r[key_r] > 0]
            print(f"  {name}: filled {len(done)} | WR {len(w)/len(done)*100:.0f}% | "
                  f"avg {np.mean([r[key_r] for r in done]):+.1f}% | "
                  f"not-triggered {len([r for r in brk_all if r.get(key_s)=='not_triggered'])}")

    # --- counterfactual B: breakout fill rule (touch vs close) -----------------
    brk = [r for r in all_rows if r["kind"] == "breakout_wait"]
    t_done = [r for r in brk if r["status"] in ("target_hit", "stop_hit", "expired")]
    c_done = [r for r in brk if r["c_status"] in ("target_hit", "stop_hit", "expired")]
    print("\nCOUNTERFACTUAL B — breakout fill rule (same plans, same bars)")
    if t_done:
        tw = [r for r in t_done if r["result"] > 0]
        print(f"  touch-fill (current): {len(t_done)} filled | win rate "
              f"{len(tw)/len(t_done)*100:.0f}% | avg {np.mean([r['result'] for r in t_done]):+.1f}%")
    if c_done:
        cw = [r for r in c_done if r["c_result"] > 0]
        print(f"  close-fill (guided):  {len(c_done)} filled | win rate "
              f"{len(cw)/len(c_done)*100:.0f}% | avg {np.mean([r['c_result'] for r in c_done]):+.1f}%")
    nt = len([r for r in brk if r["c_status"] == "not_triggered"
              and r["status"] != "not_triggered"])
    print(f"  wick-only fills avoided by close rule: {nt}")

    print("\nLegend: WIN/LOSS = traded outcome | MISS = stayed out of a real rally | "
          "OK-AVOID = correctly out of a drop | OK-PASS = correctly out of chop")


if __name__ == "__main__":
    run([t.upper() for t in (sys.argv[1:] or DEFAULT_TICKERS)])

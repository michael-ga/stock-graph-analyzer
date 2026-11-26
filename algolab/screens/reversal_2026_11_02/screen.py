"""Screen the reversal-at-support variants and pre-registered fixes on cached contexts.

PRE-REGISTERED (written before any screen result was seen; what was known: on the
replay A/B every variant lost, touch entries worst, and most losses were the same bot
re-buying a ticker after each stop-out the same day):

- Every config uses the new book rule: a bot enters a ticker at most once per ET day.
- Grid: the 4 REVERSAL_VARIANTS x 5 filters, applied before ``reversal_signal``:
    none   as today
    knife  stand aside on a gap-down <= -2% for every mode (today only with_trend does)
    green  only when the session is up (day change >= 0): buy dips in up days
    late   no entry before 10:30 ET (let the opening flush finish)
    all3   knife + green + late
  plus the 4 variants without the once-per-day rule (today's behavior, for reference).
- Judging copies the replay book: open at the signal bar's close; later bars marked
  low -> high -> close (stop first); flat at the last bar's close. R vs the initial
  stop; net R pays $0.02/share.
- Pick on the latest 40 sessions: >= 15 trades, avg net R > 0, > 0 in both halves,
  > 0 without the best date. Rank by avg net R; keep at most 2. Confirm on the older
  sessions (holdout): avg net R > 0. Anything that fails is not a validated fix.

ITERATION 2 (written after iteration 1's screen, before any holdout look). Iteration 1:
nothing passed; with_trend variants never won (0%), strong-confirm 6-11%; strong-touch
+ knife was the only one near breakeven (+0.32 R gross, +0.07 net). Diagnosis: the
stop sits 0.2% under support, so noise stops it out and the $0.02 spread costs ~0.25 R
a trade. Iteration 2 tests strong-touch + knife + once-per-day with the stop
0.5% / 1.0% under support (target and the 2.5% aim unchanged), same pass rule, then
the holdout.

    python algolab/screens/reversal_2026_11_02/screen.py [--part screen|holdout|all] [--iter 1|2]
"""
from __future__ import annotations

import argparse
import pathlib
import pickle
import statistics
import sys
from collections import defaultdict

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from stockanalyzer.setups import reversal  # noqa: E402

CTX = ROOT / ".local" / "rev-ctx"
ETFS = {"SNXX", "SNDQ", "NBIZ", "NBIS"}
SPREAD = 0.02


def _gap(card, price, prev_close):
    if card.day_change_pct is None or not prev_close:
        return None
    return price / (1 + card.day_change_pct / 100.0) / prev_close - 1


FILTERS = {
    "none": lambda b, pc: True,
    "knife": lambda b, pc: not ((g := _gap(b["card"], b["close"], pc)) is not None and g <= reversal.GAP_DOWN_PCT),
    "green": lambda b, pc: (b["card"].day_change_pct or 0) >= 0,
    "late": lambda b, pc: b["t"].strftime("%H:%M") >= "10:30",
}
FILTERS["all3"] = lambda b, pc: all(FILTERS[k](b, pc) for k in ("knife", "green", "late"))


def day_trades(day: dict, cfg, flt, once: bool, buf: float | None = None) -> list[dict]:
    bars, pc = day["bars"], day["prev_close"]
    out, pos, traded = [], None, False
    for b in bars:
        if pos is not None:                                   # mark low -> high -> close
            if b["low"] <= pos["stop"]:
                pos.update(exit=pos["stop"], why="stop")
            elif b["high"] >= pos["target"]:
                pos.update(exit=pos["target"], why="target")
            if "exit" in pos:
                out.append(pos)
                pos = None
        if pos is not None or (once and traded) or not FILTERS[flt](b, pc):
            continue
        if "idf" not in b:                                    # built once, shared by every config
            b["idf"] = pd.DataFrame([{k: b[k] for k in ("open", "high", "low", "close")}],
                                    index=[b["t"]])
        d = reversal.reversal_signal(b["card"], b["idf"], b["close"], b["trend"], pc, b["t"], cfg)
        stop = d.stop if buf is None or not d.fired else round(d.support * (1 - buf), 4)
        if d.fired and stop < d.entry:
            pos, traded = {"t": b["t"], "entry": d.entry, "stop": stop, "target": d.target}, True
    if pos is not None and bars:
        pos.update(exit=bars[-1]["close"], why="eod")
        out.append(pos)
    for p in out:
        risk = p["entry"] - p["stop"]
        p["r"] = (p["exit"] - p["entry"]) / risk
        p["net"] = p["r"] - SPREAD / risk
    return out


def load(part: str) -> dict:
    days = {}
    for f in CTX.glob("*/*.pkl"):
        days[(f.parent.name, f.stem)] = pickle.loads(f.read_bytes())
    dates = sorted({d for _, d in days})
    keep = set(dates[-40:]) if part == "screen" else set(dates[:-40]) if part == "holdout" else set(dates)
    return {k: v for k, v in days.items() if k[1] in keep}


def stats(ts: list[tuple]) -> dict | None:
    if not ts:
        return None
    nets = [x[2]["net"] for x in ts]
    by_date = defaultdict(float)
    for d, _, p in ts:
        by_date[d] += p["net"]
    dates = sorted(by_date)
    mid = dates[len(dates) // 2] if dates else None
    best = max(by_date, key=by_date.get)
    rest = [x[2]["net"] for x in ts if x[0] != best]
    h1 = [x[2]["net"] for x in ts if x[0] < mid]
    h2 = [x[2]["net"] for x in ts if x[0] >= mid]
    mean = lambda v: statistics.mean(v) if v else None   # noqa: E731
    return {"n": len(nets), "dates": len(by_date), "win": sum(n > 0 for n in nets) / len(nets) * 100,
            "avg": mean(nets), "net": sum(nets), "gross": mean([x[2]["r"] for x in ts]),
            "h1": mean(h1), "h2": mean(h2), "nobest": mean(rest),
            "etf": mean([x[2]["net"] for x in ts if x[1] in ETFS]),
            "stk": mean([x[2]["net"] for x in ts if x[1] not in ETFS]),
            "n_etf": sum(x[1] in ETFS for x in ts)}


def passes(s) -> bool:
    return bool(s and s["n"] >= 15 and s["avg"] > 0 and (s["h1"] or 0) > 0 and (s["h2"] or 0) > 0
                and (s["nobest"] or 0) > 0)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=("screen", "holdout", "all"), default="screen")
    ap.add_argument("--iter", type=int, choices=(1, 2), default=1)
    a = ap.parse_args()
    days = load(a.part)
    dates = sorted({d for _, d in days})
    print(f"{a.part}: {dates[0]}..{dates[-1]} ({len(dates)} sessions, {len(days)} ticker-days)\n")
    f2 = lambda v: "–" if v is None else f"{v:+.2f}"   # noqa: E731
    print("| config | trades (dates) | win % | avg R gross | avg R net | net R | halves | w/o best | ETFs | stocks | pass |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    if a.iter == 1:
        configs = [(c, "none", False, None) for c in reversal.REVERSAL_VARIANTS]
        configs += [(c, f, True, None) for f in FILTERS for c in reversal.REVERSAL_VARIANTS]
    else:
        st = next(c for c in reversal.REVERSAL_VARIANTS if c.name == "rev-strong-touch")
        configs = [(st, "knife", True, b) for b in (None, 0.005, 0.010)]
    results = []
    for cfg, flt, once, buf in configs:
        ts = [(d, tk, p) for (tk, d), day in sorted(days.items())
              for p in day_trades(day, cfg, flt, once, buf)]
        s = stats(ts)
        name = (f"{cfg.name} · {flt}" + ("" if once else " · re-entry (today)")
                + (f" · stop {buf * 100:.1f}% under support" if buf else ""))
        results.append((name, s))
        if s is None:
            print(f"| {name} | 0 | | | | | | | | | |")
            continue
        print(f"| {name} | {s['n']} ({s['dates']}) | {s['win']:.0f}% | {f2(s['gross'])} | {f2(s['avg'])} | "
              f"{s['net']:+.1f} | {f2(s['h1'])} / {f2(s['h2'])} | {f2(s['nobest'])} | {f2(s['etf'])} "
              f"({s['n_etf']}) | {f2(s['stk'])} ({s['n'] - s['n_etf']}) | {'✅' if passes(s) else ''} |")
    if a.part == "screen":
        ok = sorted((r for r in results if passes(r[1]) and "re-entry" not in r[0]),
                    key=lambda r: -r[1]["avg"])
        print("\nPASS (ranked):", [r[0] for r in ok] or "none")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Algorithm post-mortem — run this at the start of every algo-dev session.

Prints three views of the closed virtual book (trades.db):

  1. PER-BOT      — which trading *rule* (bot-GO / bot-70 / bot-BRK / me) earned
                    its keep. This is the original report card.
  2. ALGORITHM    — the deduped, idea-level view: every bot that copied one plan
                    counts ONCE (cohort_id), so the win rate judges the engine's
                    *calls*, not how many bots echoed them. This is the honest
                    sample for deciding whether a setup/score actually works.
  3. LOSERS       — common failed checks / signals among losing trades.

Usage (from anywhere):  python algolab/analyze.py
Point at another DB:    STOCKANALYZER_DB=/path/to/x.db python algolab/analyze.py

Read algolab/LEARNINGS.md BEFORE acting on anything here — most single-session
patterns are sample noise, and that file records what's already been ruled out.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the project importable regardless of where this is run from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")   # Windows console safety
except Exception:
    pass

from stockanalyzer.data import store  # noqa: E402


def _bar(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _fmt_agg(a: dict) -> str:
    wr = a.get("win_rate")
    avg = a.get("avg_pnl_pct")
    return (f"n={a.get('n', a.get('n_ideas', 0)):>3}  "
            f"W={a.get('wins', 0):>2} L={a.get('losses', 0):>2}  "
            f"win%={('--' if wr is None else f'{wr:>3}')}  "
            f"avg%={('--' if avg is None else f'{avg:+.2f}')}")


def per_bot() -> None:
    _bar("1. PER-BOT REPORT CARD  (which rule makes money)")
    s = store.trade_stats()
    t = s["totals"]
    if not t["n"]:
        print("No closed trades yet.")
        return
    print(f"ALL: {_fmt_agg(t)}  total ${t['total_pnl_usd']:+.0f}")
    print("\nby trader:")
    for k, v in s["traders"].items():
        print(f"  {k:10s} {_fmt_agg(v)}  ${v['total_pnl_usd']:+.0f}")
    print("\nby setup:")
    for k, v in s["setups"].items():
        print(f"  {k[:30]:31s} {_fmt_agg(v)}  ${v['total_pnl_usd']:+.0f}")
    print("\nby score band:")
    for k, v in s["bands"].items():
        print(f"  {k:7s} {_fmt_agg(v)}  ${v['total_pnl_usd']:+.0f}")


def algorithm() -> None:
    _bar("2. ALGORITHM CORRECTNESS  (deduped: 1 vote per idea, across all bots)")
    a = store.algorithm_correctness()
    t = a["totals"]
    if not t["n_ideas"]:
        print("No closed ideas yet.")
        return
    print(f"{t['n_ideas']} distinct ideas from {t['n_trades']} bot-trades  "
          f"| idea win%={t['win_rate']}  avg%={t['avg_pnl_pct']:+.2f}")
    if t["n_trades"] > t["n_ideas"]:
        dup = t["n_trades"] - t["n_ideas"]
        print(f"  ({dup} bot-trade(s) were duplicates of an idea another bot "
              "also took — counted once here.)")
    print("\nby setup (idea-level):")
    for k, v in a["setups"].items():
        print(f"  {k[:30]:31s} {_fmt_agg(v)}")
    print("\nby score band (idea-level):")
    for k, v in a["bands"].items():
        print(f"  {k:7s} {_fmt_agg(v)}")
    print("\nworst ideas first:")
    for i in a["ideas"][:12]:
        traders = ",".join(i["traders"])
        print(f"  {i['idea_pnl_pct']:+6.2f}%  {i['ticker']:5s} "
              f"{(i['setup'] or '?')[:26]:27s} sc={str(i['score'] or '--'):>3} "
              f"x{i['n_trades']} [{traders}]")


def losers() -> None:
    _bar("3. LOSING-TRADE PATTERNS  (hypotheses to TEST, not conclusions)")
    p = store.losing_trade_patterns()
    if not p["n"]:
        print("No losing trades yet.")
        return
    print(f"{p['n']} losing trades")
    if p["failed_checks"]:
        print("\nmost-failed weighted checks among losers:")
        for name, cnt in list(p["failed_checks"].items())[:10]:
            print(f"  {cnt:>2}x  {name}")
    if p["avg_indicators"]:
        print("\navg indicators among losers:")
        for k, v in p["avg_indicators"].items():
            print(f"  {k}: {('--' if v is None else round(v, 4))}")


def managed_ab() -> None:
    _bar("4. MANAGED vs FIXED  (Gap-and-Go: does the smart exit beat fixed?)")
    a = store.managed_vs_fixed()
    if not a["n_pairs"]:
        print("No matched Gap-and-Go pairs yet (need bot-gap-mgd + bot-gap-fixed "
              "on the same idea, both closed).")
        return
    print(f"{a['n_pairs']} matched idea pair(s)  | mean Δ (mgd − fixed) = "
          f"{a['mean_delta_pct']:+.2f}%  | win%  mgd={a['mgd_win_rate']} "
          f"fixed={a['fixed_win_rate']}")
    print(f"  avg stop moves={a['avg_stop_moves']}  mean MFE captured="
          f"{a['mean_mfe_pct']:+.2f}%")
    print("\nworst deltas first (where management hurt):")
    for p in a["pairs"][:12]:
        print(f"  Δ{p['delta_pct']:+6.2f}%  {p['ticker']:5s}  "
              f"mgd={p['mgd_pnl_pct']:+.2f}% ({p['mgd_reason']}) vs "
              f"fixed={p['fixed_pnl_pct']:+.2f}% ({p['fixed_reason']})")


def main() -> None:
    print(f"DB: {store.DB_PATH}")
    per_bot()
    algorithm()
    losers()
    managed_ab()
    print("\nReminder: at small n most sub-group splits are noise. See "
          "algolab/LEARNINGS.md for what has already been ruled out, and add a "
          "dated entry there for anything you change.")


if __name__ == "__main__":
    main()

"""Behavioral fingerprint of the trading algorithm — offline and deterministic.

Run against ONE source tree (``--root``) and write JSON. ``algo_gate.py`` runs it
twice, on the base and on the change, and compares. Everything reads frozen data
that already lives in the repo, so the two runs see identical inputs:

- ``plans``    the real swing plan + swing-bot rules over the frozen daily frames
               of ``tests/valuation/fixtures/ohlcv`` at several as-of points, for
               both swing paces (the radar uses FAST, the backtest STANDARD).
- ``replay``   every bar of the synthetic intraday day from ``tests/test_replay``
               through ``replay.precompute_day``: Gap-and-Go, the reversal
               variants and the swing bots, bar by bar.
- ``backtest`` ``backtest.py``'s walk-forward evaluation (``pick_points`` +
               ``evaluate_point``) over the same frozen frames, plus its summary.

Only stable public entry points are imported, so a base that predates a helper
still probes cleanly. A section that raises is recorded as an error, which the
gate reports as a difference rather than crashing.

    python scripts/algo_probe.py --root <tree> --out <file.json>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

_OFFSETS = (0, 20, 60, 120)          # bars back from the end of each frozen frame
_EMERGING_BARS = 30                  # one thin-history point per ticker
_LOOKBACKS = (("M1", 22), ("M6", 126), ("Y1", 252))


def _r(x, nd: int = 4):
    try:
        return None if x is None else round(float(x), nd)
    except (TypeError, ValueError):
        return str(x)


def _frames(root: Path) -> dict:
    import pandas as pd
    from stockanalyzer.data.schema import validate_ohlcv

    out = {}
    for path in sorted((root / "tests" / "valuation" / "fixtures" / "ohlcv").glob("*.csv")):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        out[path.stem] = validate_ohlcv(df)
    return out


def _as_of(df, t: int) -> dict:
    from stockanalyzer.analysis.engine import analyze_timeframe
    from stockanalyzer.data.schema import Timeframe

    out = {}
    for name, lookback in _LOOKBACKS:
        sl = df.iloc[max(0, t + 1 - lookback):t + 1]
        if len(sl) >= 20:
            out[Timeframe[name]] = analyze_timeframe(sl)
    return out


def _extended(plan) -> bool:
    es = getattr(plan, "emerging", None)
    return bool(es and (es.extended or "distribution_risk" in es.flags))


_LIVE_BOTS: list | None = None


def _live_bots(root: Path) -> list:
    """The LIVE swing-bot rules: app.py's ``_BOTS``, read from THIS tree.

    app.py is a Streamlit script, so it is not imported; only the ``_BOTS``
    assignment is evaluated, with its one helper bound to the same predicate.
    A tree without it falls back to the documented rules.
    """
    import ast

    try:
        src = (root / "app.py").read_text(encoding="utf-8")
        node = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "_BOTS" for t in n.targets))
        scope = {"_is_extended": _extended}
        exec(compile(ast.Module([node], []), "app.py:_BOTS", "exec"), scope)
        return [(name, cond) for name, cond, *_ in scope["_BOTS"]]
    except Exception:
        return [("bot-GO", lambda p: p.go),
                ("bot-70", lambda p: p.score >= 70 and p.kind == "immediate" and not p.go
                 and getattr(p, "actionable", True) and not _extended(p)),
                ("bot-BRK", lambda p: p.kind == "breakout_wait" and p.score >= 55)]


def _armed(plan) -> list[str]:
    """Live ``_BOTS`` behind the shared actionable/extended chokepoint (``_run_bots``)."""
    if not getattr(plan, "actionable", True) or _extended(plan):
        return []
    return [name for name, cond in _LIVE_BOTS if cond(plan)]


def _plan_fp(plan) -> dict | None:
    if plan is None:
        return None
    return {
        "setup": plan.setup, "kind": plan.kind, "go": bool(plan.go),
        "score": int(plan.score), "light": getattr(plan, "light", None),
        "rr": _r(plan.rr, 3), "entry": _r(plan.entry), "stop": _r(plan.stop),
        "target": _r(plan.target1), "trigger": _r(plan.trigger),
        "actionable": bool(getattr(plan, "actionable", True)),
        "extended": _extended(plan), "bots": _armed(plan),
        "checks": [[c.name, bool(c.ok), bool(getattr(c, "na", False)), c.weight]
                   for c in getattr(plan, "checks", [])],
    }


def probe_plans(frames: dict) -> dict:
    from stockanalyzer.data.schema import Timeframe
    from stockanalyzer.explain.swing import build_swing_plan
    from stockanalyzer.explain.usecase import UseCase
    from stockanalyzer.strategy import SwingPace
    from stockanalyzer.verdict.aggregate import build_verdict

    out = {}
    for tk, df in frames.items():
        points = [(f"-{o}", len(df) - 1 - o) for o in _OFFSETS if len(df) - 1 - o >= 60]
        points.append(("emerging", _EMERGING_BARS - 1))
        for tag, t in points:
            frame = df.iloc[:_EMERGING_BARS] if tag == "emerging" else df
            reports = _as_of(frame, t)
            dec = reports.get(Timeframe.M1)
            for pace in (SwingPace.FAST, SwingPace.STANDARD):
                key = f"{tk}@{tag}/{pace.name}"
                if dec is None:
                    out[key] = None
                    continue
                inv = round((build_verdict(reports).score + 1) / 2 * 100)
                plan = build_swing_plan(dec, UseCase.BUY, pace, all_reports=reports,
                                        context={"investor_pct": inv})
                out[key] = _plan_fp(plan)
    return out


def probe_bot_rules() -> dict:
    """The live swing-bot rules over every state the plan can be in.

    Frozen data only reaches the states its tickers happen to land in; this table
    reaches all of them, so any threshold or condition edit in ``_BOTS`` shows.
    """
    from types import SimpleNamespace

    out = {}
    for kind in ("immediate", "breakout_wait", "no_trade"):
        for go in (True, False):
            for actionable in (True, False):
                for extended in (False, True):
                    emerging = SimpleNamespace(extended=extended, flags=())
                    row = []
                    for score in range(0, 101):
                        plan = SimpleNamespace(kind=kind, go=go, score=score,
                                               actionable=actionable, emerging=emerging)
                        row.append("+".join(_armed(plan)) or "-")
                    out[f"{kind}|go={go}|actionable={actionable}|extended={extended}"] = {
                        "by_score": _runs(row)}
    return out


def _runs(row: list[str]) -> list[str]:
    """['-', '-', 'bot-70'] -> ['0-1:-', '2:bot-70']: compact, diff-friendly."""
    out, start = [], 0
    for i in range(1, len(row) + 1):
        if i == len(row) or row[i] != row[start]:
            out.append(f"{start}-{i - 1}:{row[start]}" if i - 1 > start else f"{start}:{row[start]}")
            start = i
    return out


def probe_replay(root: Path) -> dict:
    from stockanalyzer import replay

    sys.path.insert(0, str(root / "tests"))
    import test_replay as fx                            # the synthetic, network-free day

    out = {}
    for name, break_at in (("break", 9), ("no_break", None)):
        hist = fx._history(break_at)
        sess = replay.precompute_day(hist["i5"], hist["i30"], hist["daily"], fx._DAY,
                                     hist["prev_close"], ticker="TEST")
        for b in sess.bars:
            out[f"{name}#{b.t:%H:%M}"] = {
                "setup": b.setup, "kind": b.kind, "go": b.go, "score": b.score,
                "rr": _r(b.rr, 3), "entry": _r(b.entry), "stop": _r(b.stop),
                "target": _r(b.target), "trigger": _r(b.trigger),
                "swing_bots": list(replay.armed_swing_bots(b)),
                "orb": [b.bots.orb.fired, b.bots.orb.reason],
                "reversals": [[d.variant, d.fired, d.reason] for d in b.bots.reversals],
                "swing_block": b.bots.swing_block,
                "contradiction": b.bots.contradiction,
            }
    return out


def probe_backtest(root: Path, frames: dict) -> dict:
    sys.path.insert(0, str(root))
    import backtest as bt

    rows = {}
    for tk, df in frames.items():
        for t, label in bt.pick_points(df):
            row = bt.evaluate_point(df, t, label)
            if row is not None:
                rows[f"{tk}@{row['date']}"] = {k: (_r(v, 3) if isinstance(v, float) else v)
                                              for k, v in row.items()}
    return {"rows": rows, "summary": backtest_summary(list(rows.values()))}


def backtest_summary(rows: list[dict]) -> dict:
    """The same headline numbers ``backtest.py`` prints."""
    traded = [r for r in rows if r["acted"] and r["status"] in ("target_hit", "stop_hit", "expired")]
    wins = [r for r in traded if r["result"] > 0]
    ups = [r for r in rows if r["label"] == "UP"]
    downs = [r for r in rows if r["label"] == "DOWN"]
    chop = [r for r in rows if r["label"] == "CHOP"]
    return {
        "points": len(rows),
        "traded": len(traded),
        "win_rate": _r(len(wins) / len(traded) * 100, 2) if traded else None,
        "avg_result": _r(sum(r["result"] for r in traded) / len(traded), 3) if traded else None,
        "total_result": _r(sum(r["result"] for r in traded), 3),
        "rallies_missed": len([r for r in ups if r["verdict"] == "MISS"]),
        "rallies_caught": len([r for r in ups if r["verdict"] == "WIN" or (r["acted"] and r["result"] > 0)]),
        "drops_caught_long": len([r for r in downs if r["verdict"] == "LOSS"]),
        "chop_passed": len([r for r in chop if r["verdict"] in ("OK-PASS", "OK-AVOID") or not r["acted"]]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, help="source tree to probe")
    ap.add_argument("--out", required=True, help="JSON output path")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))                     # import THIS tree's stockanalyzer

    global _LIVE_BOTS
    _LIVE_BOTS = _live_bots(root)
    result: dict = {"root": str(root), "errors": {}}
    try:
        frames = _frames(root)
    except Exception:
        result["errors"]["frames"] = traceback.format_exc(limit=3)
        frames = {}
    for name, fn in (("plans", lambda: probe_plans(frames)),
                     ("bot_rules", probe_bot_rules),
                     ("replay", lambda: probe_replay(root)),
                     ("backtest", lambda: probe_backtest(root, frames))):
        try:
            result[name] = fn()
        except Exception:
            result[name] = None
            result["errors"][name] = traceback.format_exc(limit=3)
    Path(args.out).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

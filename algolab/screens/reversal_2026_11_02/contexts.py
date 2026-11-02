"""Cache every replay bar's reversal inputs once, so variants can be screened in seconds.

For each ticker-day of the cached history (``.local/replay-ab/hist/<newest>``, the bars
the replay A/B uses) this stores, per replay bar (09:45-15:55): the bar's OHLC, the day
card and the daily trend, built by the same as-of path the replay uses
(``asof.asof_reports`` -> ``build_day_card`` / ``asof.higher_trend``). Bars with no
decision report are skipped, as ``replay._eval_bar`` skips them. Output:
``.local/rev-ctx/<TICKER>/<YYYY-MM-DD>.pkl``; existing files are kept (the cache).

    python algolab/screens/reversal_2026_11_02/contexts.py [--jobs 11]
"""
from __future__ import annotations

import argparse
import os
import pathlib
import pickle
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = ROOT / ".local" / "rev-ctx"
_HIST: dict = {}


def hist_dir() -> pathlib.Path:
    return sorted(p for p in (ROOT / ".local" / "replay-ab" / "hist").iterdir() if p.is_dir())[-1]


def build_day(job: tuple[str, str]) -> tuple[str, str, int]:
    import pandas as pd
    from stockanalyzer import replay
    from stockanalyzer.analysis import asof
    from stockanalyzer.analysis.daycard import build_day_card

    tk, day_s = job
    out = OUT / tk / f"{day_s}.pkl"
    if out.exists():
        return tk, day_s, -1
    if tk not in _HIST:
        _HIST[tk] = pickle.loads((hist_dir() / f"{tk}.pkl").read_bytes())
    h, day = _HIST[tk], pd.Timestamp(day_s)
    daily = replay.daily_for_day(h["daily"], day)
    sess = asof.day_bars(h["i5"], day).between_time(asof.REG_START, asof.REG_END, inclusive="left")
    rows = []
    for t, r in sess.between_time(replay.REPLAY_START, replay.REPLAY_END).iterrows():
        reports = asof.asof_reports(h["i5"], h["i30"], daily, day, t)
        if asof.decision_rep(reports) is None:
            continue
        price = float(r["close"])
        rows.append({"t": t, "open": float(r["open"]), "high": float(r["high"]),
                     "low": float(r["low"]), "close": price,
                     "card": build_day_card(reports, price), "trend": asof.higher_trend(reports)})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pickle.dumps({"prev_close": h["prev_close"].get(day), "bars": rows}))
    return tk, day_s, len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()
    from stockanalyzer import replay
    jobs = []
    for pk in sorted(hist_dir().glob("*.pkl")):
        h = pickle.loads(pk.read_bytes())
        jobs += [(pk.stem, f"{d:%Y-%m-%d}") for d in replay.replayable_days(h)]
    todo = [j for j in jobs if not (OUT / j[0] / f"{j[1]}.pkl").exists()]
    print(f"{len(jobs)} ticker-days, {len(jobs) - len(todo)} cached, {len(todo)} to build", flush=True)
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        for n, (tk, d, bars) in enumerate(pool.map(build_day, todo, chunksize=4), 1):
            if n % 50 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)} ({tk} {d}: {bars} bars)", flush=True)


if __name__ == "__main__":
    main()

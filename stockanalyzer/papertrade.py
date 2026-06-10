"""Paper-trade journal — every radar notice is recorded as a proposition and
later judged against what the market actually did.

The point is the feedback loop: per alert level (60/70/80%) you can inspect
win rate and average result, and keep calibrating the algorithm with evidence.

Records persist to `.papertrade.json`. Outcome judging is pure (`judge_outcome`)
and conservative: on a bar where both stop and target were touched, the stop
counts first.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / ".papertrade.json"

# Final statuses — never re-evaluated.
_FINAL = {"target_hit", "stop_hit", "expired", "not_triggered"}


def load(path: Path = _PATH) -> list[dict]:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _write(path: Path, records: list[dict]) -> None:
    try:
        path.write_text(json.dumps(records, indent=1))
    except Exception:
        pass


def recent_duplicate(records: list[dict], ticker: str, level: int,
                     ts: float, hours: float = 24.0) -> bool:
    """True if the same (ticker, level) was already recorded within `hours`."""
    cutoff = ts - hours * 3600
    return any(r.get("ticker") == ticker and r.get("level") == level
               and r.get("ts", 0) >= cutoff for r in records)


def record(rec: dict, path: Path = _PATH) -> bool:
    """Append a proposition unless it's a fresh duplicate. Returns True if stored."""
    records = load(path)
    if recent_duplicate(records, rec.get("ticker", ""), rec.get("level", 0),
                        rec.get("ts", time.time())):
        return False
    records.append(rec)
    _write(path, records)
    return True


# --------------------------------------------------------------------------- #
# Outcome judging (pure).
# --------------------------------------------------------------------------- #
def judge_outcome(bars: list[tuple[float, float, float]], entry: float,
                  stop: float, target: float, trigger: float | None = None,
                  horizon_days: int = 3) -> tuple[str, float]:
    """Judge a LONG proposition against daily (high, low, close) bars after the alert.

    breakout_wait records carry `trigger`: the trade only activates once a bar's
    high crosses it; until then up to `horizon_days` of waiting is allowed.
    Active trades: stop first (conservative), then target, expiring after
    `horizon_days` active days at mark-to-market. Returns (status, result_pct).
    """
    if not bars:
        return "open", 0.0
    active = trigger is None
    waited = 0
    active_days = 0
    last_close = bars[-1][2]
    for high, low, close in bars:
        if not active:
            if high >= trigger:
                active = True              # filled this bar; judge the same bar below
            else:
                waited += 1
                if waited >= horizon_days:
                    return "not_triggered", 0.0
                continue
        if low <= stop:
            return "stop_hit", round((stop / entry - 1) * 100, 1)
        if high >= target:
            return "target_hit", round((target / entry - 1) * 100, 1)
        active_days += 1
        if active_days >= horizon_days:
            return "expired", round((close / entry - 1) * 100, 1)
    if not active:
        return "open", 0.0
    return "open", round((last_close / entry - 1) * 100, 1)


def _bars_after(df, alert_ts: float) -> list[tuple[float, float, float]]:
    """Daily (high, low, close) rows strictly after the alert's calendar day."""
    import pandas as pd

    idx = df.index
    try:
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
    except Exception:
        pass
    alert_day = pd.Timestamp(time.strftime("%Y-%m-%d", time.localtime(alert_ts)))
    mask = idx.normalize() > alert_day
    sub = df.loc[mask]
    return list(zip(sub["high"].astype(float), sub["low"].astype(float),
                    sub["close"].astype(float)))


def evaluate_all(frames_by_ticker: dict, path: Path = _PATH) -> list[dict]:
    """Re-judge every non-final record using fresh daily frames. Saves + returns."""
    records = load(path)
    changed = False
    for r in records:
        if r.get("status") in _FINAL:
            continue
        df = frames_by_ticker.get(r.get("ticker"))
        if df is None or not len(df):
            continue
        bars = _bars_after(df, r.get("ts", 0))
        status, result = judge_outcome(
            bars, r["entry"], r["stop"], r["target"],
            trigger=r.get("trigger"), horizon_days=int(r.get("horizon_days", 3)))
        if status != r.get("status") or result != r.get("result_pct"):
            r["status"], r["result_pct"] = status, result
            changed = True
    if changed:
        _write(path, records)
    return records


# --------------------------------------------------------------------------- #
# The report card — per-level stats for inspecting the algorithm.
# --------------------------------------------------------------------------- #
def summarize(records: list[dict]) -> dict:
    """{level: {n, wins, losses, expired, open, not_triggered, win_rate, avg_result}}
    plus an 'all' rollup. Win = target_hit; loss = stop_hit; expired counts by
    its sign; open/not_triggered excluded from the win rate."""
    out: dict = {}
    for key in (60, 70, 80, "all"):
        out[key] = dict(n=0, wins=0, losses=0, expired=0, open=0,
                        not_triggered=0, win_rate=None, avg_result=None)
    results: dict = {60: [], 70: [], 80: [], "all": []}
    for r in records:
        lv = r.get("level", 0)
        keys = ["all"] + ([lv] if lv in (60, 70, 80) else [])
        st_ = r.get("status", "open")
        res = float(r.get("result_pct", 0.0))
        for k in keys:
            o = out[k]
            o["n"] += 1
            if st_ == "target_hit":
                o["wins"] += 1
                results[k].append(res)
            elif st_ == "stop_hit":
                o["losses"] += 1
                results[k].append(res)
            elif st_ == "expired":
                o["expired"] += 1
                o["wins" if res > 0 else "losses"] += 1
                results[k].append(res)
            elif st_ == "not_triggered":
                o["not_triggered"] += 1
            else:
                o["open"] += 1
    for k, o in out.items():
        decided = o["wins"] + o["losses"]
        if decided:
            o["win_rate"] = round(o["wins"] / decided * 100)
        if results[k]:
            o["avg_result"] = round(sum(results[k]) / len(results[k]), 2)
    return out

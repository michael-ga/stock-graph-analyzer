"""Golden TA outputs — the byte-identical regression baseline for the valuation
layer (plan rule R5: with ``VALUATION_ENABLED = False`` or ``W_MAX = 0`` the TA
output must not change at all).

Deterministic by construction: daily OHLCV for ~20 tickers is frozen to CSV once
(``capture``), and the goldens are the real ``build_swing_plan`` output replayed
over those frozen frames at several as-of points — the same daily-only
multi-timeframe view ``backtest.py`` uses. No network at test time.

Capture (network, run once / when intentionally re-baselining):
    .venv\\Scripts\\python.exe -m tests.valuation.golden_ta capture
Regenerate goldens from the frozen CSVs (no network):
    .venv\\Scripts\\python.exe -m tests.valuation.golden_ta rebuild
"""
from __future__ import annotations

import dataclasses
import enum
import json
import sys
from pathlib import Path

import pandas as pd

from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.data.schema import Timeframe, validate_ohlcv
from stockanalyzer.explain.swing import build_swing_plan
from stockanalyzer.explain.usecase import UseCase
from stockanalyzer.strategy import SwingPace
from stockanalyzer.verdict.aggregate import build_verdict

HERE = Path(__file__).resolve().parent
OHLCV_DIR = HERE / "fixtures" / "ohlcv"
GOLDEN_PATH = HERE / "fixtures" / "ta_golden.json"

# Spread across the valuation archetypes the plan routes on (mature, growth,
# unprofitable growth, financial, REIT, cyclical/semis, energy, recent listing)
# so the regression baseline covers every card the fusion layer will touch.
TICKERS = [
    "AAPL", "MSFT", "NVDA", "INTC", "AMD", "SMCI", "NOK", "TSLA", "PLTR", "SNOW",
    "RIVN", "PFE", "NKE", "KO", "WMT", "JPM", "BAC", "XOM", "O", "CRCL",
]
# Bars back from the end of the frozen frame at which to replay the plan.
ASOF_OFFSETS = (0, 20, 60, 120)
# One extra thin-history point per ticker exercises the emerging-score path
# (< EMERGING_MAX_BARS daily bars) exactly as a fresh listing would see it.
EMERGING_BARS = 30
_LOOKBACKS = ((Timeframe.M1, 22), (Timeframe.M6, 126), (Timeframe.Y1, 252))


def _jsonable(obj):
    if dataclasses.is_dataclass(obj):
        return {f.name: _jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, float):
        return repr(obj)          # exact — golden equality is bit-for-bit
    return obj


def load_frame(ticker: str) -> pd.DataFrame:
    df = pd.read_csv(OHLCV_DIR / f"{ticker}.csv", index_col=0, parse_dates=True)
    return validate_ohlcv(df)


def as_of_reports(df: pd.DataFrame, t: int) -> dict:
    out = {}
    for tf, lookback in _LOOKBACKS:
        sl = df.iloc[max(0, t + 1 - lookback):t + 1]
        if len(sl) >= 20:
            out[tf] = analyze_timeframe(sl)
    return out


def plan_output(df: pd.DataFrame, t: int, pace: SwingPace) -> dict | None:
    """The TA result the fusion layer will consume, as exact-JSON."""
    reports = as_of_reports(df, t)
    dec = reports.get(Timeframe.M1)
    if dec is None:
        return None
    inv = round((build_verdict(reports).score + 1) / 2 * 100)
    plan = build_swing_plan(dec, UseCase.BUY, pace, all_reports=reports,
                            context={"investor_pct": inv})
    return None if plan is None else _jsonable(plan)


def golden_cases(ticker: str, df: pd.DataFrame):
    n = len(df)
    points = [(f"t-{o}", n - 1 - o) for o in ASOF_OFFSETS if n - 1 - o >= 21]
    if n > EMERGING_BARS:
        points.append((f"emerging-{EMERGING_BARS}", EMERGING_BARS - 1))
    for label, t in points:
        for pace in (SwingPace.STANDARD, SwingPace.FAST):
            yield f"{ticker}|{label}|{pace.value}", df.iloc[:t + 1], t, pace


def compute_goldens() -> dict:
    out = {}
    for tk in TICKERS:
        path = OHLCV_DIR / f"{tk}.csv"
        if not path.exists():
            continue
        df = load_frame(tk)
        for key, sub, t, pace in golden_cases(tk, df):
            out[key] = plan_output(sub, t, pace)
    return out


def capture() -> None:
    import yfinance as yf

    OHLCV_DIR.mkdir(parents=True, exist_ok=True)
    for tk in TICKERS:
        raw = yf.download(tk, period="2y", interval="1d", auto_adjust=False,
                          progress=False)
        if raw is None or raw.empty:
            print(f"{tk}: no data — skipped")
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.index.name = "date"
        validate_ohlcv(df).to_csv(OHLCV_DIR / f"{tk}.csv")
        print(f"{tk}: {len(df)} bars")
    rebuild()


def rebuild() -> None:
    goldens = compute_goldens()
    GOLDEN_PATH.write_text(json.dumps(goldens, indent=1, sort_keys=True,
                                      ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(goldens)} golden plans -> {GOLDEN_PATH}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "rebuild"
    {"capture": capture, "rebuild": rebuild}[cmd]()

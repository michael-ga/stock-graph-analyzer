"""Download 60d of 5m (with pre/post) + 1y daily once; pickle for the screen.

Writes to .local/setup-screen/ (git-ignored), or $SETUP_SCREEN_DATA. yfinance
keeps only the trailing 60 sessions of 5m bars, so a later run screens a later
window than the one in algolab/LEARNINGS.md (fetched 2026-10-24).
"""
import os
import pathlib
import sys

import pandas as pd
import yfinance as yf

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from stockanalyzer.replay import yf_intraday  # noqa: E402

out = pathlib.Path(os.environ.get("SETUP_SCREEN_DATA", ROOT / ".local" / "setup-screen"))
out.mkdir(parents=True, exist_ok=True)
for t in ["SNXX", "SNDQ", "NBIZ", "NBIC", "SNDK", "NBIS"]:
    i5 = yf_intraday(t, "5m", "60d")
    d = yf.download(t, period="1y", interval="1d", auto_adjust=False, progress=False, threads=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    i5.to_pickle(out / f"{t}_5m.pkl")
    d.to_pickle(out / f"{t}_1d.pkl")
    reg = i5.between_time("09:30", "15:55")
    print(t, len(i5), "5m bars;", len(set(reg.index.date)), "sessions;", reg.index[0], "->",
          reg.index[-1], "| daily", len(d))

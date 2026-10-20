"""Multi-day replay scan — which ticker-days actually had something happen?

The replay simulator answers "what happened on THIS day". Most days, nothing
does: no bot fires and the whole ten-second rebuild teaches nothing. This module
runs the same headless replay over a ticker list × a set of trading days, with
every bot switched on, and boils each day down to one ``DayResult`` — signals,
the bots' trades and their R, the day's move, what kept the swing plan out — so a
grid can point at the handful of days worth stepping through by hand.

It is deliberately a thin loop over ``stockanalyzer.replay``: same precompute,
same ``apply_bar``, same ``force_flat``, same scorecard. A scan row and the
replay page's end-of-day review of that day therefore cannot disagree.

A ticker-day costs ~10 s of indicator math, so finished days are cached in a
small SQLite file keyed by an **engine fingerprint** (a hash of the decision
code). Change a gate or a weight and every cached row silently goes stale instead
of reporting what the old algorithm would have done.

No Streamlit import lives here — the page is ``replay_scan_page.py``.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

import pandas as pd

from stockanalyzer import replay
from stockanalyzer.data import store

_PKG = Path(__file__).resolve().parent

# The code that decides what a day's bots do. Editing any of it invalidates the
# cache; presentation code (charting, ui) deliberately does not.
_ENGINE_SOURCES = ("analysis", "explain", "verdict", "replay.py", "strategy.py",
                   "virtualbook.py", "data/store.py")

ALL_BOTS = {key: True for key in replay.BOT_KEYS}

# Bump when DayResult's shape changes, so old cached payloads are recomputed.
_RESULT_VERSION = 1


# --------------------------------------------------------------------------- #
# The per-day record.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DayResult:
    """One ticker-day, summarized. Plain data — it round-trips through JSON."""
    ticker: str
    day: str                                   # YYYY-MM-DD
    bars: int = 0
    signal_bars: int = 0                       # bars where any bot would act
    first_signal: str | None = None            # HH:MM of the first one
    families: tuple[str, ...] = ()             # subset of replay.BOT_KEYS
    signals: tuple[dict, ...] = ()             # {t, bot, entry}, one per bot-bar
    trades: tuple[dict, ...] = ()              # the bots' book, closed at EOD
    net_r: float = 0.0
    net_usd: float = 0.0
    wins: int = 0
    losses: int = 0
    ideas: int = 0                             # distinct plans behind the trades
    prev_close: float | None = None
    open: float | None = None
    close: float | None = None
    high: float | None = None
    low: float | None = None
    block_reason: str = ""                     # modal swing-plan gate
    block_bars: int = 0
    clocks: tuple[str, ...] = ()               # the per-bar sparkline
    closes: tuple[float, ...] = ()
    complete: bool = True                      # False for a session still trading
    error: str = ""

    @property
    def date(self) -> pd.Timestamp:
        return pd.Timestamp(self.day)

    @property
    def traded(self) -> bool:
        return bool(self.trades)

    @property
    def signalled(self) -> bool:
        return self.signal_bars > 0

    @property
    def change_pct(self) -> float | None:
        if not self.prev_close or self.close is None:
            return None
        return round((self.close / self.prev_close - 1) * 100, 2)

    @property
    def range_pct(self) -> float | None:
        if not self.open or self.high is None or self.low is None:
            return None
        return round((self.high - self.low) / self.open * 100, 2)

    @property
    def status(self) -> str:
        """``error`` / ``traded`` / ``signal`` / ``quiet`` — the grid's colour."""
        if self.error:
            return "error"
        if self.traded:
            return "traded"
        return "signal" if self.signalled else "quiet"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "DayResult":
        raw = json.loads(text)
        for k in ("families", "signals", "trades", "clocks", "closes"):
            raw[k] = tuple(raw.get(k) or ())
        return cls(**raw)


# --------------------------------------------------------------------------- #
# Running one day.
# --------------------------------------------------------------------------- #
def bar_signals(bar: replay.ReplayBar) -> list[tuple[str, str, float | None]]:
    """(family, bot, entry) for every bot that would act on this bar — the same
    conditions ``apply_bar`` places on, so a signal here is a position there
    unless that bot already holds the ticker."""
    out = []
    if bar.bots.orb.fired:
        out += [("orb", name, bar.bots.orb.entry) for name, _ in replay.GAP_BOTS]
    out += [("rev", d.variant, d.entry) for d in bar.bots.fired_reversals]
    out += [("swing", name, bar.entry) for name in replay.armed_swing_bots(bar)]
    return out


def summarize(sess: replay.ReplaySession, db: Path) -> DayResult:
    """Run every bot through the whole session on the book at ``db`` and boil the
    day down. ``db`` should be empty — this flattens and scores what is in it."""
    if not sess.bars:
        return DayResult(sess.ticker, f"{sess.day:%Y-%m-%d}",
                         prev_close=sess.prev_close, error="no session bars")
    for i in range(len(sess.bars)):
        replay.apply_bar(sess, i, ALL_BOTS, db)
    replay.force_flat(sess, db)

    signals, families, signal_bars = [], [], 0
    for bar in sess.bars:
        hits = bar_signals(bar)
        signal_bars += bool(hits)
        for family, bot, entry in hits:
            families.append(family)
            signals.append({"t": bar.clock, "bot": bot,
                            "entry": None if entry is None else round(float(entry), 4)})

    clock_of = {b.epoch: b.clock for b in sess.bars}
    card = replay.scorecard(sess, db)
    trades, net_r = [], 0.0
    for row in card["book"]:
        if row.get("close_reason") == "cancelled":
            continue
        r = replay.trade_r(row)
        net_r += r or 0.0
        trades.append({
            "trader": row["trader"],
            "setup": (row.get("snapshot") or {}).get("setup", "?"),
            "in": clock_of.get(row.get("activated_ts") or row.get("opened_ts")),
            "out": clock_of.get(row.get("closed_ts")),
            "entry": row["entry"], "exit": row.get("exit_price"),
            "why": row.get("close_reason") or row.get("status"),
            "r": r, "pnl_pct": row.get("pnl_pct"), "pnl_usd": row.get("pnl_usd"),
        })
    totals = card["stats"].get("totals") or {}
    reason, n_blocked, _ = replay.blocked_digest(sess)
    reg = sess.session_full
    last = sess.bars[-1]
    return DayResult(
        ticker=sess.ticker, day=f"{sess.day:%Y-%m-%d}", bars=len(sess.bars),
        signal_bars=signal_bars,
        first_signal=signals[0]["t"] if signals else None,
        families=tuple(k for k in replay.BOT_KEYS if k in families),
        signals=tuple(signals), trades=tuple(trades),
        net_r=round(net_r, 2), net_usd=round(float(totals.get("total_pnl_usd") or 0), 2),
        wins=int(totals.get("wins") or 0), losses=int(totals.get("losses") or 0),
        ideas=len(card["ideas"].get("ideas") or []),
        prev_close=sess.prev_close,
        open=round(float(reg["open"].iloc[0]), 4),
        close=round(float(reg["close"].iloc[-1]), 4),
        high=round(float(reg["high"].max()), 4),
        low=round(float(reg["low"].min()), 4),
        block_reason=reason, block_bars=n_blocked,
        clocks=tuple(b.clock for b in sess.bars),
        closes=tuple(round(b.close, 4) for b in sess.bars),
        complete=last.clock >= replay.REPLAY_END,
    )


def scan_day(hist: dict, ticker: str, day) -> DayResult:
    """Precompute one day from a ``replay.load_history`` bundle and summarize it
    on a throwaway book. Never touches ``trades.db`` or a practice book."""
    day = pd.Timestamp(day).normalize()
    if day not in set(replay.replayable_days(hist)):
        return DayResult(ticker, f"{day:%Y-%m-%d}",
                         error="no 5-minute data for this session")
    sess = replay.precompute_day(hist["i5"], hist["i30"], hist["daily"], day,
                                 hist["prev_close"].get(day), ticker=ticker)
    # A bare temp dir: no .virtualbook.json beside the DB for the store to seed from.
    with tempfile.TemporaryDirectory(prefix="replay-scan-") as tmp:
        db = Path(tmp) / "scan.db"
        try:
            return summarize(sess, db)
        finally:
            store.release(db)


# --------------------------------------------------------------------------- #
# The cache.
# --------------------------------------------------------------------------- #
_fingerprint_memo: str | None = None


def engine_fingerprint() -> str:
    """A short hash of every source file that shapes a day's decisions."""
    global _fingerprint_memo
    if _fingerprint_memo is None:
        h = hashlib.sha256(f"v{_RESULT_VERSION}".encode())
        files: list[Path] = []
        for rel in _ENGINE_SOURCES:
            p = _PKG / rel
            files += sorted(p.rglob("*.py")) if p.is_dir() else [p]
        for f in files:
            h.update(f.relative_to(_PKG).as_posix().encode())
            h.update(f.read_bytes())
        _fingerprint_memo = h.hexdigest()[:16]
    return _fingerprint_memo


def cache_path(root: Path) -> Path:
    """Beside the practice books, under the same (git-ignored) ``.replay`` dir.
    Scan results are a function of public market data and the engine, so unlike
    practice books they are shared between users."""
    base = Path(os.environ.get("STOCKANALYZER_REPLAY_DIR") or root)
    return base / ".replay" / "scan-cache.db"


@contextmanager
def _cache(path: Path):
    """One short-lived connection per call, committed on success and always
    closed — several browser sessions may be scanning at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    try:
        with con:
            con.execute("""CREATE TABLE IF NOT EXISTS day_scan (
                               ticker TEXT NOT NULL, day TEXT NOT NULL,
                               engine TEXT NOT NULL, payload TEXT NOT NULL,
                               created REAL NOT NULL,
                               PRIMARY KEY (ticker, day, engine))""")
            yield con
    finally:
        con.close()


def cache_get(path: Path, ticker: str, day, engine: str | None = None) -> DayResult | None:
    if not Path(path).exists():
        return None
    with _cache(Path(path)) as con:
        row = con.execute(
            "SELECT payload FROM day_scan WHERE ticker=? AND day=? AND engine=?",
            (ticker.upper(), f"{pd.Timestamp(day):%Y-%m-%d}",
             engine or engine_fingerprint())).fetchone()
    try:
        return DayResult.from_json(row[0]) if row else None
    except (TypeError, ValueError):
        return None                                    # an old shape — recompute


def cache_put(path: Path, result: DayResult, engine: str | None = None) -> None:
    """Store a finished day. A session still trading, or a failed run, is never
    cached — the next scan should see the rest of the day / try again."""
    if result.error or not result.complete:
        return
    with _cache(Path(path)) as con:
        con.execute("INSERT OR REPLACE INTO day_scan VALUES (?, ?, ?, ?, ?)",
                    (result.ticker.upper(), result.day, engine or engine_fingerprint(),
                     result.to_json(), time.time()))


# --------------------------------------------------------------------------- #
# The scan.
# --------------------------------------------------------------------------- #
@dataclass
class ScanPlan:
    """What a scan will do, before it does it — so a UI can quote the cost."""
    jobs: list[tuple[str, pd.Timestamp]] = field(default_factory=list)
    cached: dict = field(default_factory=dict)      # (ticker, day) -> DayResult

    @property
    def to_compute(self) -> list[tuple[str, pd.Timestamp]]:
        return [j for j in self.jobs if j not in self.cached]


def plan(tickers, days, cache: Path | None) -> ScanPlan:
    """Every (ticker, day) pair, with whatever the cache already knows."""
    out = ScanPlan()
    for tk in tickers:
        for day in sorted(pd.Timestamp(d).normalize() for d in days):
            out.jobs.append((tk, day))
            hit = cache_get(cache, tk, day) if cache is not None else None
            if hit is not None:
                out.cached[(tk, day)] = hit
    return out


def run(scan: ScanPlan, load, cache: Path | None = None, progress=None) -> list[DayResult]:
    """Fill in every job: cached rows as-is, the rest computed and cached.

    ``load(ticker) -> history`` is called at most once per ticker, and only for
    tickers with uncached days. ``progress(done, total, result)`` fires after
    every job so a UI can stream results in as they land.
    """
    hists: dict = {}
    results: list[DayResult] = []
    for n, (tk, day) in enumerate(scan.jobs, 1):
        res = scan.cached.get((tk, day))
        if res is None:
            try:
                if tk not in hists:
                    hists[tk] = load(tk)
                res = scan_day(hists[tk], tk, day)
            except Exception as exc:          # one bad ticker must not sink the scan
                res = DayResult(tk, f"{day:%Y-%m-%d}", error=str(exc) or type(exc).__name__)
            if cache is not None:
                cache_put(cache, res)
        results.append(res)
        if progress is not None:
            progress(n, len(scan.jobs), res)
    return results


def grid(results: list[DayResult], tickers=None) -> pd.DataFrame:
    """Dates down, tickers across — each cell the day's DayResult (or None).
    ``tickers`` fixes the columns, so a ticker with nothing scanned yet still
    gets one."""
    by = {(r.day, r.ticker): r for r in results}
    days = sorted({r.day for r in results}, reverse=True)
    tickers = list(dict.fromkeys([*(tickers or ()), *(r.ticker for r in results)]))
    return pd.DataFrame([[by.get((d, t)) for t in tickers] for d in days],
                        index=days, columns=tickers)


# --------------------------------------------------------------------------- #
# CLI: python -m stockanalyzer.replay_scan AAPL MSFT --from 2026-09-01
# --------------------------------------------------------------------------- #
def main(argv=None) -> None:
    import argparse
    import sys

    try:                                   # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Scan replay days for bot activity.")
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--from", dest="start", help="first ET day, YYYY-MM-DD")
    ap.add_argument("--to", dest="end", help="last ET day, YYYY-MM-DD")
    ap.add_argument("--all", action="store_true", help="list quiet days too")
    args = ap.parse_args(argv)
    tickers = [t.upper() for t in args.tickers]
    root = Path(__file__).resolve().parents[1]

    hists: dict = {}

    def _load(tk: str) -> dict:
        if tk not in hists:
            hists[tk] = replay.load_history(tk)
        return hists[tk]

    days: set = set()
    for tk in tickers:
        try:
            days |= set(replay.replayable_days(_load(tk)))
        except Exception as exc:
            print(f"{tk}: load failed ({exc})")
    lo = pd.Timestamp(args.start) if args.start else None
    hi = pd.Timestamp(args.end) if args.end else None
    days = {d for d in days if (lo is None or d >= lo) and (hi is None or d <= hi)}

    scan = plan(tickers, days, cache_path(root))
    print(f"{len(scan.jobs)} ticker-days, {len(scan.to_compute)} to compute "
          f"(~{len(scan.to_compute) * 10 // 60} min)")

    def _tick(done, total, r):
        if args.all or r.status in ("traded", "signal", "error"):
            print(f"[{done}/{total}] {r.day} {r.ticker:<6} {r.status:<7} "
                  f"trades={len(r.trades)} R={r.net_r:+.2f} "
                  f"first={r.first_signal or '-'} {r.error}")

    run(scan, _load, cache_path(root), progress=_tick)


if __name__ == "__main__":
    main()

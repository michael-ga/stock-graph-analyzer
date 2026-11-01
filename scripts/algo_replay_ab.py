"""Real-history A/B for the intraday algorithm: every non-swing bot and every setup,
base vs change, on the same real bars.

The frozen probes in ``algo_probe.py`` check decisions on one synthetic day. This
replays REAL yfinance history on two source trees and compares, per bot and per
setup, the trades, win % and net R, so a change shows whether it moves the book
forward:

    python scripts/algo_replay_ab.py --base <ref> [--change <ref>] [--mode fast|full]
                                     [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                                     [--report out.md] [--json out.json] [TICKER ...]

- **Bots** (Gap-and-Go ``bot-gap-fixed``/``bot-gap-mgd``, the ``rev-*`` reversal
  variants) run through the replay simulator, ``replay_scan.scan_day``: the same
  book, fills and managed exits the replay page uses. Swing bots are left out.
- **Setups** (every ``setups.SETUPS`` variant of that tree) run through
  ``setups.harness.run_day``, the fast path. A tree without ``setups/`` has none, so
  a new setup shows as added and a deleted one as removed.
- ``--mode fast``: the ETF pairs (SNXX SNDQ NBIZ NBIS; NBIC is too illiquid to replay,
  NBIS stands in). ``--mode full``: the bots' tickers plus the ETF pairs. Both cover
  every session yfinance keeps (~60) unless ``--from``/``--to`` narrow it.
- The change is this working tree (uncommitted edits included) unless ``--change``
  names a ref; the base is a temporary checkout of ``--base``.
- History is fetched once per ticker per day, pickled under
  ``.local/replay-ab/hist/<date>``, and shared, so both trees replay byte-identical
  bars. Results are cached under ``.local/replay-ab/cache`` by (hash of the tree's
  ``stockanalyzer`` code, ticker, history hash): an unchanged base replays once.
- Cost: the replay is ~12 s per ticker-day per tree (measured on INTC 2026-10-22);
  the setups harness adds well under a second. (tree, ticker) jobs run in parallel.

**Regression rule** (owner's choice, 2026-11-01: net R and win %): FAIL when the
whole-book net R falls, or when a bot/setup that exists on both sides and whose
trades changed loses net R or drops more than ``WIN_PCT_DROP`` points of win %.

Exit: 0 = identical trades, 1 = trades differ without a regression, 3 = regression,
2 = could not run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from pathlib import Path

MODES = {
    "fast": ("SNXX", "SNDQ", "NBIZ", "NBIS"),
    "full": ("INTC", "AMD", "NVDA", "TSLA", "PLTR", "SMCI", "MU", "SNDK",
             "SNXX", "SNDQ", "NBIZ", "NBIS"),
}
MAX_DAYS = {"fast": 20, "full": 12}     # replayed ticker-days per ticker (bots)
GAP_PICK = 0.01             # a gap this size either way may become a Gap-and-Go day
WIN_PCT_DROP = 5.0          # points of win % an existing bot/setup may not lose
_EPS = 1e-9


def pick_days(hist: dict, max_days: int, start=None, end=None) -> list[dict]:
    """The ticker-days worth replaying for the bots: every day with a ≥1% gap (from
    the 09:30 open or the pre-market open, so a change to how the gap is measured
    still lands on a replayed day), then the widest-range days, where reversals
    and managed exits get exercised. Setups don't need this: they replay every day.
    Pure and cheap (5-minute bars only), so both trees replay the same days."""
    import pandas as pd
    i5, pcs = hist["i5"], hist["prev_close"]
    idx = i5.index.tz_localize(None) if i5.index.tz is not None else i5.index
    rows = []
    for d in hist["days"]:
        if (start is not None and d < pd.Timestamp(start)) or (end is not None and d > pd.Timestamp(end)):
            continue
        bars = i5[idx.normalize() == d]
        reg = bars.between_time("09:30", "15:55")
        pc = pcs.get(d)
        if reg.empty or not pc:
            continue
        o = float(reg["open"].iloc[0])
        gap = max(abs(o / pc - 1), abs(float(bars["open"].iloc[0]) / pc - 1))
        rng = (float(reg["high"].max()) - float(reg["low"].min())) / o
        rows.append({"day": f"{d:%Y-%m-%d}", "gap": round(gap * 100, 2),
                     "range": round(rng * 100, 2)})
    gaps = sorted((r for r in rows if r["gap"] >= GAP_PICK * 100), key=lambda r: -r["gap"])
    picked = {r["day"]: {**r, "why": "gap"} for r in gaps[:max_days]}
    for r in sorted(rows, key=lambda r: -r["range"]):
        if len(picked) >= max_days:
            break
        picked.setdefault(r["day"], {**r, "why": "range"})
    return sorted(picked.values(), key=lambda r: r["day"])


# --------------------------------------------------------------------------- #
# Worker: one tree, one ticker. Imports the tree's own code.
# --------------------------------------------------------------------------- #
def tree_fingerprint(tree: Path) -> str:
    """Hash of every ``stockanalyzer`` source file: any code change, decision path
    or input, gives a new key, so a cached result can never hide a change."""
    h = hashlib.sha256()
    for p in sorted((tree / "stockanalyzer").rglob("*.py")):
        h.update(p.relative_to(tree).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def _bots_rows(replay, replay_scan, hist, ticker, day) -> dict:
    """One replay day boiled down to the non-swing bots."""
    keep = {name for name, _ in replay.GAP_BOTS}
    keep |= {cfg.name for cfg in replay.reversal.REVERSAL_VARIANTS}
    setup_names = {cfg.name for m in getattr(replay, "SETUPS", {}).values() for cfg in m.VARIANTS}
    row = asdict(replay_scan.scan_day(hist, ticker, day))
    trades, replayed_setups = [], []
    for t in row.get("trades") or []:
        if t["trader"] in keep:
            fam = "orb" if t["trader"].startswith("bot-gap") else "rev"
            trades.append({**t, "family": fam})
        elif t["trader"] in setup_names:
            replayed_setups.append(t)             # setups the tree's replay also ran
    return {"ticker": ticker, "day": row["day"], "error": row.get("error", ""),
            "trades": trades, "replayed_setups": replayed_setups,
            "signals": [s for s in row.get("signals") or [] if s["bot"] in keep]}


def parity(replayed: list[dict], harness: list[dict]) -> list[str]:
    """Replay-book setup trades vs the harness's for one day: same variants, entry
    clock, entry price and gross R (to 0.01), or a line saying what differs."""
    def key(t):
        return (t["trader"], t["in"], round(float(t["entry"]), 2))
    a = {key(t): t["r"] for t in replayed}
    b = {key(t): t["gross_r"] for t in harness}
    out = [f"replay only {k}" for k in a.keys() - b.keys()]
    out += [f"harness only {k}" for k in b.keys() - a.keys()]
    out += [f"R differs {k}: replay {a[k]} vs harness {b[k]}" for k in a.keys() & b.keys()
            if a[k] is not None and abs(a[k] - b[k]) > 0.011]
    return out


def _setup_trades(tree_setups, hist, i, ticker, timeframe) -> list[dict]:
    """Every setup variant of this tree on day ``i``, through the harness."""
    if tree_setups is None:
        return []
    setups, harness = tree_setups
    variants = [(m, cfg) for m in setups.SETUPS.values() for cfg in m.VARIANTS]
    hh = {"i5": hist["i5"], "daily": hist["daily"][timeframe.Y1], "days": hist["days"]}
    out = []
    for t in harness.run_day(hh, i, variants, ticker=ticker):
        out.append({"trader": t["variant"], "setup": t["setup"], "family": "setups",
                    "in": t["t"], "out": None, "entry": t["entry"], "exit": None,
                    "why": t["status"], "r": round(t["net_r"], 3),
                    "gross_r": round(t["gross_r"], 3)})
    return out


def worker(tree: Path, hist_dir: Path, out: Path, start: str | None, end: str | None,
           ticker: str, load_only: bool, cache_dir: Path | None = None,
           max_days: int | None = None) -> None:
    sys.path.insert(0, str(tree.resolve()))
    import pandas as pd
    from stockanalyzer import replay, replay_scan
    from stockanalyzer.data.schema import Timeframe
    try:
        from stockanalyzer import setups
        from stockanalyzer.setups import harness
        tree_setups = (setups, harness)
    except ImportError:                           # a tree from before setups/ existed
        tree_setups = None

    pk = hist_dir / f"{ticker}.pkl"
    t0 = time.time()
    if pk.exists():
        raw = pk.read_bytes()
        hist = pickle.loads(raw)
    else:
        hist = replay.load_history(ticker)
        raw = pickle.dumps(hist)
        pk.write_bytes(raw)
    picks_file = hist_dir / f"{ticker}.picks-{max_days}-{start}-{end}.json"
    if load_only:
        if max_days:                              # chosen once, read by both trees
            picks_file.write_text(json.dumps(pick_days(hist, max_days, start, end)),
                                  encoding="utf-8")
        return
    picked = ({p["day"] for p in json.loads(picks_file.read_text(encoding="utf-8"))}
              if max_days else None)

    cache_file, cached = None, {}
    if cache_dir is not None:
        key = (f"{tree_fingerprint(tree)[:16]}-{ticker}-"
               f"{hashlib.sha256(raw).hexdigest()[:12]}.json")
        cache_file = cache_dir / key
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))

    lo = pd.Timestamp(start) if start else None
    hi = pd.Timestamp(end) if end else None
    days = list(hist["days"])
    replayable = set(replay.replayable_days(hist))
    rows, hits = [], 0
    for i, d in enumerate(days):
        if (lo is not None and d < lo) or (hi is not None and d > hi) or d not in replayable:
            continue
        k = f"{d:%Y-%m-%d}"
        replay_it = picked is None or k in picked
        if k in cached and (cached[k].get("replayed") or not replay_it):
            row = cached[k]
            if not replay_it:                     # keep only what this run covers
                row = {**row, "trades": [t for t in row["trades"] if t["family"] == "setups"],
                       "signals": [], "replayed": False}
            rows.append(row)
            hits += 1
            continue
        try:
            row = (_bots_rows(replay, replay_scan, hist, ticker, d) if replay_it else
                   {"ticker": ticker, "day": k, "error": "", "trades": [], "signals": []})
            row["replayed"] = replay_it
            setups_today = _setup_trades(tree_setups, hist, i, ticker, Timeframe)
            if replay_it and "setups" in replay.BOT_KEYS:
                row["parity"] = parity(row.pop("replayed_setups", []), setups_today)
            row.pop("replayed_setups", None)
            row["trades"] += setups_today
        except Exception as exc:                  # one bad day must not sink the run
            row = {"ticker": ticker, "day": k, "trades": [], "signals": [],
                   "error": f"{type(exc).__name__}: {exc}"}
        cached[k] = row
        rows.append(row)
    if cache_file is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cached, default=str), encoding="utf-8")
    out.write_text(json.dumps({"rows": rows, "seconds": round(time.time() - t0, 1),
                               "cache_hits": hits}, default=str), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Comparison.
# --------------------------------------------------------------------------- #
def _tkey(t: dict) -> tuple:
    return (t["trader"], t["setup"], t["in"], t["out"], t["entry"], t["exit"], t["r"])


def per_bot(rows: dict) -> dict:
    """{bot or setup variant: n, wins, r, days, win_pct, avg_r, family}."""
    c = defaultdict(lambda: {"n": 0, "r": 0.0, "wins": 0, "days": set(), "family": ""})
    for (tk, day), r in rows.items():
        for t in r.get("trades") or []:
            b = c[t["trader"]]
            b["n"] += 1
            b["r"] += t["r"] or 0.0
            b["wins"] += (t["r"] or 0) > 0
            b["days"].add(day)
            b["family"] = t.get("family", "")
    out = {}
    for bot, b in c.items():
        out[bot] = {"n": b["n"], "wins": b["wins"], "r": round(b["r"], 2),
                    "days": len(b["days"]), "family": b["family"],
                    "win_pct": round(b["wins"] / b["n"] * 100, 1) if b["n"] else None,
                    "avg_r": round(b["r"] / b["n"], 3) if b["n"] else None}
    return out


def compare(base: dict, change: dict) -> dict:
    """``base``/``change``: {(ticker, day): row}."""
    diff_days, signal_only, errors = [], [], []
    changed_bots: set[str] = set()
    for k in sorted(set(base) | set(change)):
        a, b = base.get(k, {}), change.get(k, {})
        if a.get("error") or b.get("error"):
            errors.append((k, a.get("error"), b.get("error")))
        ta = Counter(_tkey(t) for t in a.get("trades") or [])
        tb = Counter(_tkey(t) for t in b.get("trades") or [])
        if ta != tb:
            only_a, only_b = list((ta - tb).elements()), list((tb - ta).elements())
            changed_bots |= {t[0] for t in only_a + only_b}
            diff_days.append({"key": k,
                              "r": (round(sum(t["r"] or 0 for t in a.get("trades") or []), 2),
                                    round(sum(t["r"] or 0 for t in b.get("trades") or []), 2)),
                              "base_only": only_a, "change_only": only_b})
        elif ([(s["t"], s["bot"]) for s in a.get("signals") or []]
              != [(s["t"], s["bot"]) for s in b.get("signals") or []]):
            signal_only.append({"key": k})
    pa, pb = per_bot(base), per_bot(change)
    book = tuple({"n": sum(x["n"] for x in p.values()),
                  "wins": sum(x["wins"] for x in p.values()),
                  "r": round(sum(x["r"] for x in p.values()), 2)} for p in (pa, pb))
    return {"days": len(set(base) | set(change)), "keys": sorted(set(base) | set(change)),
            "diff_days": diff_days,
            "signal_only": signal_only, "errors": errors, "changed_bots": sorted(changed_bots),
            "bots": (pa, pb), "book": book,
            "regressions": regressions(pa, pb, changed_bots, book)}


def _win(x: dict) -> float | None:
    return x["wins"] / x["n"] * 100 if x and x.get("n") else None


def regressions(base: dict, change: dict, changed_bots: set[str], book: tuple) -> list[str]:
    """The owner's rule: net R and win %. Plain-English line per failure."""
    out = []
    if book[1]["r"] < book[0]["r"] - _EPS:
        out.append(f"whole-book net R fell {book[0]['r']:+.2f} → {book[1]['r']:+.2f}")
    for bot in sorted(set(base) & set(change) & set(changed_bots)):
        a, b = base[bot], change[bot]
        if b["r"] < a["r"] - _EPS:
            out.append(f"`{bot}` net R fell {a['r']:+.2f} → {b['r']:+.2f}")
        wa, wb = _win(a), _win(b)
        if wa is not None and wb is not None and wb < wa - WIN_PCT_DROP:
            out.append(f"`{bot}` win % fell {wa:.0f}% → {wb:.0f}% (more than "
                       f"{WIN_PCT_DROP:g} points)")
    for bot in sorted(set(base) & set(changed_bots) - set(change)):
        out.append(f"`{bot}` no longer trades (was {base[bot]['n']} trades, "
                   f"{base[bot]['r']:+.2f} R)")
    return out


def _pct(x) -> str:
    return "–" if x is None else f"{x:.0f}%"


def render(cmp: dict, ctx: dict) -> str:
    regs = cmp["regressions"]
    if regs:
        verdict = "REGRESSION: the change makes the intraday book worse on real days"
    elif cmp["diff_days"]:
        verdict = "TRADES DIFFER, no regression"
    else:
        verdict = "NO TRADE DIFFERENCE on these real days"
    (ba, bb) = cmp["book"]
    out = [f"# Replay A/B: **{verdict}**", "",
           f"Base `{ctx['base']}` vs `{ctx['change']}`. Mode `{ctx['mode']}`: "
           f"{', '.join(ctx['tickers'])}. Days {ctx['start']} → {ctx['end']} "
           f"({cmp['days']} ticker-days). Wall time {ctx['wall']}s"
           + (f", cache hits {ctx['cache_hits']}" if ctx.get("cache_hits") else "") + ".",
           *([f"Coverage: {ctx['coverage']}."] if ctx.get("coverage") else []),
           *([f"Setups parity (replay book vs harness, change side): {ctx['parity']}."]
             if ctx.get("parity") else []), ""]
    if regs:
        out += ["## Regressions (net R + win % rule)", ""] + [f"- {r}" for r in regs] + [""]
    out += ["## Per bot and setup (base → change)", "",
            "| Bot / setup | Family | Trades | Days | Win % | Avg R | Net R | Δ net R |",
            "|---|---|---|---|---|---|---|---|"]
    pa, pb = cmp["bots"]
    zero = {"n": 0, "wins": 0, "r": 0.0, "days": 0, "win_pct": None, "avg_r": None, "family": ""}
    for bot in sorted(set(pa) | set(pb), key=lambda b: ((pa.get(b) or pb.get(b))["family"], b)):
        x, y = pa.get(bot, zero), pb.get(bot, zero)
        tag = " **(added)**" if bot not in pa else " **(removed)**" if bot not in pb else ""
        fam = x["family"] or y["family"]
        avg = lambda v: "–" if v is None else f"{v:+.2f}"   # noqa: E731
        out.append(f"| `{bot}`{tag} | {fam} | {x['n']} → {y['n']} | {x['days']} → {y['days']} | "
                   f"{_pct(x['win_pct'])} → {_pct(y['win_pct'])} | {avg(x['avg_r'])} → "
                   f"{avg(y['avg_r'])} | {x['r']:+.2f} → {y['r']:+.2f} | {y['r'] - x['r']:+.2f} |")
    out.append(f"| **Whole book** | | {ba['n']} → {bb['n']} | | {_pct(_win(ba))} → "
               f"{_pct(_win(bb))} | | **{ba['r']:+.2f} → {bb['r']:+.2f}** | "
               f"**{bb['r'] - ba['r']:+.2f}** |")
    out += ["", "R: bots use the replay book's realized R (managed exits include the "
            "spread); setups use the harness's net R after $0.02/share.", "",
            "## Days where trades differ", ""]
    for d in cmp["diff_days"][:40]:
        (tk, day) = d["key"]
        out.append(f"- **{day} {tk}**: R {d['r'][0]:+.2f} → {d['r'][1]:+.2f}")
        for label, ts in (("base only", d["base_only"]), ("change only", d["change_only"])):
            for t in ts:
                out.append(f"  - {label}: `{t[0]}` in {t[2]} out {t[3]} entry {t[4]} "
                           f"exit {t[5]} R {t[6]}")
    if len(cmp["diff_days"]) > 40:
        out.append(f"- … {len(cmp['diff_days']) - 40} more days")
    if not cmp["diff_days"]:
        out.append("None.")
    if cmp["signal_only"]:
        out += ["", f"Signal-only differences (no trade changed): {len(cmp['signal_only'])} days."]
    if cmp["errors"]:
        out += ["", "## Errors", ""] + [f"- {k}: base {a} · change {b}" for k, a, b in cmp["errors"]]
    out += ["", "## Not covered", "",
            "- Swing bots (out of scope here; the gate's walk-forward backtest covers them).",
            "- The live quote path (`pipeline._fallback_quote`): replay takes `prev_close` "
            "from the daily frame.",
            "- Live timing (stream ticks, worker interleaving).",
            "- Sample size: ~60 sessions is a signal to review, not proof (see the "
            "statistical discipline in `CLAUDE.md`)."]
    return "\n".join(out) + "\n"


def exit_code(cmp: dict) -> int:
    return 3 if cmp["regressions"] else 1 if cmp["diff_days"] else 0


# --------------------------------------------------------------------------- #
# Driver.
# --------------------------------------------------------------------------- #
def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tickers", nargs="*", help="override the mode's tickers")
    ap.add_argument("--base", help="base ref (required unless --worker)")
    ap.add_argument("--change", help="change ref (default: this working tree)")
    ap.add_argument("--mode", choices=sorted(MODES), default="fast")
    ap.add_argument("--from", dest="start", help="first ET day (default: oldest kept)")
    ap.add_argument("--to", dest="end", help="last ET day (default: yesterday)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--report", help="also write the markdown report here")
    ap.add_argument("--json", dest="json_out", help="also write the comparison as JSON")
    ap.add_argument("--refresh", action="store_true", help="refetch history even if cached today")
    ap.add_argument("--no-cache", action="store_true", help="replay every day again")
    ap.add_argument("--max-days", type=int, help="ticker-days per ticker the bots replay "
                    "(default: fast 20, full 12; gap days first, then the most active)")
    ap.add_argument("--all-days", action="store_true", help="replay the bots on every day")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--load-only", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--tree", help=argparse.SUPPRESS)
    ap.add_argument("--hist", help=argparse.SUPPRESS)
    ap.add_argument("--cache", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.worker:
        worker(Path(a.tree), Path(a.hist), Path(a.out or os.devnull), a.start, a.end,
               a.tickers[0], a.load_only, Path(a.cache) if a.cache else None,
               a.max_days)
        return 0
    if not a.base:
        ap.error("--base is required")

    import pandas as pd                            # driver-only dependency
    root = Path(_git("rev-parse", "--show-toplevel", cwd=Path.cwd())).resolve()
    tickers = [t.upper() for t in (a.tickers or MODES[a.mode])]
    end = a.end or (pd.Timestamp(date.today()) - pd.Timedelta(days=1)).date().isoformat()
    start = a.start
    local = root / ".local" / "replay-ab"
    hist = local / "hist" / date.today().isoformat()
    if a.refresh and hist.exists():
        shutil.rmtree(hist)
    hist.mkdir(parents=True, exist_ok=True)
    cache = None if a.no_cache else local / "cache"
    max_days = None if a.all_days else (a.max_days or MAX_DAYS[a.mode])

    scratch = Path(tempfile.mkdtemp(prefix="replay-ab-"))
    base_tree = scratch / "base"
    change_tree = scratch / "change" if a.change else root
    me = Path(__file__).resolve()
    t0 = time.time()
    try:
        _git("worktree", "add", "--detach", str(base_tree), a.base, cwd=root)
        if a.change:
            _git("worktree", "add", "--detach", str(change_tree), a.change, cwd=root)

        def run(tree: Path, tk: str, out: Path | None, load_only=False) -> None:
            cmd = [sys.executable, "-I", str(me), "--worker", "--tree", str(tree),
                   "--hist", str(hist), tk, "--to", end]
            cmd += ["--from", start] if start else []
            cmd += ["--cache", str(cache)] if cache is not None else []
            cmd += ["--max-days", str(max_days)] if max_days else []
            cmd += ["--load-only"] if load_only else ["--out", str(out)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode:
                raise RuntimeError(f"{tree.name}/{tk}: {proc.stderr[-1500:]}")

        print(f"[replay-ab] loading history for {len(tickers)} tickers …", file=sys.stderr)
        for tk in tickers:                         # sequential: one fetch per ticker
            run(root, tk, None, load_only=True)
        jobs = [(side, tree, tk) for tk in tickers
                for side, tree in (("base", base_tree), ("change", change_tree))]
        print(f"[replay-ab] {len(jobs)} jobs, {a.jobs} at a time …", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            list(pool.map(lambda j: run(j[1], j[2], scratch / f"{j[0]}-{j[2]}.json"), jobs))

        hits, replayed, parity_checked, parity_bad = 0, 0, 0, []

        def collect(side: str) -> dict:
            nonlocal hits, replayed, parity_checked, parity_bad
            rows = {}
            for tk in tickers:
                data = json.loads((scratch / f"{side}-{tk}.json").read_text(encoding="utf-8"))
                hits += data.get("cache_hits", 0)
                for r in data["rows"]:
                    rows[(r["ticker"], r["day"])] = r
                    replayed += bool(r.get("replayed", True)) and side == "change"
                    if side == "change" and "parity" in r:
                        parity_checked += 1
                        parity_bad += [f"{r['ticker']} {r['day']}: {x}" for x in r["parity"]]
            return rows

        cmp_ = compare(collect("base"), collect("change"))
        days = sorted({d for _, d in cmp_["keys"]})
        ctx = {"base": a.base,
               "change": a.change or (_git("branch", "--show-current", cwd=root) or "working tree"),
               "mode": a.mode if not a.tickers else "custom", "tickers": tickers,
               "start": start or (days[0] if days else "?"), "end": days[-1] if days else end,
               "wall": round(time.time() - t0), "cache_hits": hits,
               "parity": (f"{parity_checked} replayed ticker-days, "
                          + (f"{len(parity_bad)} mismatches: " + "; ".join(parity_bad[:10])
                             if parity_bad else "replay book and harness agree")
                          if parity_checked else None),
               "coverage": (f"bots replayed on {replayed} of {cmp_['days']} ticker-days "
                            f"({'every day' if not max_days else f'≤{max_days}/ticker: gap ≥1% days, then the widest ranges'}); "
                            f"setups on all {cmp_['days']}")}
        report = render(cmp_, ctx)
        sys.stdout.buffer.write(report.encode("utf-8"))
        if a.report:
            Path(a.report).write_text(report, encoding="utf-8")
        if a.json_out:
            Path(a.json_out).write_text(json.dumps(
                {"ctx": ctx, "verdict": exit_code(cmp_), "regressions": cmp_["regressions"],
                 "changed_bots": cmp_["changed_bots"], "book": cmp_["book"],
                 "bots": cmp_["bots"], "diff_days": len(cmp_["diff_days"]),
                 "errors": len(cmp_["errors"]), "report": report}, default=str),
                encoding="utf-8")
        return exit_code(cmp_)
    except Exception as exc:                       # surface, don't hide
        print(f"[replay-ab] ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        for tree in (base_tree, change_tree if a.change else None):
            if tree is not None:
                subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=root,
                               capture_output=True)
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

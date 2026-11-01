"""Algorithm-regression gate: does this change touch the trading algorithm?

Compares the working tree (the change) against the merge-base with a base branch
in three layers, so it catches changes that never edit an "algorithm" file:

1. **Static** — which changed files/functions sit on the decision path:
   core decision code (incl. every setup in ``setups/``), its inputs, bot
   execution, or the simulators. A moved file is compared with its old path. Any
   setup or variant added, removed or renamed is an algorithm change by itself.
2. **Behavioral** — ``scripts/algo_probe.py`` runs the real decision stack on
   frozen in-repo data for BOTH trees; any different plan, bot verdict or
   backtest outcome is an algorithm change, whatever file caused it.
3. **Confirmed mode** (``--confirmed-algo-change``, only after the user has
   explicitly confirmed the change is meant to improve the algorithm) — also runs
   the full pytest suite on both trees and fails on any NEW failure, on any
   backtest headline metric getting worse, or on a missing
   ``algolab/LEARNINGS.md`` entry.

Verdicts (exit code): PASS (0) · BLOCKED (1, algorithm change without
confirmation) · FAIL (1, confirmed change that regresses) · ERROR (2).

    python scripts/algo_gate.py [--base origin/dev] [--full-suite]
                                [--confirmed-algo-change] [--report out.md] [--keep-json DIR]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------- #
# What counts as "the algorithm".
# --------------------------------------------------------------------------- #
# Every setup is part of the algorithm. The book's net result depends on which
# setups exist, so adding, removing or renaming a setup or one of its variants is
# an algorithm change, even before a bot trades it (user policy, 2026-10-31). All
# of setups/ is core decision code; only its backtest harness is a simulator.
SETUPS_DIR = "stockanalyzer/setups/"
SETUP_SUPPORT = {"__init__.py", "base.py", "harness.py"}       # in setups/, not setups
LEGACY_SETUPS = ("stockanalyzer/analysis/orb.py", "stockanalyzer/analysis/reversal.py")
CORE = ("stockanalyzer/analysis/", "stockanalyzer/explain/", "stockanalyzer/verdict/",
        "stockanalyzer/sentiment/", "stockanalyzer/strategy.py", SETUPS_DIR)
INPUTS = ("stockanalyzer/data/", "stockanalyzer/pipeline.py", "stockanalyzer/session.py")
EXECUTION = ("app.py", "stockanalyzer/radar_worker.py", "stockanalyzer/virtualbook.py",
             "stockanalyzer/papertrade.py", "stockanalyzer/swingwatch.py",
             "stockanalyzer/manage.py", "stockanalyzer/db/repositories/swingwatch.py")
SIMULATORS = ("stockanalyzer/replay.py", "backtest.py", "daytrade_backtest.py",
              "stockanalyzer/setups/harness.py")

# Functions pinned by tests/test_bot_execution_restore.py plus the other entry
# points that open, manage or mark positions.
SENSITIVE_FUNCS = {
    "_quiet_price", "_radar_panel", "_radar_body", "_portfolio_panel", "_run_bots",
    "_run_gap_bots", "_run_reversal_bots", "_radar_plan", "_radar_tier", "_sidebar",
    "_opening_range", "_live_mid_frame", "_refresh_live_shared", "_quiet_sentiment",
    "run_forever", "process_analysis", "build_radar_plan", "run_automatic_bots",
    "run_reversal_bots", "run_cycle", "open_position", "mark", "manage",
}
# Calls that place, manage, mark or record a trade, or set a bot's cadence.
SENSITIVE_CALLS = re.compile(
    r"open_position|virtualbook\.(mark|manage)|_run_bots|_run_gap_bots|_run_reversal_bots|"
    r"process_analysis|run_automatic_bots|run_reversal_bots|claim_notice|papertrade\.record|"
    r"run_every")
SENSITIVE_CONSTS = re.compile(r"^(_BOTS|SWING_BOTS|GAP_BOTS|_RADAR_TIERS|_RADAR_REFRESH_S|"
                              r"_LIVE_\w+_S|_ENGINE_REFRESH_S|REVERSAL_VARIANTS)$")

# Backtest headline metrics and which direction is "better".
HIGHER_IS_BETTER = {"win_rate", "avg_result", "total_result", "rallies_caught", "chop_passed"}
LOWER_IS_BETTER = {"rallies_missed", "drops_caught_long"}


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def _area(path: str) -> str | None:
    if path in SIMULATORS:              # exact files first: the harness sits in setups/
        return "simulator"
    for name, prefixes in (("core", CORE), ("inputs", INPUTS), ("execution", EXECUTION),
                           ("simulator", SIMULATORS)):
        if any(path == p or path.startswith(p) for p in prefixes):
            return name
    return None


# --------------------------------------------------------------------------- #
# Setup inventory — which setups and variants exist on each side.
# --------------------------------------------------------------------------- #
def _is_setup(path: str) -> bool:
    if path in LEGACY_SETUPS:
        return True
    rest = path[len(SETUPS_DIR):] if path.startswith(SETUPS_DIR) else None
    return bool(rest and rest.endswith(".py") and "/" not in rest
                and rest not in SETUP_SUPPORT)


def _variants(source: str) -> list[str]:
    """Variant names a setup module declares: the first string of each row in any
    top-level ``*VARIANTS`` list (``ReversalConfig("rev-trend-touch", ...)``)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ["<unparseable>"]
    names = []
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        if not any(isinstance(t, ast.Name) and t.id.endswith("VARIANTS") for t in targets):
            continue
        for row in getattr(node.value, "elts", []):
            first = row.args[0] if isinstance(row, ast.Call) and row.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.append(first.value)
    return sorted(names)


def setup_inventory(files: dict[str, str]) -> dict[str, list[str]]:
    """{setup name: [variant names]} from {path: source}. A setup is keyed by its
    module name, so moving it (analysis/orb.py -> setups/orb.py) is not a change."""
    return {Path(p).stem: _variants(src) for p, src in files.items() if _is_setup(p)}


def inventory_diff(base: dict[str, list[str]], change: dict[str, list[str]]) -> list[str]:
    """Plain-English lines for every setup or variant added or removed."""
    out = []
    for name in sorted(set(change) - set(base)):
        rows = f" (variants: {', '.join(change[name])})" if change[name] else ""
        out.append(f"setup added: `{name}`{rows}")
    for name in sorted(set(base) - set(change)):
        out.append(f"setup removed: `{name}`")
    for name in sorted(set(base) & set(change)):
        added = sorted(set(change[name]) - set(base[name]))
        gone = sorted(set(base[name]) - set(change[name]))
        if added:
            out.append(f"`{name}` variants added: {', '.join(added)}")
        if gone:
            out.append(f"`{name}` variants removed: {', '.join(gone)}")
    return out


def setup_changes(root: Path, merge_base: str) -> list[str]:
    """Compare the setup inventory at the merge-base with the working tree."""
    base_paths = git("ls-tree", "-r", "--name-only", merge_base, cwd=root).splitlines()
    base = setup_inventory({p: git("show", f"{merge_base}:{p}", cwd=root)
                            for p in base_paths if _is_setup(p)})
    now_paths = set(git("ls-files", cwd=root).splitlines()) | set(
        git("ls-files", "--others", "--exclude-standard", cwd=root).splitlines())
    change = setup_inventory({p: (root / p).read_text(encoding="utf-8")
                              for p in now_paths if _is_setup(p) and (root / p).exists()})
    return inventory_diff(base, change)


# --------------------------------------------------------------------------- #
# Layer 1 — static.
# --------------------------------------------------------------------------- #
def _defs(source: str | None) -> dict[str, tuple[str, str]]:
    """Top-level name -> (normalized AST dump, source) for functions/classes/assignments."""
    if not source:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"<unparseable>": ("", source)}
    out = {}
    for node in tree.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
        for name in names:
            out[name] = (ast.dump(node, include_attributes=False),
                         ast.get_source_segment(source, node) or "")
    return out


def _changed(root: Path, merge_base: str) -> dict[str, str]:
    """Changed path -> its path at the merge-base. A moved file keeps its old path,
    so it is compared with what it was, not reported as all-new code."""
    out = {}
    for line in git("diff", "--name-status", "-M", merge_base, cwd=root).splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0][:1] in ("R", "C"):
            out[parts[2]] = parts[1]
        elif len(parts) >= 2:
            out[parts[1]] = parts[1]
    return out


def static_scan(root: Path, merge_base: str) -> list[dict]:
    changed = _changed(root, merge_base)
    untracked = git("ls-files", "--others", "--exclude-standard", cwd=root).splitlines()
    changed.update({p: p for p in untracked if p.endswith(".py")})
    findings = []
    for path in sorted(changed):
        was = changed[path]
        area = _area(path) or _area(was)
        if area is None:
            continue
        item = {"path": path, "area": area, "functions": [], "sensitive": []}
        if was != path:
            item["moved_from"] = was
        if path.endswith(".py"):
            try:
                old = git("show", f"{merge_base}:{was}", cwd=root)
            except subprocess.CalledProcessError:
                old = None
            new_file = root / path
            new = new_file.read_text(encoding="utf-8") if new_file.exists() else None
            before, after = _defs(old), _defs(new)
            for name in sorted(set(before) | set(after)):
                if before.get(name, ("",))[0] == after.get(name, ("",))[0]:
                    continue
                state = ("added" if name not in before else
                         "removed" if name not in after else "changed")
                item["functions"].append(f"{name} ({state})")
                body = (before.get(name) or ("", ""))[1] + (after.get(name) or ("", ""))[1]
                if (name in SENSITIVE_FUNCS or SENSITIVE_CONSTS.match(name)
                        or (state == "changed" and SENSITIVE_CALLS.search(body))):
                    item["sensitive"].append(name)
        findings.append(item)
    return findings


# --------------------------------------------------------------------------- #
# Layer 2 — behavioral.
# --------------------------------------------------------------------------- #
def run_probe(probe: Path, tree: Path, out: Path) -> dict:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, str(probe), "--root", str(tree), "--out", str(out)],
                          cwd=tree, env=env, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"probe failed in {tree}:\n{proc.stderr[-2000:]}")
    return json.loads(out.read_text(encoding="utf-8"))


# Decision fields first, so the first few diffs in a report are the ones that matter.
_PRIORITY = ("go", "kind", "verdict", "bots", "swing_bots", "orb", "score", "status", "result",
             "setup", "actionable", "extended")


def _named(rows) -> dict | None:
    """[[name, ...], ...] (checks, reversal variants) -> {name: rest}, else None."""
    if isinstance(rows, list) and rows and all(
            isinstance(r, list) and r and isinstance(r[0], str) for r in rows):
        return {r[0]: r[1:] for r in rows}
    return None


def _field_diff(a, b) -> list[str]:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return [f"{a!r} → {b!r}"]
    keys = sorted(set(a) | set(b), key=lambda k: (k not in _PRIORITY,
                                                   _PRIORITY.index(k) if k in _PRIORITY else 0, k))
    out = []
    for k in keys:
        x, y = a.get(k), b.get(k)
        if x == y:
            continue
        nx, ny = _named(x), _named(y)
        if nx is not None and ny is not None:            # only the entries that moved
            out += [f"{k}[{n}]: {nx.get(n)!r} → {ny.get(n)!r}"
                    for n in sorted(set(nx) | set(ny)) if nx.get(n) != ny.get(n)]
        else:
            out.append(f"{k}: {x!r} → {y!r}")
    return out


def _flips(a: dict, b: dict) -> dict[str, int]:
    """How many outputs changed an actual DECISION, not just a score or a level."""
    out: dict[str, int] = {}
    for key in ("go", "kind", "bots", "by_score", "swing_bots", "orb", "reversals", "verdict",
                "status"):
        n = sum(1 for k in set(a) & set(b) if isinstance(a[k], dict) and isinstance(b[k], dict)
                and key in a[k] and a[k].get(key) != b[k].get(key))
        if n:
            out[key] = n
    missing = len(set(a) ^ set(b)) + sum(1 for k in set(a) & set(b) if (a[k] is None) != (b[k] is None))
    if missing:
        out["appeared/disappeared"] = missing
    return out


def compare(base: dict, head: dict) -> dict:
    out: dict = {"sections": {}, "errors": {}}
    for side, data in (("base", base), ("change", head)):
        for name, err in (data.get("errors") or {}).items():
            out["errors"][f"{side}:{name}"] = err.strip().splitlines()[-1]
    for section in ("plans", "bot_rules", "replay"):
        a, b = base.get(section) or {}, head.get(section) or {}
        diffs = {k: _field_diff(a.get(k), b.get(k)) for k in sorted(set(a) | set(b))
                 if a.get(k) != b.get(k)}
        out["sections"][section] = {"total": len(set(a) | set(b)), "changed": diffs,
                                    "flips": _flips(a, b)}
    a = (base.get("backtest") or {}).get("rows", {})
    b = (head.get("backtest") or {}).get("rows", {})
    diffs = {k: _field_diff(a.get(k), b.get(k)) for k in sorted(set(a) | set(b))
             if a.get(k) != b.get(k)}
    out["sections"]["backtest"] = {"total": len(set(a) | set(b)), "changed": diffs,
                                   "flips": _flips(a, b)}
    out["summary"] = {"base": (base.get("backtest") or {}).get("summary"),
                      "change": (head.get("backtest") or {}).get("summary")}
    return out


def metric_regressions(summary: dict) -> list[str]:
    base, head = summary.get("base") or {}, summary.get("change") or {}
    worse = []
    for key in sorted(HIGHER_IS_BETTER | LOWER_IS_BETTER):
        x, y = base.get(key), head.get(key)
        if x is None or y is None:
            if x != y:
                worse.append(f"{key}: {x} → {y} (missing on one side)")
            continue
        if (key in HIGHER_IS_BETTER and y < x) or (key in LOWER_IS_BETTER and y > x):
            worse.append(f"{key}: {x} → {y}")
    return worse


# --------------------------------------------------------------------------- #
# Layer 3 — full test suite on both trees (confirmed mode).
# --------------------------------------------------------------------------- #
def run_pytest(tree: Path, scratch: Path) -> dict:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
           "STOCKANALYZER_DB": f"sqlite:///{(scratch / (tree.name + '-test.db')).as_posix()}"}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rf"],
                          cwd=tree, env=env, capture_output=True, text=True, encoding="utf-8")
    failed = sorted(set(re.findall(r"^FAILED (\S+)", proc.stdout, re.M)))
    tail = (proc.stdout.strip().splitlines() or [""])[-1]
    return {"failed": failed, "tail": tail, "code": proc.returncode}


# --------------------------------------------------------------------------- #
# Report.
# --------------------------------------------------------------------------- #
def _bullets(items, limit: int = 25) -> list[str]:
    items = list(items)
    lines = [f"- {x}" for x in items[:limit]]
    if len(items) > limit:
        lines.append(f"- … {len(items) - limit} more")
    return lines


def render(ctx: dict) -> str:
    L = [f"# Algo gate: **{ctx['verdict']}**", "",
         f"Base `{ctx['base']}` (merge-base `{ctx['merge_base'][:8]}`) vs the working tree "
         f"of `{ctx['branch']}`. Mode: "
         + ("**confirmed algorithm change**" if ctx["confirmed"] else "detection") + ".", ""]
    L += [f"> {r}" for r in ctx["reasons"]] + [""]

    L += ["## 1. Static: files on the decision path", ""]
    if not ctx["static"]:
        L.append("No changed file is on the decision path.")
    for f in ctx["static"]:
        flag = " ⚠️ **sensitive: " + ", ".join(f["sensitive"]) + "**" if f["sensitive"] else ""
        fns = ", ".join(f["functions"]) or "(no top-level code change)"
        moved = f" (moved from `{f['moved_from']}`)" if f.get("moved_from") else ""
        L.append(f"- `{f['path']}`{moved} [{f['area']}]: {fns}{flag}")
    L.append("")
    L += ["### Setups added or removed", ""]
    L += _bullets(ctx.get("setups") or []) or ["None: the same setups and variants on both sides."]
    L.append("")

    cmp_ = ctx["compare"]
    L += ["## 2. Behavioral: base vs change on frozen data", "",
          "| Probe | Compared | Changed | Decision flips |", "|---|---|---|---|"]
    labels = {"plans": "Swing plans + swing bots (20 tickers × 5 points × 2 paces)",
              "bot_rules": "Live `_BOTS` rule table (every kind/go/actionable/extended × score 0–100)",
              "replay": "Replay intraday bars (Gap-and-Go, reversals, swing)",
              "backtest": "Walk-forward backtest points (`backtest.py`)"}
    for name, sec in cmp_["sections"].items():
        flips = ", ".join(f"{k} {v}" for k, v in sec["flips"].items()) or "none"
        L.append(f"| {labels[name]} | {sec['total']} | {len(sec['changed'])} | {flips} |")
    L.append("")
    if cmp_["errors"]:
        L += ["Probe errors (counted as differences):", ""]
        L += _bullets(f"`{k}`: {v}" for k, v in cmp_["errors"].items()) + [""]
    for name, sec in cmp_["sections"].items():
        if sec["changed"]:
            L += [f"<details><summary>{name}: {len(sec['changed'])} changed</summary>", ""]
            L += _bullets(f"`{k}`: " + "; ".join(v[:4]) for k, v in sec["changed"].items())
            L += ["", "</details>", ""]

    s = cmp_["summary"]
    if s.get("base") or s.get("change"):
        L += ["### Backtest headline", "", "| Metric | Base | Change |", "|---|---|---|"]
        for k in (s.get("base") or s.get("change")):
            L.append(f"| {k} | {(s.get('base') or {}).get(k)} | {(s.get('change') or {}).get(k)} |")
        L.append("")

    if ctx.get("pytest"):
        pb, ph = ctx["pytest"]["base"], ctx["pytest"]["change"]
        L += ["## 3. Full test suite: base vs change", "",
              f"- Base: {pb['tail']}", f"- Change: {ph['tail']}"]
        new = sorted(set(ph["failed"]) - set(pb["failed"]))
        L += ["- New failures:"] + (_bullets(f"`{t}`" for t in new) if new else ["  none"])
        pre = sorted(set(ph["failed"]) & set(pb["failed"]))
        if pre:
            L.append(f"- Already failing on base (ignored): {', '.join(f'`{t}`' for t in pre)}")
        L.append("")

    if ctx.get("history"):
        body = ctx["history"]["report"].replace("\n## ", "\n#### ").replace("# Replay A/B:", "Verdict:", 1)
        L += ["## 4. Real-history A/B: non-swing bots and setups", "", body, ""]

    L += ["## Not covered", "",
          "- Network backtests (`daytrade_backtest.py`, `backtest.py` live fetch): their "
          "offline equivalents above run on frozen data, so base and change see identical inputs.",
          "- Timing-dependent live behavior (stream ticks, worker interleaving): flagged "
          "statically through the sensitive execution functions, not simulated.",
          "- Sample size: ~200 frozen points. A metric move is a signal to review, not proof "
          "(see the statistical discipline in `CLAUDE.md`)."]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# Layer 4 — real-history A/B (non-swing bots + every setup).
# --------------------------------------------------------------------------- #
def run_history(root: Path, merge_base: str, mode: str, scratch: Path) -> dict:
    """Run ``algo_replay_ab.py`` (base = merge-base, change = this working tree)."""
    out = scratch / "history.json"
    script = Path(__file__).resolve().with_name("algo_replay_ab.py")
    proc = subprocess.run([sys.executable, str(script), "--base", merge_base, "--mode", mode,
                           "--json", str(out)], cwd=root, capture_output=True, text=True,
                          encoding="utf-8")
    if proc.returncode == 2 or not out.exists():
        raise RuntimeError(f"real-history A/B could not run:\n{proc.stderr[-2000:]}")
    return json.loads(out.read_text(encoding="utf-8"))


def history_moves(history: dict) -> list[str]:
    """Bots/setups that exist on the base and whose real-history trades changed."""
    base_bots = (history.get("bots") or [{}])[0]
    return sorted(b for b in history.get("changed_bots") or [] if b in base_bots)


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=None, help="base ref (default: origin/dev, else origin/master)")
    ap.add_argument("--confirmed-algo-change", action="store_true",
                    help="ONLY after the user explicitly confirmed this change is meant to "
                         "improve the algorithm: run the full no-regression verification")
    ap.add_argument("--full-suite", action="store_true",
                    help="also run the full pytest suite on base and change (implied by "
                         "--confirmed-algo-change; does NOT confirm anything)")
    ap.add_argument("--report", help="also write the markdown report here")
    ap.add_argument("--keep-json", help="directory to keep both probe JSON files")
    ap.add_argument("--history", choices=("fast", "full"),
                    help="also replay real history for the non-swing bots and every setup "
                         "(scripts/algo_replay_ab.py); confirmed mode runs `fast` by default")
    args = ap.parse_args()
    history_mode = args.history or ("fast" if args.confirmed_algo_change else None)

    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    base = args.base
    if base is None:
        refs = git("branch", "-r", "--format=%(refname:short)", cwd=root).split()
        base = "origin/dev" if "origin/dev" in refs else "origin/master"
    merge_base = git("merge-base", base, "HEAD", cwd=root)
    probe = Path(__file__).resolve().with_name("algo_probe.py")

    scratch = Path(tempfile.mkdtemp(prefix="algo-gate-"))
    base_tree = scratch / "base"
    git("worktree", "add", "--detach", str(base_tree), merge_base, cwd=root)
    try:
        print(f"[algo-gate] static scan vs {base} ({merge_base[:8]})", file=sys.stderr)
        static = static_scan(root, merge_base)
        print("[algo-gate] probing base …", file=sys.stderr)
        base_fp = run_probe(probe, base_tree, scratch / "base.json")
        print("[algo-gate] probing change …", file=sys.stderr)
        head_fp = run_probe(probe, root, scratch / "change.json")
        cmp_ = compare(base_fp, head_fp)
        if args.keep_json:
            keep = Path(args.keep_json)
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy(scratch / "base.json", keep / "probe_base.json")
            shutil.copy(scratch / "change.json", keep / "probe_change.json")

        behavior_changed = bool(cmp_["errors"]) or any(
            sec["changed"] for sec in cmp_["sections"].values())
        direct = [f for f in static if f["area"] in ("core", "inputs") and f["functions"]]
        sensitive = [f for f in static if f["sensitive"]]
        setups = setup_changes(root, merge_base)
        algo_change = behavior_changed or bool(direct) or bool(sensitive) or bool(setups)

        reasons: list[str] = []
        if setups:
            reasons.append("The set of setups changed (adding, removing or renaming a setup "
                           "or variant is an algorithm change): " + "; ".join(setups) + ".")
        if direct:
            reasons.append("Direct edits to decision code or its inputs: "
                           + ", ".join(f"`{f['path']}`" for f in direct) + ".")
        if sensitive:
            reasons.append("Bot-execution functions changed: " + ", ".join(
                f"`{f['path']}`: {', '.join(f['sensitive'])}" for f in sensitive) + ".")
        if behavior_changed:
            n = sum(len(s["changed"]) for s in cmp_["sections"].values())
            reasons.append(f"Behavior differs from base on frozen data ({n} outputs changed"
                           + (", probe errors" if cmp_["errors"] else "") + ").")

        history = None
        if history_mode:
            print(f"[algo-gate] real-history A/B ({history_mode}) …", file=sys.stderr)
            history = run_history(root, merge_base, history_mode, scratch)
            moved = history_moves(history)
            if moved:
                algo_change = True
                reasons.append("Real-history trades differ for existing bots/setups: "
                               + ", ".join(f"`{b}`" for b in moved) + ".")

        ctx = {"base": base, "merge_base": merge_base, "branch": git("branch", "--show-current", cwd=root)
               or "detached", "confirmed": args.confirmed_algo_change, "static": static,
               "compare": cmp_, "reasons": reasons, "setups": setups, "history": history}

        new_fail: list[str] = []
        if args.full_suite or args.confirmed_algo_change:
            print("[algo-gate] full test suite on base and change …", file=sys.stderr)
            ctx["pytest"] = {"base": run_pytest(base_tree, scratch),
                             "change": run_pytest(root, scratch)}
            new_fail = sorted(set(ctx["pytest"]["change"]["failed"])
                              - set(ctx["pytest"]["base"]["failed"]))

        if not args.confirmed_algo_change:
            if algo_change:
                ctx["verdict"] = "BLOCKED: algorithm change detected"
                reasons.append("This needs the user's explicit confirmation that the change is "
                               "meant to alter the algorithm. Then rerun with "
                               "`--confirmed-algo-change` for the full no-regression check.")
            elif new_fail:
                ctx["verdict"] = "FAIL: new test failures"
                reasons.append(f"No algorithm change, but {len(new_fail)} test(s) fail that pass "
                               "on base.")
            else:
                ctx["verdict"] = "PASS: no algorithm change" + (
                    " (full suite verified)" if "pytest" in ctx else "")
                reasons.append("No decision-path edit, no sensitive execution change, and "
                               "identical decisions, bot verdicts and backtest outcomes"
                               + (", and no new test failures vs base." if "pytest" in ctx else "."))
        else:
            worse = metric_regressions(cmp_["summary"])
            learn = "algolab/LEARNINGS.md" in git("diff", "--name-only", merge_base, cwd=root).split()
            problems = []
            if new_fail:
                problems.append(f"{len(new_fail)} new test failure(s).")
            if worse:
                problems.append("Backtest metrics got worse: " + "; ".join(worse) + ".")
            if algo_change and not learn:
                problems.append("No dated `algolab/LEARNINGS.md` entry (required by CLAUDE.md).")
            if cmp_["errors"]:
                problems.append("The probe errored on one side; behavior could not be compared.")
            if history and history["regressions"]:
                problems.append("Real-history regression (net R + win % rule): "
                                + "; ".join(history["regressions"]) + ".")
            if problems:
                ctx["verdict"] = "FAIL: confirmed algorithm change regresses"
                reasons.extend(problems)
            elif not algo_change:
                ctx["verdict"] = "PASS: no algorithm change (full suite verified)"
                reasons.append("No algorithm change was detected, and the full suite has no new "
                               "failures vs base.")
            else:
                ctx["verdict"] = "PASS: confirmed algorithm change, no regression"
                reasons.append("All tests pass (no new failures vs base), no backtest headline "
                               "metric got worse"
                               + (", the real-history A/B shows no regression" if history else "")
                               + ", and LEARNINGS.md is updated.")

        report = render(ctx)
        sys.stdout.buffer.write(report.encode("utf-8"))
        if args.report:
            Path(args.report).write_text(report, encoding="utf-8")
        return 0 if ctx["verdict"].startswith("PASS") else 1
    except Exception as exc:                                  # surface, don't hide
        print(f"[algo-gate] ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(base_tree)], cwd=root,
                       capture_output=True)
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

"""Algorithm-regression gate: does this change touch the trading algorithm?

Compares the working tree (the change) against the merge-base with a base branch
in three layers, so it catches changes that never edit an "algorithm" file:

1. **Static** — which changed files/functions sit on the decision path:
   core decision code, its inputs, bot execution, or the simulators.
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
CORE = ("stockanalyzer/analysis/", "stockanalyzer/explain/", "stockanalyzer/verdict/",
        "stockanalyzer/sentiment/", "stockanalyzer/strategy.py")
INPUTS = ("stockanalyzer/data/", "stockanalyzer/pipeline.py", "stockanalyzer/session.py")
EXECUTION = ("app.py", "stockanalyzer/radar_worker.py", "stockanalyzer/virtualbook.py",
             "stockanalyzer/papertrade.py", "stockanalyzer/swingwatch.py",
             "stockanalyzer/manage.py", "stockanalyzer/db/repositories/swingwatch.py")
SIMULATORS = ("stockanalyzer/replay.py", "backtest.py", "daytrade_backtest.py")

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
    for name, prefixes in (("core", CORE), ("inputs", INPUTS), ("execution", EXECUTION),
                           ("simulator", SIMULATORS)):
        if any(path == p or path.startswith(p) for p in prefixes):
            return name
    return None


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


def static_scan(root: Path, merge_base: str) -> list[dict]:
    changed = [p for p in git("diff", "--name-only", merge_base, cwd=root).splitlines() if p]
    untracked = git("ls-files", "--others", "--exclude-standard", cwd=root).splitlines()
    findings = []
    for path in sorted(set(changed) | {p for p in untracked if p.endswith(".py")}):
        area = _area(path)
        if area is None:
            continue
        item = {"path": path, "area": area, "functions": [], "sensitive": []}
        if path.endswith(".py"):
            try:
                old = git("show", f"{merge_base}:{path}", cwd=root)
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
        L.append(f"- `{f['path']}` [{f['area']}]: {fns}{flag}")
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

    L += ["## Not covered", "",
          "- Network backtests (`daytrade_backtest.py`, `backtest.py` live fetch): their "
          "offline equivalents above run on frozen data, so base and change see identical inputs.",
          "- Timing-dependent live behavior (stream ticks, worker interleaving): flagged "
          "statically through the sensitive execution functions, not simulated.",
          "- Sample size: ~200 frozen points. A metric move is a signal to review, not proof "
          "(see the statistical discipline in `CLAUDE.md`)."]
    return "\n".join(L) + "\n"


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
    args = ap.parse_args()

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
        algo_change = behavior_changed or bool(direct) or bool(sensitive)

        reasons: list[str] = []
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

        ctx = {"base": base, "merge_base": merge_base, "branch": git("branch", "--show-current", cwd=root)
               or "detached", "confirmed": args.confirmed_algo_change, "static": static,
               "compare": cmp_, "reasons": reasons}

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
                               "metric got worse, and LEARNINGS.md is updated.")

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

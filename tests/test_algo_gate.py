"""The algorithm-regression gate's own logic (scripts/algo_gate.py, algo_probe.py).

The end-to-end run needs two source trees and ~2 minutes, so it is exercised by
running the gate itself; these pin the pure pieces it decides with.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import algo_gate as gate
from scripts import algo_probe as probe

ROOT = Path(__file__).resolve().parents[1]


def test_paths_are_classified_by_their_role_in_a_decision():
    assert gate._area("stockanalyzer/explain/swing.py") == "core"
    assert gate._area("stockanalyzer/analysis/orb.py") == "core"
    assert gate._area("stockanalyzer/setups/orb.py") == "core"          # every setup,
    assert gate._area("stockanalyzer/setups/double_bottom.py") == "core"  # traded or not
    assert gate._area("stockanalyzer/setups/base.py") == "core"
    assert gate._area("stockanalyzer/setups/harness.py") == "simulator"
    assert gate._area("stockanalyzer/pipeline.py") == "inputs"
    assert gate._area("stockanalyzer/data/providers.py") == "inputs"
    assert gate._area("app.py") == "execution"
    assert gate._area("stockanalyzer/radar_worker.py") == "execution"
    assert gate._area("stockanalyzer/replay.py") == "simulator"
    assert gate._area("docs/local-setup.md") is None
    assert gate._area("stockanalyzer/ui/history.py") is None


def _repo(tmp_path: Path) -> tuple[Path, str]:
    """A throwaway git repo with one core file committed; returns (root, base sha)."""
    def run(*args):
        return gate.git("-c", "user.name=t", "-c", "user.email=t@t", *args, cwd=tmp_path)
    run("init", "-q")
    src = tmp_path / "stockanalyzer" / "analysis" / "orb.py"
    src.parent.mkdir(parents=True)
    helpers = "".join(f"\n\ndef helper_{i}(p):\n    return p * {i} + {i}\n" for i in range(12))
    src.write_text("from .daycard import x\n\nGAP = 0.02\n\ndef signal(p):\n    return p > GAP\n"
                   + helpers, encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    return tmp_path, run("rev-parse", "HEAD")


def test_a_moved_decision_file_is_compared_with_its_old_path(tmp_path):
    root, base = _repo(tmp_path)
    new = root / "stockanalyzer" / "setups" / "orb.py"
    new.parent.mkdir(parents=True)
    old = root / "stockanalyzer" / "analysis" / "orb.py"
    new.write_text(old.read_text(encoding="utf-8").replace(
        "from .daycard", "from ..analysis.daycard"), encoding="utf-8")
    old.unlink()
    gate.git("add", "-A", cwd=root)
    [item] = gate.static_scan(root, base)
    assert item["path"] == "stockanalyzer/setups/orb.py"
    assert item["moved_from"] == "stockanalyzer/analysis/orb.py"
    assert item["area"] == "core" and item["functions"] == []   # imports only: no change

    new.write_text(new.read_text(encoding="utf-8").replace("0.02", "0.03"), encoding="utf-8")
    [item] = gate.static_scan(root, base)
    assert item["functions"] == ["GAP (changed)"]                 # a move can't hide an edit

    new.write_text("def signal(p):\n    return True\n", encoding="utf-8")   # a rewrite:
    items = {i["path"]: i for i in gate.static_scan(root, base)}          # delete + add
    assert "signal (removed)" in items["stockanalyzer/analysis/orb.py"]["functions"]
    assert "signal (added)" in items["stockanalyzer/setups/orb.py"]["functions"]


_REVERSAL = ('REVERSAL_VARIANTS: list[C] = [\n    C("rev-trend-touch", "with_trend", "touch"),\n'
             '    C("rev-strong-touch", "strong_support", "touch"),\n]\n')


def test_variants_are_read_from_every_variants_list():
    assert gate._variants(_REVERSAL) == ["rev-strong-touch", "rev-trend-touch"]
    assert gate._variants('VARIANTS = [Cfg("sb-am-bias")]\n') == ["sb-am-bias"]
    assert gate._variants("GAP_MIN_PCT = 0.02\n") == []                 # orb: no variants


def test_adding_or_removing_a_setup_or_variant_is_reported_but_a_move_is_not():
    base = gate.setup_inventory({"stockanalyzer/analysis/orb.py": "X = 1\n",
                                 "stockanalyzer/analysis/reversal.py": _REVERSAL})
    moved = gate.setup_inventory({"stockanalyzer/setups/orb.py": "X = 1\n",
                                  "stockanalyzer/setups/reversal.py": _REVERSAL,
                                  "stockanalyzer/setups/base.py": "MIN_RR = 1.0\n",
                                  "stockanalyzer/setups/harness.py": "",
                                  "stockanalyzer/setups/__init__.py": ""})
    assert gate.inventory_diff(base, moved) == []                      # support files aren't setups

    grown = {**moved, "silver_bullet": ["sb-am-bias"], "reversal": ["rev-trend-touch"]}
    assert gate.inventory_diff(base, grown) == [
        "setup added: `silver_bullet` (variants: sb-am-bias)",
        "`reversal` variants removed: rev-strong-touch"]
    assert gate.inventory_diff(base, {"reversal": base["reversal"]}) == ["setup removed: `orb`"]


def test_setup_changes_compares_the_merge_base_with_the_working_tree(tmp_path):
    root, base = _repo(tmp_path)
    (root / "stockanalyzer" / "setups").mkdir(parents=True)
    gate.git("mv", "stockanalyzer/analysis/orb.py", "stockanalyzer/setups/orb.py", cwd=root)
    assert gate.setup_changes(root, base) == []                        # a move adds nothing
    (root / "stockanalyzer" / "setups" / "double_bottom.py").write_text(
        'VARIANTS = [Cfg("xfade-dbl-bottom")]\n', encoding="utf-8")     # untracked new file
    assert gate.setup_changes(root, base) == [
        "setup added: `double_bottom` (variants: xfade-dbl-bottom)"]


def test_defs_see_function_and_constant_changes_but_not_formatting():
    old = "_BOTS = (('a', 1),)\n\ndef f(x):\n    return x + 1\n"
    assert gate._defs(old)["f"][0] == gate._defs("def f(x):  return x+1\n")["f"][0]
    new = old.replace("x + 1", "x + 2").replace("1),)", "2),)")
    before, after = gate._defs(old), gate._defs(new)
    assert before["f"][0] != after["f"][0]
    assert before["_BOTS"][0] != after["_BOTS"][0]
    assert gate.SENSITIVE_CONSTS.match("_BOTS") and gate.SENSITIVE_CONSTS.match("_LIVE_MID_S")


def test_diff_lists_only_the_checks_that_moved_decision_fields_first():
    a = {"score": 70, "go": False, "checks": [["RSI", True, False, 4], ["ADX", True, False, 5]]}
    b = {"score": 72, "go": True, "checks": [["RSI", True, False, 8], ["ADX", True, False, 5]]}
    diff = gate._field_diff(a, b)
    assert diff[0] == "go: False → True"
    assert "checks[RSI]: [True, False, 4] → [True, False, 8]" in diff
    assert not any("ADX" in line for line in diff)


def test_flips_count_decisions_not_score_drift():
    a = {"x": {"go": False, "kind": "immediate", "score": 60, "bots": []},
         "y": {"go": True, "kind": "immediate", "score": 80, "bots": ["bot-GO"]}}
    b = {"x": {"go": False, "kind": "immediate", "score": 63, "bots": []},
         "y": {"go": False, "kind": "immediate", "score": 68, "bots": []}}
    assert gate._flips(a, b) == {"go": 1, "bots": 1}


def test_metric_regressions_respect_each_metrics_direction():
    base = {"win_rate": 75.0, "avg_result": 1.9, "rallies_missed": 52, "drops_caught_long": 2}
    better = {**base, "win_rate": 80.0, "rallies_missed": 40}
    worse = {**base, "avg_result": 1.5, "drops_caught_long": 3}
    assert gate.metric_regressions({"base": base, "change": better}) == []
    assert gate.metric_regressions({"base": base, "change": worse}) == [
        "avg_result: 1.9 → 1.5", "drops_caught_long: 2 → 3"]


def test_probe_reads_the_live_swing_bot_rules_from_app_py():
    bots = dict(probe._live_bots(ROOT))
    assert set(bots) == {"bot-GO", "bot-70", "bot-BRK"}
    plan = SimpleNamespace(go=False, score=72, kind="immediate", actionable=True, emerging=None)
    assert bots["bot-70"](plan) and not bots["bot-GO"](plan) and not bots["bot-BRK"](plan)


def test_rule_table_covers_every_state_and_shows_each_threshold():
    probe._LIVE_BOTS = probe._live_bots(ROOT)
    table = probe.probe_bot_rules()
    assert len(table) == 3 * 2 * 2 * 2                    # kind × go × actionable × extended
    assert table["immediate|go=False|actionable=True|extended=False"]["by_score"] == [
        "0-69:-", "70-100:bot-70"]
    assert table["breakout_wait|go=False|actionable=True|extended=False"]["by_score"] == [
        "0-54:-", "55-100:bot-BRK"]
    # The watch-only chokepoint: nothing fires on a non-actionable or extended plan.
    assert table["immediate|go=True|actionable=False|extended=False"]["by_score"] == ["0-100:-"]
    assert table["immediate|go=True|actionable=True|extended=True"]["by_score"] == ["0-100:-"]

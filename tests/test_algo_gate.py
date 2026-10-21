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
    assert gate._area("stockanalyzer/pipeline.py") == "inputs"
    assert gate._area("stockanalyzer/data/providers.py") == "inputs"
    assert gate._area("app.py") == "execution"
    assert gate._area("stockanalyzer/radar_worker.py") == "execution"
    assert gate._area("stockanalyzer/replay.py") == "simulator"
    assert gate._area("docs/local-setup.md") is None
    assert gate._area("stockanalyzer/ui/history.py") is None


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

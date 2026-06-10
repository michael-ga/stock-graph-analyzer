"""Swing radar: tracked-ticker persistence + the 60/70/80 escalation ladder."""
from __future__ import annotations

from stockanalyzer import swingwatch


def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "radar.json"
    assert swingwatch.load(p) == []
    swingwatch.add("nok", p)
    swingwatch.add("INTC", p)
    swingwatch.add("nok", p)                       # no duplicates
    assert swingwatch.load(p) == ["NOK", "INTC"]
    assert swingwatch.is_tracked("intc", p)
    swingwatch.remove("NOK", p)
    assert swingwatch.load(p) == ["INTC"]


def test_notice_level_rungs():
    assert swingwatch.notice_level(0) == 0
    assert swingwatch.notice_level(59) == 0
    assert swingwatch.notice_level(60) == 60
    assert swingwatch.notice_level(69) == 60
    assert swingwatch.notice_level(70) == 70
    assert swingwatch.notice_level(85) == 80


def test_escalation_fires_once_per_level():
    # Climb onto the first rung → 1st notice.
    fired = swingwatch.new_notice(0, 62)
    assert fired and fired[0] == 60 and "1st" in fired[1]
    # Same rung again → silent.
    assert swingwatch.new_notice(60, 65) is None
    # Climb to 70 → 2nd notice; then 80 → 3rd.
    assert swingwatch.new_notice(60, 71)[0] == 70
    assert swingwatch.new_notice(70, 83)[0] == 80
    # Jumping straight to 80 from nothing fires the top rung.
    assert "3rd" in swingwatch.new_notice(0, 82)[1]


def test_drop_rearms_the_rung():
    # Notified at 70, score falls to 55 → stored level falls (caller stores
    # notice_level(55)=0) → climbing back over 60 must fire again.
    assert swingwatch.notice_level(55) == 0
    fired = swingwatch.new_notice(0, 64)
    assert fired and fired[0] == 60

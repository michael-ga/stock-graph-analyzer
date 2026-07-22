"""Wall-clock market-session helpers (ET phases, ORB window, Friday flatten)."""
from __future__ import annotations

from datetime import datetime

from stockanalyzer import session

# 2026-06-19 is a Friday; 06-20 Sat, 06-21 Sun (naive datetimes == Eastern).
_FRI = lambda h, m: datetime(2026, 6, 19, h, m)      # noqa: E731


def test_market_phase_boundaries():
    assert session.market_phase(_FRI(9, 29)) == "premarket"
    assert session.market_phase(_FRI(9, 30)) == "opening_range"
    assert session.market_phase(_FRI(9, 44)) == "opening_range"
    assert session.market_phase(_FRI(9, 45)) == "regular"
    assert session.market_phase(_FRI(15, 59)) == "regular"
    assert session.market_phase(_FRI(16, 0)) == "closed"
    assert session.market_phase(_FRI(3, 0)) == "closed"


def test_weekend_is_closed():
    assert session.market_phase(datetime(2026, 6, 20, 10, 0)) == "closed"   # Sat
    assert session.market_phase(datetime(2026, 6, 21, 10, 0)) == "closed"   # Sun


def test_orb_window():
    assert not session.is_orb_window(_FRI(9, 44))
    assert session.is_orb_window(_FRI(9, 45))
    assert session.is_orb_window(_FRI(11, 30))
    assert not session.is_orb_window(_FRI(11, 31))
    assert not session.is_orb_window(datetime(2026, 6, 20, 10, 0))          # weekend


def test_is_friday_flat():
    assert not session.is_friday_flat(_FRI(15, 44))
    assert session.is_friday_flat(_FRI(15, 45))
    assert session.is_friday_flat(_FRI(15, 59))
    assert not session.is_friday_flat(_FRI(16, 0))                          # closed
    assert not session.is_friday_flat(datetime(2026, 6, 18, 15, 50))        # Thursday

"""Adaptive radar tiering (_radar_tier) + RVOL helper — the Gap-and-Go gating."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pandas as pd

from app import _radar_tier
from stockanalyzer.analysis.indicators import relative_volume


def _plan(go=False, kind="immediate", trigger=None, score=50, light="no", atr=3.0):
    return NS(go=go, kind=kind, trigger=trigger, score=score, light=light,
              daily_atr_pct=atr)


def test_open_position_is_always_hot():
    assert _radar_tier(_plan(), 100.0, True, "regular", None, True) == "HOT"
    assert _radar_tier(_plan(), 100.0, True, "premarket", None, False) == "HOT"


def test_opening_range_is_paused():
    assert _radar_tier(_plan(go=True), 100.0, False, "opening_range", None, False) == "PAUSED"


def test_premarket_and_closed_are_far():
    assert _radar_tier(_plan(), 100.0, False, "premarket", None, False) == "FAR"
    assert _radar_tier(_plan(), 100.0, False, "closed", None, False) == "FAR"


def test_gap_up_escalates_toward_or_high():
    org = {"gap_up": True, "high": 101.0, "low": 99.0}
    # coiled just under the OR-high (1% away, band ~1.8%) → WATCH_CLOSE
    assert _radar_tier(_plan(), 100.0, False, "regular", org, True) == "WATCH_CLOSE"
    # broke the OR-high → HOT
    assert _radar_tier(_plan(), 101.0, False, "regular", org, True) == "HOT"
    # far below the range → FAR (save CPU)
    assert _radar_tier(_plan(), 90.0, False, "regular", org, True) == "FAR"


def test_non_gapper_falls_back_to_swing_tiering():
    org = {"gap_up": False, "high": 101.0, "low": 99.0}
    assert _radar_tier(_plan(go=True), 100.0, False, "regular", org, True) == "HOT"
    assert _radar_tier(_plan(score=68), 100.0, False, "regular", org, True) == "WATCH_CLOSE"
    assert _radar_tier(_plan(light="forming"), 100.0, False, "regular", org, True) == "BUILDUP"


def test_after_window_ignores_orb_uses_swing():
    org = {"gap_up": True, "high": 101.0, "low": 99.0}
    # gap-up but orb_window False (after 11:30) → swing fallback, not ORB
    assert _radar_tier(_plan(light="forming"), 100.0, False, "regular", org, False) == "BUILDUP"


def test_relative_volume():
    assert abs(relative_volume(pd.DataFrame({"volume": [100.0] * 25})) - 1.0) < 1e-9
    assert relative_volume(pd.DataFrame({"volume": [100.0] * 24 + [400.0]})) > 1.5
    assert relative_volume(pd.DataFrame({"volume": [1, 2, 3]})) is None     # too short
    assert relative_volume(pd.DataFrame({"close": [1] * 25})) is None       # no volume

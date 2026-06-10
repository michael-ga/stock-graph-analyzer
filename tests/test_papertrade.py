"""Paper-trade journal: recording, conservative outcome judging, report card."""
from __future__ import annotations

import time

from stockanalyzer.papertrade import (
    judge_outcome,
    load,
    recent_duplicate,
    record,
    summarize,
)


# --- judge_outcome (pure) ----------------------------------------------------
def test_target_hit():
    bars = [(101.0, 99.5, 100.5), (104.2, 100.0, 104.0)]   # 2nd bar tags the target
    status, res = judge_outcome(bars, entry=100.0, stop=98.0, target=104.0)
    assert status == "target_hit" and res == 4.0


def test_stop_hit():
    bars = [(100.5, 97.5, 98.0)]
    status, res = judge_outcome(bars, entry=100.0, stop=98.0, target=104.0)
    assert status == "stop_hit" and res == -2.0


def test_same_bar_touch_counts_stop_first():
    bars = [(105.0, 97.0, 101.0)]                          # touched both — conservative
    status, res = judge_outcome(bars, entry=100.0, stop=98.0, target=104.0)
    assert status == "stop_hit"


def test_expired_marks_to_market():
    bars = [(101.0, 99.0, 100.5)] * 3                      # 3 quiet days = horizon
    status, res = judge_outcome(bars, entry=100.0, stop=98.0, target=104.0,
                                horizon_days=3)
    assert status == "expired" and res == 0.5


def test_open_when_not_enough_bars():
    bars = [(101.0, 99.0, 100.8)]
    status, res = judge_outcome(bars, entry=100.0, stop=98.0, target=104.0)
    assert status == "open" and res == 0.8


def test_breakout_not_triggered():
    bars = [(101.0, 99.0, 100.0)] * 3                      # never crosses 102
    status, res = judge_outcome(bars, entry=102.1, stop=100.0, target=106.0,
                                trigger=102.0, horizon_days=3)
    assert status == "not_triggered" and res == 0.0


def test_breakout_triggered_then_target():
    bars = [(101.0, 99.0, 100.0),       # waiting
            (102.5, 100.5, 102.2),      # trigger crossed (102), no stop/target yet
            (106.5, 102.0, 106.0)]      # target 106 tagged
    status, res = judge_outcome(bars, entry=102.1, stop=100.0, target=106.0,
                                trigger=102.0, horizon_days=3)
    assert status == "target_hit"
    assert res == round((106.0 / 102.1 - 1) * 100, 1)


# --- record / dedup ----------------------------------------------------------
def test_record_and_dedup(tmp_path):
    p = tmp_path / "journal.json"
    now = time.time()
    rec = dict(ts=now, ticker="NOK", level=60, entry=13.8, stop=13.2, target=14.5,
               status="open", result_pct=0.0)
    assert record(rec, p) is True
    assert record(dict(rec), p) is False                   # same level within 24h
    rec70 = dict(rec, level=70)
    assert record(rec70, p) is True                        # higher rung records
    assert len(load(p)) == 2
    assert recent_duplicate(load(p), "NOK", 60, now) is True
    assert recent_duplicate(load(p), "NOK", 60, now + 25 * 3600) is False


# --- report card ---------------------------------------------------------------
def test_summarize_per_level():
    recs = [
        dict(ticker="A", level=60, status="target_hit", result_pct=4.0),
        dict(ticker="B", level=60, status="stop_hit", result_pct=-2.0),
        dict(ticker="C", level=70, status="target_hit", result_pct=5.0),
        dict(ticker="D", level=80, status="expired", result_pct=1.5),
        dict(ticker="E", level=80, status="open", result_pct=0.3),
        dict(ticker="F", level=70, status="not_triggered", result_pct=0.0),
    ]
    s = summarize(recs)
    assert s[60]["n"] == 2 and s[60]["win_rate"] == 50
    assert s[70]["wins"] == 1 and s[70]["not_triggered"] == 1
    assert s[70]["win_rate"] == 100                       # not_triggered excluded
    assert s[80]["expired"] == 1 and s[80]["wins"] == 1    # +1.5% expiry = win by sign
    assert s["all"]["n"] == 6
    assert s["all"]["open"] == 1

"""Multi-day replay scan (stockanalyzer.replay_scan).

The scan is only useful if it agrees with the replay it points you to: a day it
marks as traded must be a day the replay's bots trade. So most of these tests pin
that the summary is built from the same book lifecycle (apply_bar → force_flat →
scorecard), plus the cache rules that keep a stale or half-finished day from
being served as fact.
"""
from __future__ import annotations

import pandas as pd
import pytest

from stockanalyzer import replay, replay_scan
from stockanalyzer.analysis import asof
from stockanalyzer.data.schema import Timeframe
from tests.test_replay import _DAY as _SYN_DAY, _history as _syn_history
from tests.test_replay_book import _bar, _session


def _traded_session():
    """Gap-and-Go fires at bar 1; bar 3 runs through the 108 target."""
    return _session([_bar(0), _bar(1, gap_fired=True), _bar(2),
                     _bar(3, high=108.5, close=108.2), _bar(4)])


def _quiet_session():
    return _session([_bar(i) for i in range(5)])


# --------------------------------------------------------------------------- #
# Summarizing one day.
# --------------------------------------------------------------------------- #
def test_a_day_the_bots_trade_is_summarized_as_traded(tmp_path):
    r = replay_scan.summarize(_traded_session(), tmp_path / "scan.db")
    assert r.status == "traded"
    assert r.families == ("orb",)
    assert r.first_signal == "09:50"
    assert {t["trader"] for t in r.trades} == {name for name, _ in replay.GAP_BOTS}
    assert all(t["in"] == "09:50" for t in r.trades)
    assert r.net_r > 0 and r.wins == len(r.trades) and r.losses == 0
    # Two gap bots on one plan are ONE idea — the number to judge the algorithm on.
    assert r.ideas == 1


def test_a_day_nobody_acts_on_is_quiet(tmp_path):
    r = replay_scan.summarize(_quiet_session(), tmp_path / "scan.db")
    assert r.status == "quiet"
    assert not r.trades and not r.signals and r.first_signal is None
    assert r.block_reason == "vol_ok: needs volume"
    assert r.closes == (104.0,) * 5 and r.clocks[0] == "09:45"


def test_signals_are_the_ones_apply_bar_places_on(tmp_path):
    """An extended swing plan is watch-only — it must not count as a signal."""
    sess = _session([_bar(0, go=True, extended=True), _bar(1, score=75)])
    r = replay_scan.summarize(sess, tmp_path / "scan.db")
    assert [(s["t"], s["bot"]) for s in r.signals] == [("09:50", "bot-70")]
    assert r.signal_bars == 1


def test_an_unfinished_session_is_marked_incomplete(tmp_path):
    r = replay_scan.summarize(_quiet_session(), tmp_path / "scan.db")
    assert not r.complete                     # last bar 10:05, not 15:55


def test_the_result_round_trips_through_json(tmp_path):
    r = replay_scan.summarize(_traded_session(), tmp_path / "scan.db")
    assert replay_scan.DayResult.from_json(r.to_json()) == r


def test_change_and_range_are_measured_from_the_session(tmp_path):
    r = replay_scan.summarize(_traded_session(), tmp_path / "scan.db")
    assert r.prev_close == 100.0
    assert r.change_pct == pytest.approx(round((r.close / 100.0 - 1) * 100, 2))
    assert r.range_pct == pytest.approx(round((r.high - r.low) / r.open * 100, 2))


# --------------------------------------------------------------------------- #
# End to end on synthetic history — same precompute the replay page runs.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def syn_hist() -> dict:
    h = _syn_history()
    days = asof.trading_days(h["i5"])
    return {**h, "days": days, "prev_close": {d: h["prev_close"] for d in days}}


def test_scan_day_matches_the_replay_session(syn_hist):
    r = replay_scan.scan_day(syn_hist, "TEST", _SYN_DAY)
    sess = replay.precompute_day(syn_hist["i5"], syn_hist["i30"], syn_hist["daily"],
                                 _SYN_DAY, 100.0, ticker="TEST")
    assert not r.error
    assert r.bars == len(sess.bars)
    assert r.signal_bars == sum(bool(replay_scan.bar_signals(b)) for b in sess.bars)
    assert "orb" in r.families                # the synthetic day's gap break


def test_a_day_outside_the_window_is_an_error_not_a_crash(syn_hist):
    r = replay_scan.scan_day(syn_hist, "TEST", "2026-01-05")
    assert r.status == "error" and "no 5-minute data" in r.error


# --------------------------------------------------------------------------- #
# The cache.
# --------------------------------------------------------------------------- #
def _complete(r):
    from dataclasses import replace
    return replace(r, complete=True)


def test_cache_round_trip_and_engine_invalidation(tmp_path):
    db = tmp_path / ".replay" / "scan-cache.db"
    r = _complete(replay_scan.summarize(_traded_session(), tmp_path / "scan.db"))
    replay_scan.cache_put(db, r, engine="A")
    assert replay_scan.cache_get(db, "test", r.day, engine="A") == r
    assert replay_scan.cache_get(db, "TEST", r.day, engine="B") is None


def test_unfinished_and_failed_days_are_never_cached(tmp_path):
    db = tmp_path / "scan-cache.db"
    partial = replay_scan.summarize(_quiet_session(), tmp_path / "scan.db")
    replay_scan.cache_put(db, partial, engine="A")
    replay_scan.cache_put(db, replay_scan.DayResult("TEST", "2026-06-17", error="x"),
                          engine="A")
    assert replay_scan.cache_get(db, "TEST", partial.day, engine="A") is None
    assert replay_scan.cache_get(db, "TEST", "2026-06-17", engine="A") is None


def test_the_fingerprint_is_stable_and_covers_the_engine():
    fp = replay_scan.engine_fingerprint()
    assert fp == replay_scan.engine_fingerprint() and len(fp) == 16


def test_cache_lives_under_the_ignored_replay_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKANALYZER_REPLAY_DIR", raising=False)
    assert replay_scan.cache_path(tmp_path) == tmp_path / ".replay" / "scan-cache.db"


# --------------------------------------------------------------------------- #
# Running a scan.
# --------------------------------------------------------------------------- #
def test_run_serves_cached_days_and_loads_each_ticker_once(tmp_path, monkeypatch):
    db = tmp_path / "scan-cache.db"
    days = [pd.Timestamp("2026-06-16"), pd.Timestamp("2026-06-17"),
            pd.Timestamp("2026-06-18")]
    hit = replay_scan.DayResult("AAA", "2026-06-16", bars=3)
    replay_scan.cache_put(db, hit)

    loads, computed = [], []
    monkeypatch.setattr(replay_scan, "scan_day", lambda h, tk, d: computed.append(
        (tk, d)) or replay_scan.DayResult(tk, f"{d:%Y-%m-%d}", bars=1))

    scan = replay_scan.plan(["AAA", "BBB"], days, db)
    assert len(scan.jobs) == 6 and len(scan.to_compute) == 5
    seen = []
    out = replay_scan.run(scan, lambda tk: loads.append(tk) or {}, db,
                          progress=lambda n, total, r: seen.append((n, total)))
    assert loads == ["AAA", "BBB"]
    assert ("AAA", days[0]) not in computed and len(computed) == 5
    assert out[0] == hit and seen[-1] == (6, 6)
    # Everything computed is now cached for the next scan.
    assert not replay_scan.plan(["AAA", "BBB"], days, db).to_compute


def test_one_bad_ticker_does_not_sink_the_scan(tmp_path):
    def _load(tk):
        raise RuntimeError("no 5m data for ZZZ")
    out = replay_scan.run(replay_scan.plan(["ZZZ"], ["2026-06-18"], None), _load)
    assert out[0].status == "error" and "ZZZ" in out[0].error


def test_grid_puts_dates_down_and_tickers_across():
    rs = [replay_scan.DayResult("AAA", "2026-06-17"),
          replay_scan.DayResult("BBB", "2026-06-18")]
    g = replay_scan.grid(rs)
    assert list(g.index) == ["2026-06-18", "2026-06-17"]      # newest first
    assert list(g.columns) == ["AAA", "BBB"]
    assert g.loc["2026-06-18", "AAA"] is None and g.loc["2026-06-17", "AAA"] is rs[0]
    # A ticker with nothing scanned yet still gets its (empty) column.
    assert list(replay_scan.grid(rs, ["CCC", "AAA"]).columns) == ["CCC", "AAA", "BBB"]


# --------------------------------------------------------------------------- #
# The per-day daily window (shared by the replay page and the backtest).
# --------------------------------------------------------------------------- #
def test_daily_for_day_rebuilds_the_trailing_month_from_the_six_month_frame():
    idx = pd.bdate_range("2026-03-02", "2026-06-30")
    m6 = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                       "volume": 1.0}, index=idx)
    m1_fetched_today = m6.iloc[-22:]
    day = pd.Timestamp("2026-05-15")
    out = replay.daily_for_day({Timeframe.M1: m1_fetched_today, Timeframe.M6: m6}, day)
    m1 = out[Timeframe.M1]
    assert m1.index.max() < day                               # never the day itself
    assert m1.index.min() >= pd.Timestamp("2026-04-15")
    assert len(m1) >= 20                                      # a full month, not ~0 rows
    assert out[Timeframe.M6] is m6


def test_daily_for_day_is_a_no_op_without_a_six_month_frame():
    daily = {Timeframe.M1: pd.DataFrame()}
    assert replay.daily_for_day(daily, "2026-05-15") is daily

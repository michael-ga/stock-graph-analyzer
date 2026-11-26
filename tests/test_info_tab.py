"""Release number, trader registry, worker status and the Info-tab summaries."""
from __future__ import annotations

import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockanalyzer import traders, virtualbook, workerstatus
from stockanalyzer.db.repositories.worker_status import WorkerStatusRepository
from stockanalyzer.ui import info
from stockanalyzer.version import VERSION_FILE, bump_patch, release

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Release number
# --------------------------------------------------------------------------- #
def test_version_file_is_a_major_minor_patch_release():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION_FILE.read_text().strip())
    assert release() == VERSION_FILE.read_text().strip()


def test_bump_patch_adds_one_to_the_last_number():
    assert bump_patch("1.0.0") == "1.0.1"
    assert bump_patch("1.0.9") == "1.0.10"
    assert bump_patch("2.3.41\n") == "2.3.42"


@pytest.mark.parametrize("bad", ["", "1.0", "v1.0.0", "1.0.0-rc1", "one.two.three"])
def test_bump_patch_rejects_anything_but_major_minor_patch(bad):
    with pytest.raises(ValueError):
        bump_patch(bad)


def test_release_is_unknown_for_a_missing_or_malformed_file(tmp_path):
    assert release(tmp_path / "missing") == "unknown"
    broken = tmp_path / "VERSION"
    broken.write_text("not a version\n")
    assert release(broken) == "unknown"


def test_bump_script_prints_the_current_release():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "bump_version.py"), "--check"],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == release()


# --------------------------------------------------------------------------- #
# Trader registry
# --------------------------------------------------------------------------- #
def test_every_trader_in_the_book_is_classified():
    """A trader added to TRADER_RULES must be given a category here."""
    assert set(virtualbook.TRADER_RULES) <= set(traders.CATEGORY_OF)
    assert set(traders.CATEGORY_OF) <= set(virtualbook.TRADER_RULES)
    assert set(traders.CATEGORY_OF.values()) <= set(traders.CATEGORIES)


def test_retired_matches_the_rule_text():
    retired_in_text = {name for name, rule in virtualbook.TRADER_RULES.items()
                       if "(retired" in rule}
    assert traders.RETIRED == retired_in_text


def test_swing_plan_bots_are_long_term_and_intraday_bots_are_day_trade():
    assert traders.category_of("bot-GO") == traders.LONG
    assert traders.category_of("bot-70") == traders.LONG
    for name in ("bot-gap-mgd", "rev-strong-touch", "sb-mkt", "xfade-chop"):
        assert traders.category_of(name) == traders.DAY
    assert traders.category_of("me") == traders.MANUAL


def test_live_setup_bots_are_all_listed_as_live_day_traders():
    from stockanalyzer import setup_bots
    live_day = {t.name for t in traders.by_category(traders.DAY, live=True)}
    assert set(setup_bots.bot_names()) <= live_day


# --------------------------------------------------------------------------- #
# Worker status
# --------------------------------------------------------------------------- #
@pytest.fixture()
def fresh_status(book_db, monkeypatch):
    monkeypatch.setattr(workerstatus, "_started", False)
    return WorkerStatusRepository()


def test_first_beat_creates_the_row_and_later_beats_refresh_it(fresh_status):
    assert workerstatus.current() is None
    workerstatus.beat()
    first = workerstatus.current()
    assert first["release"] == release()
    workerstatus.beat()
    second = workerstatus.current()
    assert second["started_at"] == first["started_at"]
    assert second["heartbeat_at"] >= first["heartbeat_at"]


def test_a_new_process_run_restarts_the_row(fresh_status, monkeypatch):
    workerstatus.beat()
    first = workerstatus.current()["started_at"]
    monkeypatch.setattr(workerstatus, "_started", False)     # the worker restarted
    workerstatus.beat()
    assert workerstatus.current()["started_at"] >= first


def test_status_beat_never_raises_without_a_database(monkeypatch):
    from stockanalyzer.db import session as db_session
    monkeypatch.setattr(db_session, "_engine", None, raising=False)
    monkeypatch.setattr(workerstatus, "_started", False)
    workerstatus.beat()                                       # must not raise


def test_background_beat_never_holds_up_the_caller_and_never_stacks(monkeypatch):
    """A hung database must not delay the container heartbeat file: the beat runs on its
    own thread, and a second one is skipped while the first is still stuck."""
    import threading
    release_beat, started = threading.Event(), threading.Event()

    def stuck_beat():
        started.set()
        release_beat.wait(timeout=5)

    monkeypatch.setattr(workerstatus, "beat", stuck_beat)
    assert workerstatus.beat_in_background() is True          # returns at once
    assert started.wait(timeout=2)
    assert workerstatus.beat_in_background() is False         # previous one still running
    release_beat.set()
    for _ in range(100):                                      # the lock frees when it ends
        if workerstatus.beat_in_background():
            break
        time.sleep(0.02)
    else:
        pytest.fail("the background beat never finished")


def test_default_heartbeat_writes_the_file_then_updates_the_status_row(fresh_status, tmp_path):
    from stockanalyzer import radar_worker
    radar_worker.write_heartbeat_and_status(tmp_path / "hb")
    assert (tmp_path / "hb").exists()
    for _ in range(100):                                      # the row is written off-thread
        if workerstatus.current() is not None:
            break
        time.sleep(0.02)
    assert workerstatus.current() is not None


def test_the_trading_cycle_does_not_touch_the_status_code():
    """Why this PR is not [ALGO]: the status plumbing stays out of bot-execution code."""
    import inspect
    from stockanalyzer import radar_worker
    for name in ("run_cycle", "run_forever", "process_analysis"):
        assert "workerstatus" not in inspect.getsource(getattr(radar_worker, name)), name


def test_health_is_running_then_stale_then_never():
    status = {"heartbeat_at": NOW - timedelta(seconds=20)}
    assert workerstatus.health(status, NOW) == ("running", 20.0)
    status = {"heartbeat_at": NOW - timedelta(seconds=workerstatus.STALE_AFTER_S + 1)}
    assert workerstatus.health(status, NOW)[0] == "stale"
    assert workerstatus.health(None, NOW) == ("never", None)


# --------------------------------------------------------------------------- #
# Info-tab summaries
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("seconds,text", [
    (None, "never"), (5, "5 s ago"), (89, "89 s ago"), (120, "2 min ago"),
    (3 * 3600, "3 h ago"), (3 * 86400, "3 d ago"),
])
def test_age_text(seconds, text):
    assert info.age_text(seconds) == text


def _status(**over):
    base = {"name": "radar-worker", "release": "1.0.0", "build": "x",
            "started_at": NOW - timedelta(hours=1), "heartbeat_at": NOW - timedelta(seconds=10),
            "phase": "regular"}
    base.update(over)
    return base


def test_worker_summary_running_shows_the_beat_and_the_market_phase():
    summary = info.worker_summary(_status(), NOW)
    assert summary["icon"] == "🟢" and "running" in summary["headline"]
    assert "10 s ago" in summary["headline"]
    assert "regular session" in " ".join(summary["lines"])


def test_worker_summary_flags_a_silent_worker_and_a_missing_one():
    stale = info.worker_summary(_status(heartbeat_at=NOW - timedelta(minutes=10)), NOW)
    assert stale["icon"] == "🔴" and "not responding" in stale["headline"]
    assert info.worker_summary(None, NOW)["icon"] == "⚪"


def test_worker_summary_says_idle_when_the_market_is_closed():
    summary = info.worker_summary(_status(phase="closed"), NOW)
    assert any("Idle until pre-market" in line for line in summary["lines"])


def _pos(trader, status="open", ticker="AAA", opened_ts=1000.0):
    return {"trader": trader, "status": status, "ticker": ticker, "opened_ts": opened_ts,
            "entry": 10.0, "shares": 1, "pnl_usd": 0.0}


def test_book_summary_counts_open_positions_by_category_and_finds_the_last_bot_entry():
    cancelled = _pos("bot-GO", status="closed", opened_ts=1100)
    cancelled["close_reason"] = "cancelled"
    book = [_pos("bot-GO", opened_ts=100), _pos("sb-mkt", ticker="SNXX", opened_ts=300),
            _pos("rev-strong-touch", opened_ts=200),
            _pos("bot-70", status="pending", opened_ts=1000),    # armed, not filled
            cancelled,                                           # never filled
            _pos("me", ticker="ZZZ", opened_ts=999),             # the owner is not a bot
            _pos("bot-70", status="closed", opened_ts=50)]
    out = info.book_summary(book)
    assert out["open"] == {traders.DAY: 2, traders.LONG: 1, traders.MANUAL: 1}
    assert out["armed"] == 1
    assert out["last_action"]["trader"] == "sb-mkt"
    assert out["last_action"]["ticker"] == "SNXX"


def test_book_summary_of_an_empty_book():
    out = info.book_summary([])
    assert out["last_action"] is None and out["armed"] == 0


def test_trader_rows_list_bots_that_have_not_traded_yet():
    rows = info.trader_rows(traders.DAY, live=True, report_rows=[])
    names = {row["trader"] for row in rows}
    assert {"sb-am-bias", "sb-mkt", "xfade-dbl-bottom", "xfade-chop",
            "rev-trend-vol", "rev-strong-vol", "bot-gap-mgd"} <= names
    assert all(row["closed trades"] == 0 and row["win %"] is None for row in rows)


def test_frame_shows_a_dash_for_win_rate_until_a_bot_has_traded():
    report = [{"trader": "sb-mkt", "closed": 4, "win_rate": 50.0,
               "realized_usd": 12.5, "open": 0, "armed": 0}]
    frame = info._frame(info.trader_rows(traders.DAY, live=True, report_rows=report))
    wins = dict(zip(frame["trader"], frame["win %"]))
    assert wins["sb-mkt"] == "50%" and wins["xfade-chop"] == "–"
    assert list(frame.columns)[-1] == "rule"          # the long column goes last


def test_trader_rows_take_results_from_the_report_and_split_live_from_retired():
    report = [{"trader": "sb-mkt", "closed": 4, "win_rate": 50.0,
               "realized_usd": 12.5, "open": 1, "armed": 0}]
    live = {r["trader"]: r for r in info.trader_rows(traders.DAY, live=True, report_rows=report)}
    assert live["sb-mkt"]["closed trades"] == 4 and live["sb-mkt"]["win %"] == 50.0
    assert live["sb-mkt"]["open"] == 1
    retired = {r["trader"] for r in info.trader_rows(traders.DAY, live=False, report_rows=[])}
    assert "bot-gap-fixed" in retired and "bot-gap-fixed" not in live
    long_live = {r["trader"] for r in info.trader_rows(traders.LONG, live=True, report_rows=[])}
    assert long_live == {"bot-GO", "bot-70"}

"""The replay trade book — the isolated sandbox and the per-bar lifecycle.

Practice trades must never reach the real book: the algorithm is judged on a
sample that is already small, and salting it with training runs would quietly
corrupt every conclusion drawn from it. These tests pin that isolation, and the
bar-clock discipline that makes a replayed trade's timestamps mean anything.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from stockanalyzer import replay, virtualbook
from stockanalyzer.db import session as db_session
from stockanalyzer.setups import orb, reversal

_DAY = pd.Timestamp("2026-06-18")
_T0 = pd.Timestamp("2026-06-18 09:45", tz="America/New_York")

_ALL_BOTS = {"orb": True, "rev": True, "swing": True}
_NO_BOTS = {"orb": False, "rev": False, "swing": False}


# --------------------------------------------------------------------------- #
# Hand-built bars — precise control over the lifecycle, no indicator cost.
# --------------------------------------------------------------------------- #
def _gap(fired=False, entry=104.0, stop=102.0, target=108.0) -> orb.GapGoDecision:
    return orb.GapGoDecision(
        fired=fired, reason="test", gap_up=True, in_window=True,
        broke_or_high=fired, rvol=3.0, vol_ok=True,
        entry=entry if fired else None, stop=stop if fired else None,
        target=target if fired else None, rr=2.0 if fired else None)


def _rev(fired=False, variant="rev-trend-touch") -> reversal.ReversalDecision:
    return reversal.ReversalDecision(
        fired=fired, reason="test", variant=variant, at_support=fired,
        support_conf=2, trend_ok=True, reversed_up=fired, aim_ok=True,
        support=102.0, entry=103.0 if fired else None,
        stop=101.5 if fired else None, target=106.0 if fired else None,
        rr=2.0 if fired else None)


def _bar(i: int, *, high=105.0, low=103.0, close=104.0, gap_fired=False,
         rev_fired=False, go=False, score=60, kind="immediate", trigger=None,
         extended=False) -> replay.ReplayBar:
    # score 60 is deliberately under bot-70's threshold, so the default bar fires
    # no swing bot unless a test asks for one.
    t = _T0 + pd.Timedelta(minutes=5 * i)
    return replay.ReplayBar(
        i=i, t=t, epoch=t.timestamp(), open=close, high=high, low=low,
        close=close, volume=1000.0, d1=None, card=None,
        setup="Breakout (volume)", kind=kind, go=go, score=score, rr=2.0,
        entry=104.0, stop=102.0, target=108.0, trigger=trigger, actionable=True,
        extended=extended,
        light="go" if go else "forming", horizon="1–3 days",
        earned=80, denom=120, lost=(),
        snapshot={"setup": "Breakout (volume)", "score": 71},
        bots=replay.BotVerdicts(
            orb=_gap(gap_fired), reversals=(_rev(rev_fired),),
            swing_go=go, swing_block="" if go else "vol_ok: needs volume",
            contradiction=None))


def _session(bars) -> replay.ReplaySession:
    idx = [b.t for b in bars]
    frame = pd.DataFrame({"open": [b.open for b in bars],
                          "high": [b.high for b in bars],
                          "low": [b.low for b in bars],
                          "close": [b.close for b in bars],
                          "volume": [b.volume for b in bars]}, index=idx)
    return replay.ReplaySession(
        ticker="TEST", day=_DAY, prev_close=100.0,
        orange=orb.OpeningRange(day="2026-06-18", open=103.0, high=103.6,
                                low=102.8, gap_pct=0.03, gap_up=True),
        bars=tuple(bars), session_full=frame,
        fired_bars=tuple(b.i for b in bars if b.bots.any_fired))


def _db(tmp_path, attempt=1):
    return replay.replay_db_path(tmp_path, "TEST", _DAY, attempt)


# --------------------------------------------------------------------------- #
# 🔴 Sandbox isolation.
# --------------------------------------------------------------------------- #
def test_replay_db_ignores_legacy_json_beside_it(tmp_path):
    """The retired SQLite store seeded a brand-new book from a ``.virtualbook.json``
    next to it. Sandbox books never import anything: a fresh one is always empty."""
    legacy = [{"id": "old1", "ticker": "NOK", "trader": "me", "status": "closed",
               "kind": "immediate", "entry": 14.0, "stop": 13.3, "target": 14.8,
               "opened_ts": 1.0, "opened": "2026-06-01 10:00", "pnl_pct": 5.0}]
    (tmp_path / ".virtualbook.json").write_text(json.dumps(legacy))
    (tmp_path / ".replay").mkdir()
    (tmp_path / ".replay" / ".virtualbook.json").write_text(json.dumps(legacy))

    assert replay.load_book(_db(tmp_path)) == []
    assert replay.load_book(tmp_path / "beside.db") == []


def test_replay_db_path_is_namespaced_per_ticker_day_and_attempt(tmp_path):
    a, b = _db(tmp_path, 1), _db(tmp_path, 2)
    assert a != b
    assert a.parent == tmp_path / ".replay" and a.parent.is_dir()
    assert a.name == "TEST-20260618-1.db"


def test_a_restart_starts_from_an_empty_book(tmp_path):
    sess = _session([_bar(0, gap_fired=True)])
    replay.apply_bar(sess, 0, _ALL_BOTS, _db(tmp_path, 1))
    assert replay.load_book(_db(tmp_path, 1))
    assert replay.load_book(_db(tmp_path, 2)) == []


def test_next_attempt_skips_books_already_on_disk(tmp_path):
    """The attempt counter lives in session state, which dies with the server —
    numbering from 1 again would hand a 'fresh' run the previous run's trades."""
    assert replay.next_attempt(tmp_path, "TEST", _DAY) == 1
    replay.apply_bar(_session([_bar(0, gap_fired=True)]), 0, _ALL_BOTS,
                     _db(tmp_path, 1))
    assert replay.next_attempt(tmp_path, "TEST", _DAY) == 2
    # ...and a different ticker/day is its own sequence.
    assert replay.next_attempt(tmp_path, "OTHER", _DAY) == 1


def test_next_attempt_ignores_unrelated_files(tmp_path):
    folder = tmp_path / ".replay"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "TEST-20260618-notanumber.db").write_text("")
    (folder / "TEST-20260618-1.db-wal").write_text("")
    assert replay.next_attempt(tmp_path, "TEST", _DAY) == 1


def test_the_real_book_is_never_touched(tmp_path):
    """Whatever a replay does, the application database must stay untouched."""
    from stockanalyzer.auth import AuthService
    from stockanalyzer.db.models import Base

    engine = db_session.configure_engine(f"sqlite:///{tmp_path / 'real.db'}")
    Base.metadata.create_all(engine)
    real_owner = AuthService().create_user("real-owner", "correct horse battery staple").id
    sess = _session([_bar(0, gap_fired=True), _bar(1), _bar(2, low=95.0)])
    for i in range(3):
        replay.apply_bar(sess, i, _ALL_BOTS, _db(tmp_path))
    replay.force_flat(sess, _db(tmp_path))
    assert virtualbook.load(user_id=real_owner) == []
    assert replay.load_book(_db(tmp_path))
    engine.dispose()


def test_a_replay_never_reaches_postgres_even_when_it_is_configured(tmp_path,
                                                                    monkeypatch):
    """On the NUC ``DATABASE_URL`` routes the application book to PostgreSQL. A
    replay binds the repositories to its own sandbox file, so it must never even
    create the application engine — practice trades in the production book would
    corrupt the real sample."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused@nowhere/db")
    monkeypatch.setattr(db_session, "_engine", None)
    monkeypatch.setattr(db_session, "_factory", None)

    def _forbidden(*_a, **_k):
        raise AssertionError("replay reached the application database")

    monkeypatch.setattr(db_session, "configure_engine", _forbidden)
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True), _bar(1), _bar(2, close=106.0)])
    for i in range(3):
        replay.apply_bar(sess, i, _ALL_BOTS, db)
    replay.force_flat(sess, db)
    card = replay.scorecard(sess, db)
    assert card["book"] and all(r["status"] == "closed" for r in card["book"])
    assert db_session._engine is None


# --------------------------------------------------------------------------- #
# Per-bar lifecycle.
# --------------------------------------------------------------------------- #
def test_positions_are_stamped_with_the_bar_clock_not_the_wall_clock(tmp_path):
    """A replayed trade opened at 09:45 on 2026-06-18 must say so — the book's
    expiry and Friday-flat logic both read these timestamps."""
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True)])
    replay.apply_bar(sess, 0, _ALL_BOTS, db)
    rows = replay.load_book(db)
    assert rows
    for row in rows:
        assert row["opened_ts"] == sess.bars[0].epoch


def test_a_bar_that_spans_both_levels_is_resolved_as_the_stop(tmp_path):
    """Marking walks low → high → close, so an ambiguous bar takes the loss."""
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True),
                     _bar(1, low=101.0, high=109.0, close=105.0)])
    replay.apply_bar(sess, 0, _ALL_BOTS, db)
    replay.apply_bar(sess, 1, _ALL_BOTS, db)
    for row in replay.load_book(db):
        assert row["status"] == "closed"
        assert row["close_reason"] == "stop_hit"
        assert row["closed_ts"] == sess.bars[1].epoch


def test_a_position_is_not_judged_against_the_bar_it_opened_on(tmp_path):
    """Auto-placement happens after marking — otherwise a break bar whose own low
    sits under the stop would open and close in the same instant."""
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True, low=101.0, high=109.0)])
    replay.apply_bar(sess, 0, _ALL_BOTS, db)
    rows = replay.load_book(db)
    assert rows and all(r["status"] == "open" for r in rows)


def test_stepping_one_bar_at_a_time_equals_a_fast_forward(tmp_path):
    """Playback speed skips *frames*, never side effects — the books must match."""
    sess = _session([_bar(0, gap_fired=True), _bar(1, high=106.0),
                     _bar(2, low=101.5), _bar(3)])
    slow, fast = _db(tmp_path, 1), _db(tmp_path, 2)
    for i in range(len(sess.bars)):
        replay.apply_bar(sess, i, _ALL_BOTS, slow)
    for i in range(len(sess.bars)):          # same loop, one UI frame
        replay.apply_bar(sess, i, _ALL_BOTS, fast)

    def _cmp(rows):
        return sorted((r["trader"], r["status"], r["entry"], r["stop"],
                       r["exit_price"], r["opened_ts"], r["closed_ts"])
                      for r in rows)

    assert _cmp(replay.load_book(slow)) == _cmp(replay.load_book(fast))


def test_only_one_position_per_trader_per_ticker(tmp_path):
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True), _bar(1, gap_fired=True)])
    replay.apply_bar(sess, 0, _ALL_BOTS, db)
    replay.apply_bar(sess, 1, _ALL_BOTS, db)
    traders = [r["trader"] for r in replay.load_book(db)]
    assert sorted(traders) == sorted(set(traders))


# --------------------------------------------------------------------------- #
# Auto-place toggles.
# --------------------------------------------------------------------------- #
def test_toggles_off_means_signal_only(tmp_path):
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True, rev_fired=True, go=True)])
    replay.apply_bar(sess, 0, _NO_BOTS, db)
    assert replay.load_book(db) == []
    assert sess.bars[0].bots.any_fired is True      # the signal still exists


@pytest.mark.parametrize("key,expected", [
    ("orb", {"bot-gap-fixed", "bot-gap-mgd"}),
    ("rev", {"rev-trend-touch"}),
    ("swing", {"bot-GO"}),
])
def test_each_toggle_places_only_its_own_bots(tmp_path, key, expected):
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True, rev_fired=True, go=True)])
    replay.apply_bar(sess, 0, {**_NO_BOTS, key: True}, db)
    assert {r["trader"] for r in replay.load_book(db)} == expected


def test_the_replay_runs_the_same_swing_roster_as_the_live_radar():
    """bot-GO / bot-70 / bot-BRK must all exist here, or the replay would quietly
    grade a smaller field than the one actually trading."""
    import app

    assert [n for n, _ in replay.SWING_BOTS] == [n for n, _, _ in app._BOTS]


def test_bot_70_takes_a_strong_plan_that_falls_short_of_go(tmp_path):
    db = _db(tmp_path)
    replay.apply_bar(_session([_bar(0, go=False, score=74)]), 0,
                     {**_NO_BOTS, "swing": True}, db)
    assert {r["trader"] for r in replay.load_book(db)} == {"bot-70"}


def test_bot_70_stands_aside_on_an_extended_plan(tmp_path):
    db = _db(tmp_path)
    replay.apply_bar(_session([_bar(0, go=False, score=74, extended=True)]), 0,
                     {**_NO_BOTS, "swing": True}, db)
    assert replay.load_book(db) == []


def test_bot_brk_arms_a_pending_order_and_fills_on_the_break(tmp_path):
    """The breakout bot is the only one that doesn't enter at market: it arms a
    trigger, and the book's two-phase fill decides whether the break confirms."""
    db = _db(tmp_path)
    bars = [_bar(0, kind="breakout_wait", trigger=104.0, score=60,
                 high=103.0, low=102.5, close=102.8),
            _bar(1, high=106.0, low=103.5, close=105.5),   # confirms past trigger
            _bar(2, high=105.0, low=103.9, close=104.1)]   # throwback fills entry
    sess = _session(bars)

    replay.apply_bar(sess, 0, {**_NO_BOTS, "swing": True}, db)
    row = replay.load_book(db)[0]
    assert row["trader"] == "bot-BRK" and row["status"] == "pending"

    replay.apply_bar(sess, 1, {**_NO_BOTS, "swing": True}, db)
    replay.apply_bar(sess, 2, {**_NO_BOTS, "swing": True}, db)
    row = replay.load_book(db)[0]
    assert row["status"] in ("open", "closed"), "the armed breakout never filled"
    assert row["activated_ts"] in (sess.bars[1].epoch, sess.bars[2].epoch)


def test_armed_swing_bots_reports_the_watch_only_chokepoint():
    assert replay.armed_swing_bots(_bar(0, go=True)) == ("bot-GO",)
    assert replay.armed_swing_bots(_bar(0, go=True, extended=True)) == ()
    assert replay.armed_swing_bots(_bar(0, go=False, score=40)) == ()


def test_the_gap_pair_is_an_ab_test_on_identical_geometry(tmp_path):
    """bot-gap-fixed and bot-gap-mgd differ only in whether the exit is managed —
    same entry/stop/target, so they collapse to one cohort."""
    db = _db(tmp_path)
    replay.apply_bar(_session([_bar(0, gap_fired=True)]), 0,
                     {**_NO_BOTS, "orb": True}, db)
    rows = {r["trader"]: r for r in replay.load_book(db)}
    fixed, managed = rows["bot-gap-fixed"], rows["bot-gap-mgd"]
    assert (fixed["entry"], fixed["stop"], fixed["target"]) == \
           (managed["entry"], managed["stop"], managed["target"])
    assert fixed["managed"] == 0 and managed["managed"] == 1
    assert fixed["cohort_id"] == managed["cohort_id"]


def test_a_swing_bot_stands_aside_on_a_non_actionable_plan(tmp_path):
    db = _db(tmp_path)
    bar = _bar(0, go=True)
    sess = _session([replay.ReplayBar(**{**bar.__dict__, "actionable": False})])
    replay.apply_bar(sess, 0, {**_NO_BOTS, "swing": True}, db)
    assert replay.load_book(db) == []


# --------------------------------------------------------------------------- #
# End of session.
# --------------------------------------------------------------------------- #
def test_force_flat_closes_everything_at_the_last_bar(tmp_path):
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True), _bar(1), _bar(2, close=106.0)])
    for i in range(3):
        replay.apply_bar(sess, i, _ALL_BOTS, db)
    closed = replay.force_flat(sess, db)
    assert closed
    rows = replay.load_book(db)
    assert all(r["status"] == "closed" for r in rows)
    assert all(r["close_reason"] == "eod" for r in closed)
    assert all(r["exit_price"] == sess.bars[-1].close for r in closed)
    assert all(r["closed_ts"] == sess.bars[-1].epoch for r in closed)


def test_scorecard_reports_per_trader_and_idea_level_views(tmp_path):
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True), _bar(1), _bar(2, close=106.0)])
    for i in range(3):
        replay.apply_bar(sess, i, _ALL_BOTS, db)
    replay.force_flat(sess, db)

    card = replay.scorecard(sess, db)
    assert {r["trader"] for r in card["book"]} == {"bot-gap-fixed", "bot-gap-mgd"}
    assert set(card["stats"]["traders"]) == {"bot-gap-fixed", "bot-gap-mgd"}
    # Two bots acting on one plan is ONE idea, not two — that dedupe is the whole
    # point of the idea-level view.
    assert len(card["ideas"]["ideas"]) == 1


def test_missed_signals_price_what_standing_aside_cost(tmp_path):
    """A signal you didn't take is judged over the revealed day."""
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True), _bar(1, high=109.0),
                     _bar(2, close=108.5)])
    missed = replay.missed_signals(sess, db)
    assert len(missed) == 1
    assert missed[0]["name"].endswith("Gap-and-Go")
    assert missed[0]["status"] == "target" and missed[0]["r"] == 2.0


def test_a_signal_you_took_is_not_reported_as_missed(tmp_path):
    db = _db(tmp_path)
    sess = _session([_bar(0, gap_fired=True), _bar(1, high=109.0)])
    with replay.book(db) as owner:
        virtualbook.open_position(ticker="TEST", trader="me", entry=104.0, stop=102.0,
                                  target=108.0, now=sess.bars[0].epoch, user_id=owner)
    assert replay.missed_signals(sess, db) == []


def test_excursions_are_computed_for_manual_trades(tmp_path):
    """The book only tracks MFE/MAE on managed rows, so a manual trade would read
    0/0 — these come from the session bars instead."""
    db = _db(tmp_path)
    sess = _session([_bar(0), _bar(1, high=110.0, low=99.0), _bar(2)])
    with replay.book(db) as owner:
        row = virtualbook.open_position(ticker="TEST", trader="me", entry=100.0,
                                        stop=98.0, target=106.0,
                                        now=sess.bars[0].epoch, user_id=owner)
    mfe, mae = replay.excursions(sess, row, upto=2)
    assert mfe == pytest.approx(10.0)
    assert mae == pytest.approx(-1.0)


# --------------------------------------------------------------------------- #
# 🎯 Setups in the replay (never in live mode).
# --------------------------------------------------------------------------- #
def _with_setup(bar, *, entry=104.0, stop=102.5, target=106.0, intrabar=False):
    from dataclasses import replace

    from stockanalyzer.setups.base import SetupDecision
    dec = SetupDecision(True, "test fire", "double_bottom", "xfade-dbl-bottom",
                        entry=entry, stop=stop, target=target, rr=1.0,
                        filled_intrabar=intrabar)
    return replace(bar, bots=replace(bar.bots, setups=(dec,)))


def test_a_fired_setup_trades_only_when_its_toggle_is_on(tmp_path):
    db = _db(tmp_path)
    sess = _session([_with_setup(_bar(0))])
    assert sess.bars[0].bots.any_fired is True
    replay.apply_bar(sess, 0, {**_ALL_BOTS, "setups": False}, db)
    replay.apply_setups(sess, 0, _NO_BOTS, db)
    assert [r["trader"] for r in replay.load_book(db)] == []             # bar fires no bot
    replay.apply_bar(sess, 0, {**_NO_BOTS, "setups": True}, db)          # apply_bar ignores it
    assert replay.load_book(db) == []
    replay.apply_setups(sess, 0, {**_NO_BOTS, "setups": True}, db)
    [row] = replay.load_book(db)
    assert row["trader"] == "xfade-dbl-bottom" and row["status"] == "open"


def test_a_resting_setup_order_stopped_inside_its_fill_bar_is_a_full_loss(tmp_path):
    db = _db(tmp_path)
    sess = _session([_with_setup(_bar(0, low=102.0), stop=102.5, intrabar=True),
                     _with_setup(_bar(1), stop=102.5, intrabar=True)])
    replay.apply_setups(sess, 0, {**_NO_BOTS, "setups": True}, db)
    [row] = replay.load_book(db)
    assert row["close_reason"] == "stop_hit" and row["exit_price"] == 102.5
    assert replay.trade_r(row) == -1.0


def test_the_setups_family_is_a_replay_toggle_and_a_scan_family():
    assert "setups" in replay.BOT_KEYS
    from stockanalyzer import replay_scan
    bar = _with_setup(_bar(0))
    assert ("setups", "xfade-dbl-bottom", 104.0) in replay_scan.bar_signals(bar)

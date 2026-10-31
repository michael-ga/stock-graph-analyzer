"""Book analytics: idea-level cohorts, full indicator persistence, loser patterns."""
from __future__ import annotations

from sqlalchemy import select

from stockanalyzer import virtualbook as vb
from stockanalyzer.db.models import TradeIndicator
from stockanalyzer.db.session import session_scope


def _snap(score=70, setup="Support test (uptrend)", **ind):
    base = {"close": 100.0, "rsi": 55.0}
    base.update(ind)
    return {"score": score, "setup": setup,
            "timeframes": {"6M": {"signals": [], "bias_score": 0.2,
                                  "trend_dir": "bull", "indicators": base}}}


def test_same_idea_across_bots_is_one_cohort(owner):
    snap = {"score": 80, "setup": "Pullback to 20-EMA"}
    # Two bots take the IDENTICAL plan (same ticker/setup/levels/day) → one idea.
    a = vb.open_position(ticker="NOK", trader="bot-GO", entry=14.0, stop=13.3,
                         target=14.8, snapshot=snap, now=1_000_000.0, user_id=owner)
    b = vb.open_position(ticker="NOK", trader="me", entry=14.0, stop=13.3,
                         target=14.8, snapshot=snap, now=1_000_000.0, user_id=owner)
    # A genuinely different idea (different ticker/levels).
    c = vb.open_position(ticker="MU", trader="bot-BRK", entry=100.0, stop=95.0,
                         target=110.0, snapshot={"score": 60,
                         "setup": "Support test (uptrend)"}, now=1_000_000.0, user_id=owner)
    book = {t["id"]: t for t in vb.load(user_id=owner)}
    assert book[a["id"]]["cohort_id"] == book[b["id"]]["cohort_id"]   # same idea
    assert book[a["id"]]["cohort_id"] != book[c["id"]]["cohort_id"]   # different idea

    vb.close_position(a["id"], 13.4, now=1_000_100.0, user_id=owner)         # loss
    vb.close_position(b["id"], 13.4, now=1_000_100.0, user_id=owner)         # loss (dup)
    vb.close_position(c["id"], 110.0, now=1_000_100.0, user_id=owner)        # win

    ac = vb.algorithm_correctness(user_id=owner)
    assert ac["totals"]["n_trades"] == 3       # three bot-trades…
    assert ac["totals"]["n_ideas"] == 2        # …but only two distinct ideas
    assert ac["totals"]["wins"] == 1 and ac["totals"]["losses"] == 1
    assert ac["totals"]["win_rate"] == 50      # NOT 33 (the inflated per-trade rate)


def test_full_indicator_set_is_persisted(owner):
    vb.open_position(ticker="MU", trader="me", entry=100.0, stop=95.0, target=110.0,
                     snapshot=_snap(adx=30.0, plus_di=25.0, minus_di=10.0,
                                    bb_upper=110.0, bb_lower=90.0),
                     now=1_000_000.0, user_id=owner)
    with session_scope() as session:
        r = session.scalars(select(TradeIndicator.payload)).one()
    assert r["adx"] == 30.0 and r["plus_di"] == 25.0 and r["minus_di"] == 10.0
    assert r["bb_upper"] == 110.0 and r["bb_lower"] == 90.0


def test_closed_ts_never_before_opened(owner):
    pos = vb.open_position(ticker="NOK", trader="me", entry=14.0, stop=13.3,
                          target=14.8, snapshot={"score": 70, "setup": "x"},
                          now=1_000_000.0, user_id=owner)
    vb.close_position(pos["id"], 14.4, now=1_000_100.0, user_id=owner)
    row = {t["id"]: t for t in vb.load(user_id=owner)}[pos["id"]]
    assert row["closed_ts"] is not None and row["closed_ts"] >= row["opened_ts"]


def test_losing_trade_patterns_aggregate_checks_signals_and_indicators(owner):
    snap = _snap(rsi=40.0, macd_hist=-0.2)
    snap["timeframes"]["6M"]["signals"] = [
        {"name": "MACD cross", "direction": "bear", "strength": 0.5,
         "category": "momentum"}]
    snap["checks"] = [
        {"name": "Engine bias agrees", "ok": False, "weight": 2},
        {"name": "Info only", "ok": False, "weight": 0},
        {"name": "R:R >= 2", "ok": True, "weight": 3}]
    loser = vb.open_position(ticker="AMD", trader="bot-GO", entry=100.0, stop=95.0,
                             target=110.0, snapshot=snap, now=1_000_000.0,
                             user_id=owner)
    winner = vb.open_position(ticker="MU", trader="bot-GO", entry=100.0, stop=95.0,
                              target=110.0, snapshot=_snap(rsi=70.0),
                              now=1_000_000.0, user_id=owner)
    vb.close_position(loser["id"], 96.0, now=1_000_100.0, user_id=owner)
    vb.close_position(winner["id"], 108.0, now=1_000_100.0, user_id=owner)

    patterns = vb.losing_trade_patterns(user_id=owner)
    assert patterns["n"] == 1
    assert patterns["failed_checks"] == {"Engine bias agrees": 1}   # weighted only
    assert patterns["common_signals"] == {"MACD cross:bear": 1}
    assert patterns["avg_indicators"]["avg_rsi"] == 40.0            # winner excluded
    assert patterns["avg_indicators"]["avg_macd_hist"] == -0.2
    assert patterns["avg_indicators"]["avg_bias"] == 0.2
    assert [t["id"] for t in vb.closed_trades(user_id=owner)] and len(
        vb.closed_trades(user_id=owner)) == 2


def test_losing_trade_patterns_empty_book(owner):
    assert vb.losing_trade_patterns(user_id=owner) == {
        "n": 0, "failed_checks": {}, "common_signals": {}, "avg_indicators": {}}


def test_book_is_scoped_to_its_owner(owner):
    from stockanalyzer.auth import AuthService
    other = AuthService().create_user("someone-else", "another long password").id
    pos = vb.open_position(ticker="NOK", trader="me", entry=14.0, stop=13.3,
                           target=14.8, now=1_000_000.0, user_id=owner)
    assert vb.load(user_id=other) == []
    assert vb.has_open("NOK", "me", user_id=other) is False
    assert vb.close_position(pos["id"], 14.4, user_id=other) is None
    assert vb.mark("NOK", 13.0, now=1_000_100.0, user_id=other) == []
    assert vb.load(user_id=owner)[0]["status"] == "open"


def test_user_id_is_required():
    import pytest
    with pytest.raises(ValueError, match="user_id"):
        vb.load(user_id="")

"""Managed positions: telemetry columns, trade_events, manage(), A/B pairing."""
from __future__ import annotations

from sqlalchemy import select

from stockanalyzer import virtualbook as vb
from stockanalyzer.db.models import TradeEvent
from stockanalyzer.db.session import session_scope


def _event_kinds(trade_id: str) -> list[str]:
    with session_scope() as session:
        return list(session.scalars(
            select(TradeEvent.kind).where(TradeEvent.trade_id == trade_id)).all())

_GAP = {"setup": "gap_and_go_orb", "score": 70}


def test_managed_telemetry_fields_set_on_open(owner):
    pos = vb.open_position(ticker="FFF", trader="me", entry=10.0, stop=9.0, target=12.0,
                           snapshot={"setup": "x"}, now=1_000_000.0, user_id=owner)
    row = vb.load(user_id=owner)[0]
    for c in ("init_stop", "mfe_pct", "mae_pct", "stop_moves", "managed",
              "entry_rvol", "hold_weekend"):
        assert c in row
    assert row["init_stop"] == row["stop"]                     # set on open
    assert row["managed"] is False and row["stop_moves"] == 0
    assert _event_kinds(pos["id"]) == []


def test_manage_trades_ratchets_and_logs(owner):
    pos = vb.open_position(ticker="AAA", trader="bot-gap-mgd", entry=100.0,
                           stop=98.0, target=104.0, managed=True, init_stop=98.0,
                           snapshot=_GAP, now=1_000_000.0, user_id=owner)
    # +1R at 102 → breakeven move to spread-adjusted entry + $0.02 = 100.02.
    vb.manage("AAA", 102.0, None, None, now=1_000_100.0, user_id=owner)
    row = {t["id"]: t for t in vb.load(user_id=owner)}[pos["id"]]
    assert row["stop"] == round(100.0 + 0.02, 4)
    assert (row["stop_moves"] or 0) >= 1
    assert "move_stop_breakeven" in _event_kinds(pos["id"])


def test_manage_trades_never_widens(owner):
    pos = vb.open_position(ticker="BBB", trader="bot-gap-mgd", entry=100.0,
                           stop=101.0, target=110.0, managed=True, init_stop=98.0,
                           snapshot=_GAP, now=1_000_000.0, user_id=owner)
    vb.manage("BBB", 103.0, None, None, now=1_000_100.0, user_id=owner)   # +2.5R, no candidate beats 101
    row = {t["id"]: t for t in vb.load(user_id=owner)}[pos["id"]]
    assert row["stop"] == 101.0


def test_manage_does_not_touch_unmanaged(owner):
    pos = vb.open_position(ticker="GGG", trader="bot-GO", entry=100.0, stop=98.0,
                           target=104.0, managed=False, snapshot=_GAP,
                           now=1_000_000.0, user_id=owner)
    vb.manage("GGG", 102.0, None, None, now=1_000_100.0, user_id=owner)
    row = {t["id"]: t for t in vb.load(user_id=owner)}[pos["id"]]
    assert row["stop"] == 98.0                                  # baseline untouched


def test_weekend_flat_applies_spread(owner):
    pos = vb.open_position(ticker="CCC", trader="bot-gap-mgd", entry=100.0,
                           stop=98.0, target=110.0, managed=True, init_stop=98.0,
                           hold_weekend=False, snapshot=_GAP, now=1_000_000.0, user_id=owner)
    changed = vb.manage("CCC", 105.0, reports=None, trend_change=None,
                        now=1_000_100.0, friday_flat=True, user_id=owner)
    assert changed and changed[0]["close_reason"] == "weekend_flat"
    row = {t["id"]: t for t in vb.load(user_id=owner)}[pos["id"]]
    expected = round(((105.0 - 0.02) / 100.0 - 1) * 100, 2)   # flat $0.02/share haircut
    assert row["pnl_pct"] == expected
    assert "weekend_flat" in _event_kinds(pos["id"])


def test_gap_pair_shares_cohort_and_ab(owner):
    f = vb.open_position(ticker="DDD", trader="bot-gap-fixed", entry=50.0, stop=49.0,
                         target=52.0, snapshot=_GAP, managed=False, init_stop=49.0,
                         now=1_000_000.0, user_id=owner)
    m = vb.open_position(ticker="DDD", trader="bot-gap-mgd", entry=50.0, stop=49.0,
                         target=52.0, snapshot=_GAP, managed=True, init_stop=49.0,
                         now=1_000_000.0, user_id=owner)
    book = {t["id"]: t for t in vb.load(user_id=owner)}
    assert book[f["id"]]["cohort_id"] == book[m["id"]]["cohort_id"]   # same idea
    vb.close_position(f["id"], 49.0, now=1_000_100.0, user_id=owner)         # fixed loss
    vb.close_position(m["id"], 52.0, now=1_000_100.0, user_id=owner)         # managed win
    ab = vb.managed_vs_fixed(user_id=owner)
    assert ab["n_pairs"] == 1
    assert ab["pairs"][0]["delta_pct"] == round((52.0 / 50 - 1) * 100
                                                - (49.0 / 50 - 1) * 100, 2)


def test_algorithm_correctness_excludes_managed(owner):
    m = vb.open_position(ticker="EEE", trader="bot-gap-mgd", entry=50.0, stop=49.0,
                         target=52.0, snapshot=_GAP, managed=True, init_stop=49.0,
                         now=1_000_000.0, user_id=owner)
    vb.close_position(m["id"], 52.0, now=1_000_100.0, user_id=owner)
    assert vb.algorithm_correctness(user_id=owner)["totals"]["n_ideas"] == 0      # managed excluded


def test_mark_trades_baseline_unchanged(owner):
    # Regression: a plain managed=False position still stops out at the stop with
    # NO spread haircut (mark() closes with the default spread=0.0).
    vb.open_position(ticker="HHH", trader="me", entry=14.0, stop=13.3, target=14.8,
                     snapshot={"setup": "x"}, now=1_000_000.0, user_id=owner)
    changed = vb.mark("HHH", 13.2, now=1_000_100.0, user_id=owner)
    assert changed[0]["close_reason"] == "stop_hit"
    assert changed[0]["pnl_pct"] == round((13.3 / 14.0 - 1) * 100, 2)

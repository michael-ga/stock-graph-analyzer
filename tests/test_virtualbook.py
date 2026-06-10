"""Virtual paper-trading book: lifecycle, auto-close, pending orders, stats."""
from __future__ import annotations

from stockanalyzer import virtualbook as vb


def _open(p, **kw):
    base = dict(ticker="NOK", trader="me", entry=14.0, stop=13.3, target=14.8,
                kind="immediate", horizon_days=3, now=1_000_000.0, path=p,
                snapshot={"score": 72, "setup": "Pullback to 20-EMA"})
    base.update(kw)
    return vb.open_position(**base)


def test_open_and_has_open(tmp_path):
    p = tmp_path / "book.json"
    pos = _open(p)
    assert pos["status"] == "open"
    assert pos["shares"] == round(vb.STAKE_USD / 14.0, 4)
    assert vb.has_open("nok", "me", p) is True
    assert vb.has_open("NOK", "bot-GO", p) is False


def test_mark_stop_hit_is_conservative(tmp_path):
    p = tmp_path / "book.json"
    _open(p)
    changed = vb.mark("NOK", 13.2, now=1_000_100.0, path=p)
    assert len(changed) == 1
    c = changed[0]
    assert c["status"] == "closed" and c["close_reason"] == "stop_hit"
    assert c["pnl_pct"] == round((13.3 / 14.0 - 1) * 100, 2)   # exits AT the stop
    assert c["pnl_usd"] < 0


def test_mark_target_hit(tmp_path):
    p = tmp_path / "book.json"
    _open(p)
    changed = vb.mark("NOK", 14.9, now=1_000_100.0, path=p)
    assert changed[0]["close_reason"] == "target_hit"
    assert changed[0]["pnl_pct"] == round((14.8 / 14.0 - 1) * 100, 2)


def test_mark_expiry_at_market(tmp_path):
    p = tmp_path / "book.json"
    _open(p)
    week_later = 1_000_000.0 + 6 * 86400          # > 3d × 1.5 cushion
    changed = vb.mark("NOK", 14.1, now=week_later, path=p)
    assert changed[0]["close_reason"] == "expired"
    assert changed[0]["exit_price"] == 14.1


def test_pending_breakout_activates_then_wins(tmp_path):
    p = tmp_path / "book.json"
    pos = _open(p, kind="breakout_wait", trigger=14.2, entry=14.21, target=15.0)
    assert pos["status"] == "pending"
    # Below the trigger: nothing happens.
    assert vb.mark("NOK", 14.1, now=1_000_050.0, path=p) == []
    # Cross the trigger → fills at the trigger price.
    changed = vb.mark("NOK", 14.3, now=1_000_100.0, path=p)
    assert changed[0]["status"] == "open" and changed[0]["entry"] == 14.2
    # Then the target.
    changed = vb.mark("NOK", 15.1, now=1_000_200.0, path=p)
    assert changed[0]["close_reason"] == "target_hit"


def test_pending_cancelled_when_stale(tmp_path):
    p = tmp_path / "book.json"
    _open(p, kind="breakout_wait", trigger=14.2)
    week_later = 1_000_000.0 + 6 * 86400
    changed = vb.mark("NOK", 14.0, now=week_later, path=p)
    assert changed[0]["status"] == "closed"
    assert changed[0]["close_reason"] == "cancelled"


def test_manual_close(tmp_path):
    p = tmp_path / "book.json"
    pos = _open(p)
    closed = vb.close_position(pos["id"], 14.4, now=1_000_100.0, path=p)
    assert closed["close_reason"] == "manual"
    assert closed["pnl_pct"] == round((14.4 / 14.0 - 1) * 100, 2)
    assert vb.has_open("NOK", "me", p) is False


def test_custom_stake_scales_pnl(tmp_path):
    p = tmp_path / "book.json"
    pos = _open(p, stake=2500.0)
    assert pos["stake"] == 2500.0
    assert pos["shares"] == round(2500.0 / 14.0, 4)
    changed = vb.mark("NOK", 14.9, now=1_000_100.0, path=p)   # target hit
    c = changed[0]
    # P&L $ scales with the stake: 2.5× the default-$1000 position.
    expected = round((14.8 - 14.0) * (2500.0 / 14.0), 2)
    assert c["pnl_usd"] == expected
    assert c["pnl_pct"] == round((14.8 / 14.0 - 1) * 100, 2)   # % unchanged by stake


def test_stats_by_trader_band_setup(tmp_path):
    p = tmp_path / "book.json"
    a = _open(p, trader="me", snapshot={"score": 82, "setup": "Pullback to 20-EMA"})
    b = _open(p, trader="bot-GO", ticker="INTC",
              snapshot={"score": 65, "setup": "Breakout (volume)"})
    vb.close_position(a["id"], 14.8, now=1_000_100.0, path=p)     # win
    vb.close_position(b["id"], 13.4, now=1_000_100.0, path=p)     # loss
    s = vb.stats(vb.load(p))
    assert s["totals"]["n"] == 2 and s["totals"]["win_rate"] == 50
    assert s["traders"]["me"]["wins"] == 1
    assert s["traders"]["bot-GO"]["losses"] == 1
    assert s["bands"]["80+"]["n"] == 1 and s["bands"]["60–69"]["n"] == 1
    assert s["setups"]["Pullback to 20-EMA"]["win_rate"] == 100


def test_cancelled_excluded_from_stats(tmp_path):
    p = tmp_path / "book.json"
    pos = _open(p, kind="breakout_wait", trigger=14.2)
    vb.close_position(pos["id"], 0, path=p)        # pending → cancelled
    s = vb.stats(vb.load(p))
    assert s["totals"]["n"] == 0

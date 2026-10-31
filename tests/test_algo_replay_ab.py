"""The real-history replay A/B's comparison (scripts/algo_replay_ab.py).

The end-to-end run fetches live history and takes minutes, so it is exercised by
running the script; these pin the pure pieces it decides with, and the setups'
trip through the harness on the synthetic sessions of tests/test_setups.py.
"""
from __future__ import annotations

from scripts import algo_replay_ab as ab


def _trade(trader="bot-gap-fixed", family="orb", t_in="09:50", r=1.0):
    return {"trader": trader, "setup": "s", "family": family, "in": t_in, "out": "10:30",
            "entry": 100.0, "exit": 102.0, "r": r}


def _day(tk="INTC", day="2026-10-20", trades=(), signals=(), error=""):
    return {"ticker": tk, "day": day, "trades": list(trades), "signals": list(signals),
            "error": error}


_CTX = {"base": "abc", "change": "x", "mode": "fast", "tickers": ["INTC"],
        "start": "2026-10-01", "end": "2026-10-21", "wall": 1}


def test_identical_days_report_no_difference():
    rows = {("INTC", "2026-10-20"): _day(trades=[_trade()])}
    cmp_ = ab.compare(rows, dict(rows))
    assert cmp_["diff_days"] == [] and cmp_["regressions"] == []
    assert ab.exit_code(cmp_) == 0
    assert ab.render(cmp_, _CTX).startswith("# Replay A/B: **NO TRADE DIFFERENCE")


def test_a_trade_that_appears_or_moves_is_a_trade_difference():
    base = {("INTC", "2026-10-20"): _day(trades=[_trade(t_in="09:50")])}
    change = {("INTC", "2026-10-20"): _day(trades=[
        _trade(t_in="10:05"), _trade(trader="rev-trend-touch", family="rev")])}
    cmp_ = ab.compare(base, change)
    [d] = cmp_["diff_days"]
    assert len(d["base_only"]) == 1 and len(d["change_only"]) == 2
    assert cmp_["changed_bots"] == ["bot-gap-fixed", "rev-trend-touch"]
    assert cmp_["bots"][1]["rev-trend-touch"]["n"] == 1
    assert cmp_["regressions"] == [] and ab.exit_code(cmp_) == 1   # book R went up


def test_signal_change_without_trades_is_reported_separately():
    base = {("AMD", "2026-10-20"): _day("AMD", signals=[{"t": "09:50", "bot": "rev-x"}])}
    change = {("AMD", "2026-10-20"): _day("AMD")}
    cmp_ = ab.compare(base, change)
    assert cmp_["diff_days"] == [] and len(cmp_["signal_only"]) == 1


def test_per_bot_counts_wins_days_and_win_pct():
    rows = {("A", "d1"): _day("A", "d1", [_trade(r=2.0), _trade(trader="rev-a", r=-1.0)]),
            ("B", "d1"): _day("B", "d1", [_trade(r=-1.0)]),
            ("A", "d2"): _day("A", "d2", [_trade(r=0.5)])}
    p = ab.per_bot(rows)
    assert p["bot-gap-fixed"] == {"n": 3, "wins": 2, "r": 1.5, "days": 2, "family": "orb",
                                 "win_pct": 66.7, "avg_r": 0.5}


# --- the owner's rule: net R and win % ------------------------------------- #
def _cmp(base_trades, change_trades):
    return ab.compare({("A", "d1"): _day("A", "d1", base_trades)},
                      {("A", "d1"): _day("A", "d1", change_trades)})


def test_a_new_losing_setup_fails_on_the_whole_book():
    cmp_ = _cmp([_trade(r=1.0)],
                [_trade(r=1.0), _trade(trader="xfade", family="setups", r=-1.0)])
    assert cmp_["regressions"] == ["whole-book net R fell +1.00 → +0.00"]
    assert ab.exit_code(cmp_) == 3
    assert "`xfade` **(added)**" in ab.render(cmp_, _CTX)


def test_a_new_winning_setup_is_progress_not_a_regression():
    cmp_ = _cmp([_trade(r=1.0)],
                [_trade(r=1.0), _trade(trader="xfade", family="setups", r=0.5)])
    assert cmp_["regressions"] == [] and ab.exit_code(cmp_) == 1


def test_an_existing_bot_losing_net_r_fails_even_if_the_book_grows():
    cmp_ = _cmp([_trade(r=1.0, t_in="09:50")],
                [_trade(r=0.5, t_in="10:00"), _trade(trader="new", family="setups", r=3.0)])
    assert cmp_["regressions"] == ["`bot-gap-fixed` net R fell +1.00 → +0.50"]


def test_win_pct_may_drop_up_to_five_points_but_not_more():
    wins = [_trade(t_in=f"10:{i:02d}", r=1.0) for i in range(19)]
    base = wins + [_trade(t_in="11:00", r=-1.0)]                       # 95% win
    nineteen = wins + [_trade(t_in="11:05", r=-0.9)]                   # 95%, R up
    assert _cmp(base, nineteen)["regressions"] == []
    worse = wins[:17] + [_trade(t_in="11:10", r=8.0)] + [               # 90%... still R up
        _trade(t_in="11:2" + str(i), r=-0.1) for i in range(2)]
    regs = _cmp(base, worse)["regressions"]
    assert regs == []                                                  # 90% → exactly −5 pts
    much_worse = wins[:15] + [_trade(t_in="11:30", r=20.0)] + [
        _trade(t_in="11:4" + str(i), r=-0.1) for i in range(4)]       # 80%
    assert _cmp(base, much_worse)["regressions"] == [
        "`bot-gap-fixed` win % fell 95% → 80% (more than 5 points)"]


def test_a_bot_that_stops_trading_is_named():
    cmp_ = _cmp([_trade(r=1.0), _trade(trader="rev-a", family="rev", r=-1.0)],
                [_trade(r=1.0)])
    assert "`rev-a` no longer trades (was 1 trades, -1.00 R)" in cmp_["regressions"]


def test_report_leads_with_regressions_and_lists_errors():
    base = {("TSLA", "d1"): _day("TSLA", "d1", [_trade(r=1.0)], error="no session bars")}
    change = {("TSLA", "d1"): _day("TSLA", "d1", [_trade(r=-1.0)], error="no session bars")}
    report = ab.render(ab.compare(base, change), _CTX)
    assert report.startswith("# Replay A/B: **REGRESSION")
    assert "## Regressions (net R + win % rule)" in report
    assert "## Errors" in report and "**Whole book**" in report


# --- the worker's pieces ---------------------------------------------------- #
def test_setup_trades_come_from_the_harness_with_net_r():
    from stockanalyzer import setups
    from stockanalyzer.data.schema import Timeframe
    from stockanalyzer.setups import harness
    from tests import test_setups as fx

    h = fx._hist(fx._db_session())
    hist = {"i5": h["i5"], "daily": {Timeframe.Y1: h["daily"]}, "days": h["days"]}
    [t] = [t for t in ab._setup_trades((setups, harness), hist, 1, "X", Timeframe)
           if t["trader"] == "xfade-dbl-bottom"]
    assert t["family"] == "setups" and t["in"] == "11:30" and t["entry"] == 98.2
    assert t["gross_r"] == 1.0 and t["r"] < t["gross_r"]              # the spread is paid
    assert ab._setup_trades(None, hist, 1, "X", Timeframe) == []       # tree without setups/


def test_tree_fingerprint_moves_with_any_package_file(tmp_path):
    pkg = tmp_path / "stockanalyzer" / "data"
    pkg.mkdir(parents=True)
    (pkg / "feed.py").write_text("X = 1\n", encoding="utf-8")
    before = ab.tree_fingerprint(tmp_path)
    (pkg / "feed.py").write_text("X = 2\n", encoding="utf-8")   # an *input*, not decision code
    assert ab.tree_fingerprint(tmp_path) != before

"""Plain-English glossary: maps each engine signal `name` to a friendly title and
a one-sentence layman explanation. Used to explain jargon inline for non-experts.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlainTerm:
    title: str
    layman: str


# Every name the engine can emit (see analysis/*). Keep in sync with detectors.
TERMS: dict[str, PlainTerm] = {
    "trend": PlainTerm(
        "Overall trend",
        "The general direction the price has been heading (up, down, or sideways)."),
    "near_support": PlainTerm(
        "Near a support level",
        "Price is sitting on a 'floor' where buyers have stepped in before — it often bounces here."),
    "near_resistance": PlainTerm(
        "Near a resistance level",
        "Price is bumping into a 'ceiling' where sellers have appeared before — it often stalls here."),
    "uptrend_line_break": PlainTerm(
        "Broke its rising support line",
        "Price fell below the upward line it had been respecting — an early warning the up-move may be ending."),
    "downtrend_line_break": PlainTerm(
        "Broke its falling resistance line",
        "Price pushed above the downward line that had been capping it — an early sign the slide may be ending."),
    "golden_cross": PlainTerm(
        "Golden cross",
        "The ~2.5-month average price crossed above the ~10-month average — often an early sign of a lasting upturn."),
    "death_cross": PlainTerm(
        "Death cross",
        "The ~2.5-month average crossed below the ~10-month average — often an early sign of a lasting downturn."),
    "rsi_overbought": PlainTerm(
        "Overbought (RSI high)",
        "The stock has risen fast and may be 'stretched' — a pause or pullback is more likely."),
    "rsi_oversold": PlainTerm(
        "Oversold (RSI low)",
        "The stock has fallen fast and may be 'oversold' — a bounce is more likely."),
    "rsi_bearish_divergence": PlainTerm(
        "Momentum fading at the highs",
        "Price made a new high but momentum didn't — buyers may be losing steam (possible top)."),
    "rsi_bullish_divergence": PlainTerm(
        "Momentum improving at the lows",
        "Price made a new low but momentum didn't — sellers may be losing steam (possible bottom)."),
    "macd_bull_cross": PlainTerm(
        "Momentum turned up (MACD)",
        "A momentum gauge just flipped positive — short-term pressure is shifting upward."),
    "macd_bear_cross": PlainTerm(
        "Momentum turned down (MACD)",
        "A momentum gauge just flipped negative — short-term pressure is shifting downward."),
    "stoch_bull": PlainTerm(
        "Turning up from oversold",
        "A short-term timing gauge is curling up from a low zone — a near-term bounce signal."),
    "stoch_bear": PlainTerm(
        "Turning down from overbought",
        "A short-term timing gauge is curling down from a high zone — a near-term pullback signal."),
    "volume_spike": PlainTerm(
        "Big volume",
        "Unusually heavy trading — when it lines up with the move's direction, the move is more believable."),
    "doji": PlainTerm(
        "Indecision candle",
        "Buyers and sellers ended in a tie for the period — the market is undecided."),
    "hammer": PlainTerm(
        "Hammer candle",
        "Price dropped then recovered to close near the top — buyers fought back (possible bottom)."),
    "shooting_star": PlainTerm(
        "Shooting-star candle",
        "Price popped then faded to close near the bottom — sellers took over (possible top)."),
    "bullish_engulfing": PlainTerm(
        "Bullish engulfing",
        "A strong up day completely swallowed the prior down day — buyers took control."),
    "bearish_engulfing": PlainTerm(
        "Bearish engulfing",
        "A strong down day completely swallowed the prior up day — sellers took control."),
    "harami": PlainTerm(
        "Harami (momentum stalling)",
        "A small candle inside the previous big one — the prior move is losing steam."),
    "double_top": PlainTerm(
        "Double top",
        "Price hit about the same high twice and failed — a classic warning of a reversal down."),
    "double_bottom": PlainTerm(
        "Double bottom",
        "Price hit about the same low twice and held — a classic sign of a reversal up."),
    "head_and_shoulders": PlainTerm(
        "Head & shoulders",
        "A three-peak topping pattern that broke its neckline — a well-known reversal-down signal."),
    "inverse_head_and_shoulders": PlainTerm(
        "Inverse head & shoulders",
        "A three-trough bottoming pattern that broke its neckline — a well-known reversal-up signal."),
    "premarket_gap_up": PlainTerm(
        "Pre-market gap up",
        "Before the open, the price is trading above yesterday's close — buyers are early; the open may start higher."),
    "premarket_gap_down": PlainTerm(
        "Pre-market gap down",
        "Before the open, the price is trading below yesterday's close — sellers are early; the open may start lower."),
    "afterhours_move_up": PlainTerm(
        "After-hours move up",
        "After the close, the price is trading higher (often on news) — could carry into tomorrow."),
    "afterhours_move_down": PlainTerm(
        "After-hours move down",
        "After the close, the price is trading lower (often on news) — could carry into tomorrow."),
}

_FALLBACK = PlainTerm("Technical signal", "A technical pattern detected on the chart.")


def explain_signal(name: str) -> PlainTerm:
    return TERMS.get(name, _FALLBACK)

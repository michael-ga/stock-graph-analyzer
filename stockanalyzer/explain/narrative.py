"""Compose plain-English narrative from the engine outputs (no AI).

Combines the overall verdict, the big-picture vs short-term trend, the strongest
signals (translated via the glossary), and one scenario sentence.
"""
from __future__ import annotations

from ..analysis.engine import TimeframeReport
from ..analysis.signals import Direction
from ..data.schema import Timeframe
from .glossary import explain_signal

_LONG = {Timeframe.M6, Timeframe.YTD, Timeframe.Y1, Timeframe.Y5}
_SHORT = {Timeframe.D1, Timeframe.D5, Timeframe.M1}

_DIR_WORD = {Direction.BULL: "upward", Direction.BEAR: "downward", Direction.NEUTRAL: "sideways"}


def _group_bias(reports: dict[Timeframe, TimeframeReport], group: set) -> float:
    vals = [r.bias_score for tf, r in reports.items() if tf in group]
    return sum(vals) / len(vals) if vals else 0.0


def _word(bias: float) -> str:
    if bias > 0.15:
        return "upward"
    if bias < -0.15:
        return "downward"
    return "sideways"


def top_reasons(reports: dict[Timeframe, TimeframeReport], n: int = 3) -> list:
    """Strongest non-trend signals across timeframes, deduped by name."""
    best: dict[str, object] = {}
    for rep in reports.values():
        for s in rep.signals:
            if s.name == "trend":
                continue
            if s.name not in best or s.strength > best[s.name].strength:
                best[s.name] = s
    return sorted(best.values(), key=lambda s: s.strength, reverse=True)[:n]


def overall_summary(
    ticker: str,
    verdict,
    reports: dict[Timeframe, TimeframeReport],
    preset_label: str,
    bullish_pct: int,
    scenario_sentence: str = "",
) -> str:
    long_bias = _group_bias(reports, _LONG)
    short_bias = _group_bias(reports, _SHORT)

    parts = [
        f"Overall, {ticker} looks **{preset_label}** ({bullish_pct}% bullish on our scale)."
    ]
    parts.append(
        f"The bigger picture (6 months–5 years) is **{_word(long_bias)}**, "
        f"and the short term (days–weeks) is **{_word(short_bias)}**."
    )

    reasons = top_reasons(reports, 3)
    if reasons:
        bits = []
        for s in reasons:
            term = explain_signal(s.name)
            arrow = {Direction.BULL: "👍", Direction.BEAR: "👎", Direction.NEUTRAL: "•"}[s.direction]
            bits.append(f"{arrow} {term.title} — {term.layman}")
        parts.append("Main things driving this: " + "  ".join(bits))

    conf = verdict.confidence
    conf_word = "high" if conf >= 0.6 else "moderate" if conf >= 0.35 else "low"
    parts.append(f"Confidence is **{conf_word}** ({conf:.0%}) based on how much the timeframes agree.")

    if scenario_sentence:
        parts.append(scenario_sentence)
    return "\n\n".join(parts)


def timeframe_caption(tf_value: str, report: TimeframeReport) -> str:
    """One-line plain summary for a timeframe tab."""
    bias = report.bias_score
    word = _word(bias)
    cap = f"Over **{tf_value}**, the price is leaning **{word}**"
    tc = report.trend_change
    if tc.likely:
        cap += f" — but watch for a possible turn {_DIR_WORD[tc.direction]} (signal {tc.score:.0%})."
    else:
        cap += "."
    return cap

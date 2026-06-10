"""Multi-timeframe synthesis → an explained verdict.

Top-down weighting (Murphy): longer timeframes define the primary trend and get
more weight; shorter ones refine timing. Phase 2 will blend in analyst/news
sentiment via the optional ``sentiment_score`` argument.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis.engine import TimeframeReport
from ..analysis.signals import Direction
from ..data.schema import Timeframe
from ..strategy import Strategy, SwingPace

# Longer ranges carry more weight in the overall read (top-down, per Murphy).
TIMEFRAME_WEIGHTS: dict[Timeframe, float] = {
    Timeframe.D1: 0.5,
    Timeframe.D5: 0.7,
    Timeframe.M1: 1.0,
    Timeframe.YTD: 1.2,
    Timeframe.M6: 1.3,
    Timeframe.Y1: 1.5,
    Timeframe.Y5: 1.6,
}

# Swing trading flips the emphasis: the short timeframes drive the decision.
SWING_TIMEFRAME_WEIGHTS: dict[Timeframe, float] = {
    Timeframe.D1: 1.5,
    Timeframe.D5: 1.4,
    Timeframe.M1: 1.3,
    Timeframe.YTD: 0.8,
    Timeframe.M6: 0.7,
    Timeframe.Y1: 0.5,
    Timeframe.Y5: 0.4,
}

# Fast swing (1–3 days) leans even harder on the 1D/5D charts.
SWING_FAST_TIMEFRAME_WEIGHTS: dict[Timeframe, float] = {
    Timeframe.D1: 1.7,
    Timeframe.D5: 1.5,
    Timeframe.M1: 0.9,
    Timeframe.YTD: 0.6,
    Timeframe.M6: 0.5,
    Timeframe.Y1: 0.4,
    Timeframe.Y5: 0.3,
}


def _weights(strategy: Strategy, pace: SwingPace) -> dict[Timeframe, float]:
    if strategy != Strategy.SWING:
        return TIMEFRAME_WEIGHTS
    return SWING_FAST_TIMEFRAME_WEIGHTS if pace == SwingPace.FAST else SWING_TIMEFRAME_WEIGHTS


@dataclass
class Verdict:
    label: str                 # "Buy" / "Sell" / "Hold" lean
    direction: Direction
    score: float               # -1..+1
    confidence: float          # 0..1
    explanation: list[str] = field(default_factory=list)
    per_timeframe: dict[str, float] = field(default_factory=dict)


def build_verdict(
    reports: dict[Timeframe, TimeframeReport],
    sentiment_score: float | None = None,
    strategy: Strategy = Strategy.INVESTOR,
    pace: SwingPace = SwingPace.STANDARD,
) -> Verdict:
    if not reports:
        return Verdict("Hold", Direction.NEUTRAL, 0.0, 0.0, ["No data."])

    weights = _weights(strategy, pace)
    weighted_sum = 0.0
    weight_total = 0.0
    per_tf: dict[str, float] = {}
    for tf, rep in reports.items():
        w = weights.get(tf, 1.0)
        weighted_sum += rep.bias_score * w
        weight_total += w
        per_tf[tf.value] = rep.bias_score

    tech_score = weighted_sum / weight_total if weight_total else 0.0

    # Blend sentiment (Phase 2+). Technicals dominate 70/30 until proven otherwise.
    if sentiment_score is not None:
        score = 0.7 * tech_score + 0.3 * sentiment_score
    else:
        score = tech_score

    score = round(max(-1.0, min(1.0, score)), 3)

    if score > 0.2:
        label, direction = "Buy (lean)", Direction.BULL
    elif score < -0.2:
        label, direction = "Sell (lean)", Direction.BEAR
    else:
        label, direction = "Hold / Neutral", Direction.NEUTRAL

    explanation = _explain(reports, score, direction)
    if sentiment_score is not None:
        explanation.insert(1, (
            f"Blended 70% technicals ({tech_score:+.2f}) + 30% "
            f"analyst/news sentiment ({sentiment_score:+.2f})."
        ))
    confidence = round(min(1.0, abs(score) + 0.2 * _agreement(per_tf)), 3)

    return Verdict(label, direction, score, confidence, explanation, per_tf)


def _agreement(per_tf: dict[str, float]) -> float:
    """Fraction of timeframes whose sign matches the majority sign."""
    signs = [1 if v > 0.15 else -1 if v < -0.15 else 0 for v in per_tf.values()]
    nonzero = [s for s in signs if s != 0]
    if not nonzero:
        return 0.0
    pos = sum(1 for s in nonzero if s > 0)
    neg = len(nonzero) - pos
    return max(pos, neg) / len(nonzero)


def _explain(reports, score, direction) -> list[str]:
    lines: list[str] = []
    lines.append(
        f"Overall technical bias scores {score:+.2f} "
        f"({direction.value}), weighting longer timeframes more heavily."
    )
    for tf, rep in reports.items():
        tc = rep.trend_change
        bias_word = ("bullish" if rep.bias_score > 0.15
                     else "bearish" if rep.bias_score < -0.15 else "mixed")
        bits = [f"structure {rep.trend.direction.value}, "
                f"combined signals {bias_word} ({rep.bias_score:+.2f})"]
        if tc.likely:
            bits.append(f"⚠ possible reversal toward {tc.direction.value} (score {tc.score:.2f})")
        lines.append(f"• {tf.value}: " + "; ".join(bits) + ".")
        for reason in (tc.reasons[:3] if tc.likely else []):
            lines.append(f"    – {reason}")
    return lines

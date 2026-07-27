"""Decision snapshots — the full context stored alongside every virtual trade.

Every position in the book carries *why* it was opened: the plan's checks and
reasons, the per-timeframe signals and indicator values, the verdict, the
recommendation. That's what makes post-hoc analysis (`store.algorithm_correctness`,
`losing_trade_patterns`) possible at all — without it a closed trade is just a
number with no testable hypothesis attached.

These builders are pure dicts with no UI dependency, so the live radar, the
bots, and the replay simulator all produce identically-shaped snapshots. That
shape matters: `store.trade_stats` groups by ``snapshot["setup"]`` and
``["score"]``, so a divergent builder silently degrades those groupings to "?".
"""
from __future__ import annotations

from stockanalyzer.analysis.orb import ORB_TARGET_R

# Indicator columns worth freezing at entry time (last non-NaN value of each).
_SNAP_INDICATORS = (
    "close", "sma20", "sma50", "sma200", "ema20",
    "rsi", "macd", "macd_signal", "macd_hist",
    "stoch_k", "stoch_d", "atr",
    "adx", "plus_di", "minus_di", "bb_upper", "bb_lower",
)


def plan_snapshot(plan, reports=None, verdict=None, rec=None) -> dict:
    """Full decision context stored with every virtual trade — signals,
    indicators, verdict, swing checks, recommendation — for post-hoc analysis."""
    snap = dict(
        score=plan.score, label=plan.score_label, setup=plan.setup,
        kind=plan.kind, rr=plan.rr, daily_atr_pct=plan.daily_atr_pct,
        guidance=plan.guidance,
        failed_checks=[c.name for c in plan.checks if c.weight and not c.ok and not c.na],
        checks=[dict(name=c.name, ok=c.ok, na=c.na, detail=c.detail, weight=c.weight)
                for c in plan.checks],
        reasons=list(plan.reasons),
        bias=plan.bias.value,
        confidence=getattr(plan, "confidence", None),
        entry_note=plan.entry_note,
        atr_source=plan.atr_source,
        fast_mover=plan.fast_mover,
    )
    if reports:
        tf_data = {}
        for tf, rep in reports.items():
            tf_key = tf.value if hasattr(tf, "value") else str(tf)
            sigs = [dict(name=s.name, direction=s.direction.value,
                         strength=round(s.strength, 3), category=s.category,
                         evidence=s.evidence)
                    for s in rep.signals]
            indicators = {}
            for col in _SNAP_INDICATORS:
                if col in rep.df.columns:
                    series = rep.df[col].dropna()
                    if not series.empty:
                        indicators[col] = round(float(series.iloc[-1]), 4)
            tf_data[tf_key] = dict(signals=sigs, bias_score=rep.bias_score,
                                   trend_dir=rep.trend.direction.value,
                                   indicators=indicators)
        snap["timeframes"] = tf_data
    if verdict:
        snap["verdict"] = dict(
            label=verdict.label, direction=verdict.direction.value,
            score=verdict.score, confidence=verdict.confidence,
            per_timeframe=verdict.per_timeframe)
    if rec:
        snap["recommendation"] = dict(
            go_score=rec.go_score, light_color=rec.light_color,
            preset=rec.preset.label if hasattr(rec.preset, "label") else str(rec.preset),
            bullish_pct=rec.bullish_pct)
    return snap


def gap_snapshot(orange: dict, rvol: float | None, entry: float, stop: float,
                 target: float) -> dict:
    """Minimal decision snapshot for a Gap-and-Go trade. The shared setup/kind +
    identical entry/stop/target make ``bot-gap-mgd`` and ``bot-gap-fixed`` collapse
    to one ``cohort_id`` (the A/B pairing)."""
    rr = round((target - entry) / (entry - stop), 1) if entry > stop else ORB_TARGET_R
    return dict(
        setup="gap_and_go_orb", kind="immediate", score=None, label="Gap-and-Go",
        rr=rr, daily_atr_pct=None,
        guidance=(f"ORB break of ${orange['high']:.2f} on "
                  f"{(rvol or 0):.1f}×vol — hard stop below OR-low ${orange['low']:.2f}, "
                  f"target ${target:.2f} (2R)."))


def reversal_snapshot(dec) -> dict:
    """Minimal decision snapshot for a reversal-at-support day trade — one row per
    variant so each risk profile is its own cohort in the trade book."""
    return dict(
        setup="reversal_support", kind="immediate", score=None,
        label=f"Reversal @ support ({dec.variant})", rr=dec.rr, daily_atr_pct=None,
        guidance=(f"{dec.variant}: {dec.reason} — hard stop ${dec.stop:.2f}, "
                  f"target ${dec.target:.2f}."))

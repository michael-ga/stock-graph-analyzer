"""The no-AI recommendation: verdict score → preset + conviction % + per-use-case
go/no-go traffic light + plain summary + scenarios + checklist.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis.engine import TimeframeReport
from ..data.schema import Timeframe
from ..strategy import Strategy, SwingPace
from .checklist import build_checklist
from .narrative import overall_summary
from .scenarios import Scenario, build_scenario
from .swing import SwingPlan, build_swing_plan
from .usecase import UseCase


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    color: str
    emoji: str


# Verdict-score buckets (score is -1..+1).
def preset_for(score: float) -> Preset:
    if score >= 0.5:
        return Preset("strong_buy", "Strong Buy signal", "#1b9e3e", "🟢🟢")
    if score >= 0.2:
        return Preset("lean_buy", "Leaning Buy", "#4caf50", "🟢")
    if score > -0.2:
        return Preset("neutral", "Neutral / Wait", "#9e9e9e", "⚪")
    if score > -0.5:
        return Preset("lean_sell", "Leaning Sell", "#ff9800", "🟠")
    return Preset("strong_sell", "Strong Sell signal", "#e53935", "🔴")


# Traffic-light labels per use-case for GREEN / YELLOW / RED.
_LIGHT_LABELS = {
    UseCase.BUY: ("Good entry zone", "Wait for confirmation", "Avoid / high risk"),
    UseCase.SELL: ("Good time to sell", "Hold off selling", "Don't sell yet"),
    UseCase.OWN: ("Keep holding", "Watch closely", "Consider trimming"),
}
_LIGHT_COLORS = ("#1b9e3e", "#f9a825", "#e53935")  # green, yellow, red


@dataclass
class Recommendation:
    preset: Preset
    bullish_pct: int                 # 0..100, how bullish overall
    go_score: int                    # 0..100, tailored to the use-case
    light_color: str
    light_label: str
    light_reasons: list[str] = field(default_factory=list)
    summary: str = ""
    watch_items: list[str] = field(default_factory=list)
    scenario: Scenario | None = None
    decision_timeframe: str = ""
    swing: SwingPlan | None = None
    strategy: Strategy = Strategy.INVESTOR


# Timeframe preference: investors decide on the long chart; standard swing on the
# ~1-month daily; fast swing (1–3 days) on the 5D/1D charts.
_DECISION_ORDER = {
    Strategy.INVESTOR: (Timeframe.Y1, Timeframe.M6, Timeframe.YTD, Timeframe.Y5,
                        Timeframe.M1, Timeframe.D5, Timeframe.D1),
    Strategy.SWING: (Timeframe.M1, Timeframe.D5, Timeframe.D1, Timeframe.YTD,
                     Timeframe.M6, Timeframe.Y1, Timeframe.Y5),
}
_SWING_FAST_ORDER = (Timeframe.D5, Timeframe.D1, Timeframe.M1, Timeframe.YTD,
                     Timeframe.M6, Timeframe.Y1, Timeframe.Y5)


def _decision_report(reports: dict[Timeframe, TimeframeReport],
                     strategy: Strategy, pace: SwingPace) -> tuple[str, TimeframeReport] | None:
    order = _DECISION_ORDER[strategy]
    if strategy == Strategy.SWING and pace == SwingPace.FAST:
        order = _SWING_FAST_ORDER
    for tf in order:
        if tf in reports:
            return tf.value, reports[tf]
    return None


def build_recommendation(
    ticker: str,
    verdict,
    reports: dict[Timeframe, TimeframeReport],
    usecase: UseCase,
    strategy: Strategy = Strategy.INVESTOR,
    pace: SwingPace = SwingPace.STANDARD,
    price_override: float | None = None,
    context: dict | None = None,
) -> Recommendation:
    preset = preset_for(verdict.score)
    bullish_pct = round((verdict.score + 1) / 2 * 100)
    go_score = bullish_pct if usecase in (UseCase.BUY, UseCase.OWN) else 100 - bullish_pct

    dec = _decision_report(reports, strategy, pace)
    scenario = build_scenario(dec[1]) if dec else None
    decision_tf = dec[0] if dec else ""

    swing_plan = None
    if strategy == Strategy.SWING and dec:
        # price_override (live tick) recomputes entry/stop/target against the
        # streaming price; structural levels & daily ATR come from the reports.
        # Context adds the investor conviction (conflict guard) + earnings/Street.
        from ..verdict.aggregate import build_verdict as _bv
        ctx = dict(context or {})
        ctx.setdefault("investor_pct",
                       round((_bv(reports).score + 1) / 2 * 100))
        swing_plan = build_swing_plan(dec[1], usecase, pace,
                                      price_override=price_override,
                                      all_reports=reports, context=ctx)

    if swing_plan is not None:
        # Swing mode: the traffic light is driven by the honest plan kind.
        if swing_plan.light == "go":
            light_color = _LIGHT_COLORS[0]
            light_label = f"Take the swing — R:R {swing_plan.rr:.1f}:1"
        elif swing_plan.kind == "breakout_wait":
            light_color = _LIGHT_COLORS[1]
            light_label = (f"⏳ Wait for breakout — ${swing_plan.trigger:.2f}"
                           if swing_plan.trigger else "⏳ Wait for the breakout")
        elif swing_plan.light == "forming":
            light_color = _LIGHT_COLORS[1]
            light_label = "Setup forming — wait for the trigger"
        else:
            light_color = _LIGHT_COLORS[2]
            light_label = "No swing — watch the levels"
        light_reasons = swing_plan.reasons
        watch = swing_plan.reasons
    else:
        green, yellow, red = _LIGHT_LABELS[usecase]
        if go_score >= 65:
            light_color, light_label, idx = _LIGHT_COLORS[0], green, 0
        elif go_score >= 45:
            light_color, light_label, idx = _LIGHT_COLORS[1], yellow, 1
        else:
            light_color, light_label, idx = _LIGHT_COLORS[2], red, 2
        light_reasons = [f"Go-score {go_score}% for '{usecase.value}' "
                         f"({'≥65 → green' if idx == 0 else '45–64 → yellow' if idx == 1 else '<45 → red'})."]
        if verdict.confidence < 0.35:
            light_reasons.append("Signals are mixed across timeframes — treat this as low confidence.")
        watch = build_checklist(dec[1], usecase) if dec else []

    scen_sentence = ""
    if scenario and scenario.ordered:
        scen_sentence = "Looking ahead: " + scenario.ordered[0]
    summary = overall_summary(ticker, verdict, reports, preset.label, bullish_pct, scen_sentence)
    if strategy == Strategy.SWING:
        horizon = swing_plan.horizon if swing_plan else "days→~2 weeks"
        summary += (f"\n\n_Swing view ({pace.label}): horizon {horizon}; decisions are "
                    "weighted to the short timeframes._")

    return Recommendation(
        preset=preset,
        bullish_pct=bullish_pct,
        go_score=go_score,
        light_color=light_color,
        light_label=light_label,
        light_reasons=light_reasons,
        summary=summary,
        watch_items=watch,
        scenario=scenario,
        decision_timeframe=decision_tf,
        swing=swing_plan,
        strategy=strategy,
    )

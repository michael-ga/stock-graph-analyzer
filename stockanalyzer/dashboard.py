"""Serialize prepared market results for a fast dashboard, without candle arrays."""
from __future__ import annotations

import json

from stockanalyzer.version import app_version


def snapshot_result(result, plan) -> dict:
    quote = getattr(result, "quote", None)
    fields = ("go", "kind", "light", "entry", "stop", "target1", "rr", "trigger",
              "setup", "score", "daily_atr_pct", "guidance")
    plan_data = {key: getattr(plan, key, None) for key in fields} if plan else None
    trends = []
    for timeframe, report in result.reports.items():
        trends.append({
            "timeframe": timeframe.value, "direction": report.trend.direction.value,
            "bull": sum(s.direction.value == "bullish" for s in report.signals if s.name != "trend"),
            "bear": sum(s.direction.value == "bearish" for s in report.signals if s.name != "trend"),
        })
    payload = {
        "plan": plan_data, "provider": result.provider,
        "app_version": app_version(),
        "quote": {"price": quote.price, "day_change_pct": quote.day_change_pct,
                  "session": quote.session} if quote else None,
        "trends": trends,
    }
    # Some indicator/plan scalars are NumPy types. Store plain JSON numbers/bools.
    return json.loads(json.dumps(payload, default=lambda value: value.item(), allow_nan=False))

from __future__ import annotations
import csv
import io
import json
from datetime import datetime
from sqlalchemy import select
from stockanalyzer.db.models import AnalysisRun, AnalysisTimeframe
from stockanalyzer.db.session import session_scope


class AnalysisHistoryRepository:
    def save(self, user_id: str, *, ticker: str, provider: str, verdict: dict,
             timeframes: list[dict], started_at: datetime, completed_at: datetime,
             idempotency_key: str, strategy: str | None = None,
             swing_pace: str | None = None, use_case: str | None = None,
             quote: dict | None = None, sentiment: dict | None = None,
             errors: dict | None = None, notices: list | None = None) -> str:
        with session_scope() as s:
            existing = s.scalar(select(AnalysisRun).where(AnalysisRun.user_id == user_id,
                                                          AnalysisRun.idempotency_key == idempotency_key))
            if existing:
                return existing.id
            run = AnalysisRun(user_id=user_id, idempotency_key=idempotency_key,
                              ticker=ticker.strip().upper(), provider=provider,
                              strategy=strategy, swing_pace=swing_pace, use_case=use_case,
                              quote=quote, sentiment=sentiment, verdict=verdict,
                              errors=errors or {}, notices=notices or [],
                              started_at=started_at, completed_at=completed_at)
            for item in timeframes:
                run.timeframes.append(AnalysisTimeframe(
                    timeframe=item["timeframe"], bias_score=float(item["bias_score"]),
                    trend_direction=item["trend_direction"], last_close=item.get("last_close"),
                    bar_count=int(item["bar_count"]), signals=item.get("signals", []),
                    levels=item.get("levels", {}), trendlines=item.get("trendlines", {}),
                    trend_change=item.get("trend_change", {})))
            s.add(run); s.flush(); return run.id

    def query(self, user_id: str, *, ticker: str | None = None,
              provider: str | None = None, timeframe: str | None = None,
              strategy: str | None = None, start: datetime | None = None,
              end: datetime | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        limit = max(1, min(int(limit), 500)); offset = max(0, int(offset))
        stmt = select(AnalysisRun).where(AnalysisRun.user_id == user_id)
        if ticker: stmt = stmt.where(AnalysisRun.ticker == ticker.strip().upper())
        if provider: stmt = stmt.where(AnalysisRun.provider == provider)
        if strategy: stmt = stmt.where(AnalysisRun.strategy == strategy)
        if start: stmt = stmt.where(AnalysisRun.completed_at >= start)
        if end: stmt = stmt.where(AnalysisRun.completed_at <= end)
        if timeframe:
            stmt = stmt.join(AnalysisTimeframe).where(AnalysisTimeframe.timeframe == timeframe)
        stmt = stmt.order_by(AnalysisRun.completed_at.desc()).limit(limit).offset(offset)
        with session_scope() as s:
            runs = list(s.scalars(stmt).unique())
            return [{"id": r.id, "ticker": r.ticker, "provider": r.provider,
                     "strategy": r.strategy, "completed_at": r.completed_at.isoformat(),
                     "verdict": r.verdict,
                     "timeframes": [{"timeframe": t.timeframe, "bias_score": t.bias_score,
                                      "trend_direction": t.trend_direction, "last_close": t.last_close,
                                      "bar_count": t.bar_count} for t in r.timeframes]} for r in runs]

    def get_detail(self, user_id: str, analysis_id: str) -> dict | None:
        with session_scope() as session:
            run = session.scalar(
                select(AnalysisRun).where(
                    AnalysisRun.id == analysis_id,
                    AnalysisRun.user_id == user_id,
                )
            )
            if run is None:
                return None
            return {
                "id": run.id,
                "ticker": run.ticker,
                "provider": run.provider,
                "strategy": run.strategy,
                "swing_pace": run.swing_pace,
                "use_case": run.use_case,
                "quote": run.quote,
                "sentiment": run.sentiment,
                "verdict": run.verdict,
                "errors": run.errors,
                "notices": run.notices,
                "started_at": run.started_at.isoformat(),
                "completed_at": run.completed_at.isoformat(),
                "timeframes": [
                    {
                        "timeframe": item.timeframe,
                        "bias_score": item.bias_score,
                        "trend_direction": item.trend_direction,
                        "last_close": item.last_close,
                        "bar_count": item.bar_count,
                        "signals": item.signals,
                        "levels": item.levels,
                        "trendlines": item.trendlines,
                        "trend_change": item.trend_change,
                    }
                    for item in run.timeframes
                ],
            }

    @staticmethod
    def to_csv(rows: list[dict]) -> str:
        fields = ["id", "ticker", "provider", "strategy", "completed_at", "verdict"]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            rendered = {}
            for field in fields:
                value = json.dumps(row[field], separators=(",", ":")) if field == "verdict" else row.get(field)
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                    value = "'" + value
                rendered[field] = value
            writer.writerow(rendered)
        return output.getvalue()

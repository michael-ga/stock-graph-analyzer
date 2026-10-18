"""Unattended, user-scoped radar worker.

The Streamlit UI configures persisted radar membership. This module executes those
radars independently so scans and virtual bots continue without a browser session.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Protocol

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from stockanalyzer import session as market_session, swingwatch, virtualbook
from stockanalyzer.analysis import orb, reversal
from stockanalyzer.analysis.daycard import _intraday_frame, build_day_card
from stockanalyzer.data.schema import Timeframe
from stockanalyzer.explain import UseCase
from stockanalyzer.explain.swing import build_swing_plan
from stockanalyzer.strategy import SwingPace
from stockanalyzer.verdict.aggregate import build_verdict
from stockanalyzer.db.repositories.paper_trades import PaperTradeRepository
from stockanalyzer.db.session import transaction_scope

logger = logging.getLogger(__name__)
_LEASE_NAME = "stockanalyzer-radar-worker"


@contextmanager
def worker_lease(engine) -> Iterator[bool]:
    """Hold a PostgreSQL session advisory lock for one worker process."""
    if engine.dialect.name != "postgresql":
        yield True
        return
    connection = engine.connect()
    acquired = False
    try:
        acquired = bool(connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtext(:name))"),
            {"name": _LEASE_NAME},
        ))
        yield acquired
    finally:
        if acquired:
            connection.execute(
                text("SELECT pg_advisory_unlock(hashtext(:name))"),
                {"name": _LEASE_NAME},
            )
        connection.close()


class AssignmentRepository(Protocol):
    def list_assignments(self) -> list[tuple[str, str]]: ...


@dataclass(frozen=True)
class CycleReport:
    assignments: int
    tickers_analyzed: int
    failures: int


@dataclass(frozen=True)
class ProcessReport:
    notice_level: int | None
    paper_recorded: bool
    opened: int
    changed: int


def write_heartbeat(path: Path, *, now: float | None = None) -> None:
    """Atomically record worker liveness for the container health check."""
    timestamp = time.time() if now is None else float(now)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(f"{timestamp}\n", encoding="utf-8")
    temporary.replace(path)


def heartbeat_is_healthy(
    path: Path, *, now: float | None = None, max_age: float = 120.0
) -> bool:
    try:
        timestamp = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    current = time.time() if now is None else float(now)
    return 0 <= current - timestamp <= max_age


@contextmanager
def heartbeat_writer(
    path: Path,
    stop: threading.Event,
    *,
    interval: float = 60.0,
    write: Callable[[Path], None] = write_heartbeat,
) -> Iterator[threading.Thread]:
    """Write heartbeats until the worker's lifecycle stop event is set."""
    def run() -> None:
        write(path)
        while not stop.wait(interval):
            write(path)

    thread = threading.Thread(target=run, name="radar-heartbeat", daemon=True)
    thread.start()
    try:
        yield thread
    finally:
        stop.set()
        thread.join()


def run_cycle(
    repository: AssignmentRepository,
    *,
    analyze: Callable[[str], object],
    process: Callable[[str, str, object, datetime], object],
    asof: datetime,
) -> CycleReport:
    """Analyze each unique ticker once, then apply it to every owning user."""
    assignments = repository.list_assignments()
    results: dict[str, object] = {}
    failures = 0
    for _user_id, ticker in assignments:
        if ticker in results:
            continue
        try:
            results[ticker] = analyze(ticker)
        except Exception:
            failures += 1
            logger.exception("radar analysis failed ticker=%s", ticker)
    for user_id, ticker in assignments:
        if ticker not in results:
            continue
        try:
            process(user_id, ticker, results[ticker], asof)
        except Exception:
            failures += 1
            logger.exception("radar processing failed user=%s ticker=%s", user_id, ticker)
    return CycleReport(len(assignments), len(results), failures)


def _decision_report(result):
    for timeframe in (Timeframe.D5, Timeframe.D1, Timeframe.M1):
        report = result.reports.get(timeframe)
        if report is not None:
            return report
    return None


def _sentiment_score(result) -> float | None:
    sentiment = getattr(result, "sentiment", None)
    return sentiment.score if sentiment is not None and sentiment.available else None


def build_radar_plan(result):
    report = _decision_report(result)
    if report is None:
        return None
    sentiment = _sentiment_score(result)
    investor_pct = round((build_verdict(result.reports, sentiment).score + 1) / 2 * 100)
    return build_swing_plan(
        report,
        UseCase.BUY,
        SwingPace.FAST,
        all_reports=result.reports,
        context={"investor_pct": investor_pct, "sentiment": sentiment},
    )


def _price_of(result) -> float | None:
    quote = getattr(result, "quote", None)
    if quote is not None and quote.price:
        return float(quote.price)
    for timeframe in (Timeframe.D1, Timeframe.D5, Timeframe.M1):
        report = result.reports.get(timeframe)
        if report is not None:
            price = float(report.meta.get("last_close", 0))
            if price:
                return price
    return None


def opening_range(result, asof: datetime):
    frame = _intraday_frame(getattr(result, "reports", None))
    quote = getattr(result, "quote", None)
    previous_close = quote.prev_close if quote is not None else None
    return orb.opening_range(frame, previous_close, asof)


def _is_extended(plan) -> bool:
    emerging = getattr(plan, "emerging", None)
    return bool(emerging and (
        emerging.extended or "distribution_risk" in emerging.flags
    ))


def _plan_snapshot(plan, reports=None) -> dict:
    snapshot = {
        "score": plan.score,
        "label": plan.score_label,
        "setup": plan.setup,
        "kind": plan.kind,
        "rr": plan.rr,
        "daily_atr_pct": plan.daily_atr_pct,
        "guidance": plan.guidance,
        "failed_checks": [
            check.name for check in plan.checks
            if check.weight and not check.ok and not check.na
        ],
        "checks": [
            {
                "name": check.name,
                "ok": check.ok,
                "na": check.na,
                "detail": check.detail,
                "weight": check.weight,
            }
            for check in plan.checks
        ],
        "reasons": list(plan.reasons),
        "bias": plan.bias.value,
        "confidence": getattr(plan, "confidence", None),
        "entry_note": plan.entry_note,
        "atr_source": plan.atr_source,
        "fast_mover": plan.fast_mover,
    }
    if reports:
        timeframe_data = {}
        for timeframe, report in reports.items():
            key = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
            signals = [
                {
                    "name": signal.name,
                    "direction": signal.direction.value,
                    "strength": round(signal.strength, 3),
                    "category": signal.category,
                    "evidence": signal.evidence,
                }
                for signal in report.signals
            ]
            indicators = {}
            for column in (
                "close", "sma20", "sma50", "sma200", "ema20", "rsi", "macd",
                "macd_signal", "macd_hist", "stoch_k", "stoch_d", "atr", "adx",
                "plus_di", "minus_di", "bb_upper", "bb_lower",
            ):
                if column in report.df.columns:
                    series = report.df[column].dropna()
                    if not series.empty:
                        indicators[column] = round(float(series.iloc[-1]), 4)
            timeframe_data[key] = {
                "signals": signals,
                "bias_score": report.bias_score,
                "trend_dir": report.trend.direction.value,
                "indicators": indicators,
            }
        snapshot["timeframes"] = timeframe_data
    return snapshot


def _gap_snapshot(orange, decision) -> dict:
    return {
        "setup": "gap_and_go_orb",
        "kind": "immediate",
        "score": None,
        "label": "Gap-and-Go",
        "rr": decision.rr,
        "daily_atr_pct": None,
        "guidance": decision.reason,
    }


def _open_position(user_id: str, **kwargs) -> dict | None:
    ticker = kwargs["ticker"]
    trader = kwargs["trader"]
    if not swingwatch.assignment_is_active(ticker, user_id=user_id):
        return None
    if virtualbook.has_open(ticker, trader, user_id=user_id):
        return None
    with transaction_scope(join_existing=True) as session:
        try:
            with session.begin_nested():
                trade = virtualbook.open_position(user_id=user_id, **kwargs)
                session.flush()
                return trade
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            sqlite_duplicate = (
                "UNIQUE constraint failed: trades.user_id, trades.ticker, trades.trader"
                in str(exc.orig)
            )
            if constraint != "uq_trade_active_owner_ticker_trader" and not sqlite_duplicate:
                raise
            # The partial unique index is the final duplicate guard if the UI and
            # worker race between has_open() and the candidate flush.
            logger.info(
                "radar bot open raced with active position user=%s ticker=%s trader=%s",
                user_id, ticker, trader,
            )
            return None


def claim_notice_and_record(
    user_id: str, ticker: str, score: int | float, record: dict
) -> tuple[tuple[int, str] | None, bool]:
    """Claim a notice and append its journal row in one transaction."""
    with transaction_scope(join_existing=True):
        if not swingwatch.assignment_is_active(ticker, user_id=user_id):
            return None, False
        notice = swingwatch.claim_notice(ticker, score, user_id=user_id)
        if notice is None:
            return None, False
        payload = {**record, "level": notice[0]}
        payload.setdefault(
            "source_id", f"radar:{ticker}:{notice[0]}:{str(payload.get('date', ''))[:10]}"
        )
        return notice, PaperTradeRepository().insert(user_id, payload)


def run_automatic_bots(user_id: str, ticker: str, plan, result, orange, asof) -> list[dict]:
    """Run swing and Gap-and-Go bots for one persisted radar owner."""
    if market_session.market_phase(asof) == "opening_range":
        return []
    opened: list[dict] = []
    price = _price_of(result)
    reports = result.reports

    if orange is not None and price and reports:
        decision = orb.gap_and_go_signal(
            orange, _intraday_frame(reports), price, asof
        )
        if decision.fired:
            for trader, managed in (("bot-gap-fixed", False), ("bot-gap-mgd", True)):
                trade = _open_position(
                    user_id,
                    ticker=ticker,
                    trader=trader,
                    entry=decision.entry,
                    stop=decision.stop,
                    target=decision.target,
                    kind="immediate",
                    horizon_days=1,
                    managed=managed,
                    entry_rvol=decision.rvol,
                    init_stop=decision.stop,
                    snapshot=_gap_snapshot(orange, decision),
                )
                if trade is not None:
                    opened.append(trade)

    if not getattr(plan, "actionable", True) or _is_extended(plan):
        return opened
    rules = (
        ("bot-GO", lambda candidate: candidate.go),
        ("bot-70", lambda candidate: (
            candidate.score >= 70
            and candidate.kind == "immediate"
            and not candidate.go
            and candidate.actionable
            and not _is_extended(candidate)
        )),
        ("bot-BRK", lambda candidate: (
            candidate.kind == "breakout_wait" and candidate.score >= 55
        )),
    )
    for trader, condition in rules:
        if not condition(plan):
            continue
        trade = _open_position(
            user_id,
            ticker=ticker,
            trader=trader,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target1,
            kind=plan.kind,
            trigger=plan.trigger,
            horizon_days=3,
            snapshot=_plan_snapshot(plan, reports),
        )
        if trade is not None:
            opened.append(trade)
    return opened


def _reversal_snapshot(decision) -> dict:
    return {
        "setup": "reversal_support",
        "kind": "immediate",
        "score": None,
        "label": f"Reversal @ support ({decision.variant})",
        "rr": decision.rr,
        "daily_atr_pct": None,
        "guidance": decision.reason,
    }


def run_reversal_bots(user_id: str, ticker: str, result, asof) -> list[dict]:
    """Run every reversal-at-support variant for one radar owner."""
    if market_session.market_phase(asof) == "opening_range":
        return []
    price = _price_of(result)
    if result is None or not price:
        return []
    reports = result.reports
    card = build_day_card(reports, price)
    if card is None:
        return []
    higher_trend = None
    for timeframe in (Timeframe.M1, Timeframe.D5, Timeframe.M6):
        report = reports.get(timeframe)
        if report is not None:
            higher_trend = report.trend.direction
            break
    quote = getattr(result, "quote", None)
    previous_close = quote.prev_close if quote is not None else None
    frame = _intraday_frame(reports)
    opened = []
    for config in reversal.REVERSAL_VARIANTS:
        decision = reversal.reversal_signal(
            card, frame, price, higher_trend, previous_close, asof, config
        )
        if not decision.fired:
            continue
        trade = _open_position(
            user_id,
            ticker=ticker,
            trader=decision.variant,
            entry=decision.entry,
            stop=decision.stop,
            target=decision.target,
            kind="immediate",
            horizon_days=1,
            snapshot=_reversal_snapshot(decision),
        )
        if trade is not None:
            opened.append(trade)
    return opened


def process_analysis(user_id: str, ticker: str, result, asof: datetime) -> ProcessReport:
    """Apply one shared market analysis to one radar owner, always explicitly scoped."""
    # This is only an inexpensive prefilter. The locked check below is authoritative.
    if not swingwatch.assignment_is_active(ticker, user_id=user_id):
        return ProcessReport(None, False, 0, 0)
    plan = build_radar_plan(result)
    if plan is None:
        return ProcessReport(None, False, 0, 0)
    orange = opening_range(result, asof)
    with transaction_scope() as session:
        if not swingwatch.lock_active_assignment(session, ticker, user_id=user_id):
            return ProcessReport(None, False, 0, 0)
        notice, recorded = claim_notice_and_record(
            user_id, ticker, plan.score,
            {
                "ts": asof.timestamp(),
                "date": asof.isoformat(),
                "ticker": ticker,
                "score": plan.score,
                "label": plan.score_label,
                "kind": plan.kind,
                "setup": plan.setup,
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target1,
                "rr": plan.rr,
                "trigger": plan.trigger,
                "horizon_days": 3,
                "guidance": plan.guidance,
                "status": "open",
                "result_pct": 0.0,
            },
        )
        opened = run_automatic_bots(user_id, ticker, plan, result, orange, asof)
        opened += run_reversal_bots(user_id, ticker, result, asof)
        price = _price_of(result)
        changed = []
        if price:
            decision = _decision_report(result)
            trend_change = getattr(decision, "trend_change", None)
            changed.extend(virtualbook.manage(
                ticker, price, result.reports, trend_change, user_id=user_id
            ))
            changed.extend(virtualbook.mark(ticker, price, user_id=user_id))
        return ProcessReport(
            notice[0] if notice else None, recorded, len(opened), len(changed)
        )


def _positive_seconds(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def run_forever() -> int:
    """Run persisted radars until SIGTERM/SIGINT, independent of Streamlit."""
    from stockanalyzer.config import Settings
    from stockanalyzer.db.repositories.swingwatch import SwingWatchRepository
    from stockanalyzer.db.session import configure_engine
    from stockanalyzer.pipeline import analyze_ticker

    settings = Settings.from_env()
    engine = configure_engine(settings.database_url)
    interval = _positive_seconds("RADAR_WORKER_INTERVAL_SECONDS", 30.0)
    heartbeat = Path(os.getenv(
        "RADAR_WORKER_HEARTBEAT", "/tmp/radar-worker-heartbeat"
    ))
    stop = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    repository = SwingWatchRepository()

    with worker_lease(engine) as acquired:
        if not acquired:
            logger.error("another radar worker already holds the database lease")
            return 2
        logger.info("radar worker started interval_seconds=%s", interval)
        with heartbeat_writer(heartbeat, stop):
            while not stop.is_set():
                asof = market_session.now_et().to_pydatetime()
                if market_session.market_phase(asof) != "closed":
                    report = run_cycle(
                        repository,
                        analyze=lambda ticker: analyze_ticker(
                            ticker, include_fundamentals=False, live_mode=True
                        ),
                        process=process_analysis,
                        asof=asof,
                    )
                    logger.info(
                        "radar cycle assignments=%s tickers=%s failures=%s",
                        report.assignments,
                        report.tickers_analyzed,
                        report.failures,
                    )
                stop.wait(interval)
    logger.info("radar worker stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run persisted user radars")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args(argv)
    heartbeat = Path(os.getenv(
        "RADAR_WORKER_HEARTBEAT", "/tmp/radar-worker-heartbeat"
    ))
    if args.healthcheck:
        try:
            max_age = _positive_seconds(
                "RADAR_WORKER_HEALTH_MAX_AGE_SECONDS", 180.0
            )
        except ValueError:
            return 1
        return 0 if heartbeat_is_healthy(heartbeat, max_age=max_age) else 1
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return run_forever()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from contextlib import contextmanager
import threading
import pytest

from stockanalyzer import swingwatch
from stockanalyzer.auth import AuthService
from stockanalyzer.db.models import Base
from stockanalyzer.db.repositories.paper_trades import PaperTradeRepository
from stockanalyzer.db.repositories.trades import TradeRepository
from stockanalyzer.db.session import transaction_scope
from stockanalyzer.db.session import configure_engine


@dataclass
class FakeRepository:
    assignments: list[tuple[str, str]]

    def list_assignments(self) -> list[tuple[str, str]]:
        return self.assignments


@pytest.fixture()
def db(tmp_path):
    engine = configure_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_cycle_scans_saved_radars_without_a_streamlit_session():
    from stockanalyzer.radar_worker import run_cycle

    analyzed: list[str] = []
    processed: list[tuple[str, str, object]] = []
    results = {"AAPL": object(), "MSFT": object()}

    def analyze(ticker: str):
        analyzed.append(ticker)
        return results[ticker]

    def process(user_id: str, ticker: str, result, asof):
        processed.append((user_id, ticker, result))

    report = run_cycle(
        FakeRepository([("user-a", "AAPL"), ("user-b", "AAPL"), ("user-b", "MSFT")]),
        analyze=analyze,
        process=process,
        asof=datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
    )

    assert analyzed == ["AAPL", "MSFT"]
    assert processed == [
        ("user-a", "AAPL", results["AAPL"]),
        ("user-b", "AAPL", results["AAPL"]),
        ("user-b", "MSFT", results["MSFT"]),
    ]
    assert report.assignments == 3
    assert report.tickers_analyzed == 2
    assert report.failures == 0


def test_process_analysis_scopes_all_persistence_to_radar_owner(monkeypatch):
    import stockanalyzer.radar_worker as worker

    owner = "owner-id"
    plan = NS(
        score=70, score_label="good", kind="immediate", setup="momentum",
        entry=100.0, stop=95.0, target1=110.0, rr=2.0, trigger=None,
        guidance="buy", actionable=True,
    )
    result = NS(reports={"frame": object()}, quote=NS(price=101.0))
    calls: list[tuple] = []

    monkeypatch.setattr(worker, "build_radar_plan", lambda _result: plan)
    monkeypatch.setattr(worker.swingwatch, "assignment_is_active", lambda ticker, *, user_id: True)
    monkeypatch.setattr(worker.swingwatch, "lock_active_assignment", lambda *_a, **_k: True)
    @contextmanager
    def transaction():
        yield object()
    monkeypatch.setattr(worker, "transaction_scope", transaction)
    monkeypatch.setattr(worker, "opening_range", lambda _result, _asof: None)
    monkeypatch.setattr(
        worker, "claim_notice_and_record",
        lambda user_id, ticker, score, record:
            (calls.append(("claim", user_id, ticker, score))
             or calls.append(("paper", user_id, record["ticker"]))
             or ((70, "notice"), True)),
    )
    monkeypatch.setattr(
        worker, "run_automatic_bots",
        lambda user_id, ticker, _plan, _result, _orange, _asof:
            calls.append(("bots", user_id, ticker)) or [],
    )
    monkeypatch.setattr(
        worker, "run_reversal_bots",
        lambda user_id, ticker, _result, _asof:
            calls.append(("reversal", user_id, ticker)) or [],
    )
    monkeypatch.setattr(
        worker.virtualbook, "manage",
        lambda ticker, price, reports, trend_change, *, user_id:
            calls.append(("manage", user_id, ticker, price)) or [],
    )
    monkeypatch.setattr(
        worker.virtualbook, "mark",
        lambda ticker, price, *, user_id:
            calls.append(("mark", user_id, ticker, price)) or [],
    )

    report = worker.process_analysis(
        owner, "AAPL", result, datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)
    )

    assert report.notice_level == 70
    assert all(call[1] == owner for call in calls)
    assert {call[0] for call in calls} == {
        "claim", "paper", "bots", "reversal", "manage", "mark"
    }


def test_removed_radar_is_not_processed_after_an_inflight_analysis(monkeypatch):
    import stockanalyzer.radar_worker as worker

    monkeypatch.setattr(
        worker.swingwatch, "assignment_is_active", lambda ticker, *, user_id: False
    )
    monkeypatch.setattr(
        worker, "build_radar_plan",
        lambda _result: (_ for _ in ()).throw(AssertionError("must not process")),
    )

    report = worker.process_analysis(
        "owner", "AAPL", object(),
        datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
    )

    assert report == worker.ProcessReport(None, False, 0, 0)


def test_disabled_user_is_revalidated_at_worker_write_boundary(monkeypatch):
    import stockanalyzer.radar_worker as worker

    monkeypatch.setattr(worker.swingwatch, "assignment_is_active", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        worker, "build_radar_plan",
        lambda _result: (_ for _ in ()).throw(AssertionError("must not process")),
    )
    report = worker.process_analysis(
        "owner", "AAPL", object(),
        datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
    )
    assert report == worker.ProcessReport(None, False, 0, 0)


def test_open_position_only_swallows_integrity_errors(monkeypatch):
    import pytest
    import stockanalyzer.radar_worker as worker

    monkeypatch.setattr(worker.virtualbook, "has_open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(worker.swingwatch, "assignment_is_active", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        worker.virtualbook, "open_position",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )
    with pytest.raises(RuntimeError, match="unexpected"):
        worker._open_position("owner", ticker="AAPL", trader="bot")


def test_duplicate_open_flush_isolated_from_outer_assignment_transaction(db, monkeypatch):
    import stockanalyzer.radar_worker as worker

    owner = AuthService().create_user("race-owner", "correct horse battery staple")
    repository = TradeRepository()

    def trade(trade_id: str) -> dict:
        return {
            "id": trade_id, "ticker": "AAPL", "trader": "bot-GO",
            "status": "open", "kind": "immediate", "opened_ts": 1.0,
            "opened": "2026-07-22 14:00", "entry": 100.0, "stop": 95.0,
            "target": 110.0,
        }

    repository.insert(owner.id, trade("existing"))
    monkeypatch.setattr(worker.swingwatch, "assignment_is_active", lambda *_a, **_k: True)
    monkeypatch.setattr(worker.virtualbook, "has_open", lambda *_a, **_k: False)
    monkeypatch.setattr(
        worker.virtualbook, "open_position",
        lambda **_kwargs: repository.insert(owner.id, trade("racing-candidate")),
    )

    with transaction_scope():
        assert worker._open_position(owner.id, ticker="AAPL", trader="bot-GO") is None
        assert PaperTradeRepository().insert(
            owner.id,
            {"source_id": "outer-survived", "ticker": "AAPL", "status": "open"},
        ) is True

    assert [row["id"] for row in repository.list(owner.id)] == ["existing"]
    assert len(PaperTradeRepository().list(owner.id)) == 1


def test_write_boundary_lock_failure_prevents_every_worker_mutation(monkeypatch):
    import stockanalyzer.radar_worker as worker

    plan = NS(score=70, score_label="good", kind="immediate", setup="momentum",
              entry=100.0, stop=95.0, target1=110.0, rr=2.0, trigger=None,
              guidance="buy", actionable=True)
    monkeypatch.setattr(worker.swingwatch, "assignment_is_active", lambda *_a, **_k: True)
    monkeypatch.setattr(worker.swingwatch, "lock_active_assignment", lambda *_a, **_k: False)
    @contextmanager
    def transaction():
        yield object()
    monkeypatch.setattr(worker, "transaction_scope", transaction)
    monkeypatch.setattr(worker, "build_radar_plan", lambda _result: plan)
    monkeypatch.setattr(worker, "opening_range", lambda *_args: None)
    monkeypatch.setattr(worker, "claim_notice_and_record",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no notice")))
    monkeypatch.setattr(worker, "run_automatic_bots",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no bot write")))

    report = worker.process_analysis(
        "owner", "AAPL", NS(reports={}, quote=None),
        datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
    )
    assert report == worker.ProcessReport(None, False, 0, 0)


def test_worker_write_phase_rolls_back_notice_and_paper_when_later_write_fails(db, monkeypatch):
    import stockanalyzer.radar_worker as worker

    owner = AuthService().create_user("owner", "correct horse battery staple")
    swingwatch.add("AAPL", user_id=owner.id)
    plan = NS(
        score=70, score_label="good", kind="immediate", setup="momentum",
        entry=100.0, stop=95.0, target1=110.0, rr=2.0, trigger=None,
        guidance="buy", actionable=True,
    )
    result = NS(reports={}, quote=NS(price=101.0))
    monkeypatch.setattr(worker, "build_radar_plan", lambda _result: plan)
    monkeypatch.setattr(worker, "opening_range", lambda *_args: None)
    monkeypatch.setattr(worker, "run_automatic_bots", lambda *_args: [])
    monkeypatch.setattr(worker, "run_reversal_bots", lambda *_args: [])
    monkeypatch.setattr(
        worker.virtualbook, "manage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("manage failed")),
    )

    with pytest.raises(RuntimeError, match="manage failed"):
        worker.process_analysis(
            owner.id, "AAPL", result,
            datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc),
        )

    assert swingwatch.get_notice_level("AAPL", user_id=owner.id) == 0
    assert PaperTradeRepository().list(owner.id) == []


def test_worker_heartbeat_reports_stale_or_missing_process(tmp_path):
    from stockanalyzer.radar_worker import heartbeat_is_healthy, write_heartbeat

    path = tmp_path / "radar-worker-heartbeat"
    assert heartbeat_is_healthy(path, now=100.0, max_age=60.0) is False
    write_heartbeat(path, now=100.0)
    assert heartbeat_is_healthy(path, now=159.9, max_age=60.0) is True
    assert heartbeat_is_healthy(path, now=160.1, max_age=60.0) is False


def test_heartbeat_advances_while_cycle_is_blocked_and_stops_cleanly(tmp_path):
    import stockanalyzer.radar_worker as worker

    writes = 0
    first_write = threading.Event()
    allow_tick = threading.Event()
    second_write = threading.Event()
    class ControlledStop:
        def __init__(self):
            self.stopped = threading.Event()

        def is_set(self):
            return self.stopped.is_set()

        def set(self):
            self.stopped.set()
            allow_tick.set()

        def wait(self, _interval):
            allow_tick.wait()
            allow_tick.clear()
            return self.stopped.is_set()

    stop = ControlledStop()

    def write(_path):
        nonlocal writes
        writes += 1
        (first_write if writes == 1 else second_write).set()

    with worker.heartbeat_writer(
        tmp_path / "heartbeat", stop, interval=60.0, write=write
    ) as thread:
        assert first_write.wait(timeout=1)
        allow_tick.set()
        assert second_write.wait(timeout=1)
        assert thread.is_alive()

    assert stop.is_set()
    assert not thread.is_alive()
    completed_writes = writes
    allow_tick.set()
    assert writes == completed_writes


def test_postgres_worker_lease_prevents_duplicate_worker_instances():
    from stockanalyzer.radar_worker import worker_lease

    calls: list[str] = []

    class Connection:
        def scalar(self, statement, parameters):
            calls.append(str(statement))
            return True

        def execute(self, statement, parameters):
            calls.append(str(statement))

        def close(self):
            calls.append("close")

    engine = NS(dialect=NS(name="postgresql"), connect=lambda: Connection())
    with worker_lease(engine) as acquired:
        assert acquired is True

    assert any("pg_try_advisory_lock" in call for call in calls)
    assert any("pg_advisory_unlock" in call for call in calls)
    assert calls[-1] == "close"


def test_healthcheck_cli_uses_worker_heartbeat(tmp_path, monkeypatch):
    from stockanalyzer.radar_worker import main, write_heartbeat

    path = tmp_path / "heartbeat"
    monkeypatch.setenv("RADAR_WORKER_HEARTBEAT", str(path))
    monkeypatch.setenv("RADAR_WORKER_HEALTH_MAX_AGE_SECONDS", "60")
    assert main(["--healthcheck"]) == 1
    write_heartbeat(path)
    assert main(["--healthcheck"]) == 0

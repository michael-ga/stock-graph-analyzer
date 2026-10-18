from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError

from stockanalyzer.auth import AuthService
from stockanalyzer.db.models import Base
from stockanalyzer.db.repositories.api_cache import ApiCacheRepository
from stockanalyzer.db.repositories.paper_trades import PaperTradeRepository
from stockanalyzer.db.repositories.trades import TradeRepository
from stockanalyzer.db.repositories.watchlist import WatchlistRepository
from stockanalyzer.db.session import configure_engine, transaction_scope
from stockanalyzer.migrations.legacy import read_symbol_file
from stockanalyzer.ui.history import _safe_csv


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'production-path.db'}")
    engine = configure_engine(os.environ["DATABASE_URL"])
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


def test_api_cache_is_provider_qualified(database):
    repo = ApiCacheRepository()
    repo.put("profile:MSFT", "finnhub", {"source": "f"}, ttl_seconds=60)
    repo.put("profile:MSFT", "other", {"source": "o"}, ttl_seconds=60)
    assert repo.get("profile:MSFT", provider="finnhub") == {"source": "f"}
    assert repo.get("profile:MSFT", provider="other") == {"source": "o"}


def test_trade_lifecycle_is_user_isolated_and_cannot_double_close(database):
    auth = AuthService()
    owner = auth.create_user("trade-owner", "correct horse battery staple")
    other = auth.create_user("trade-other", "another correct long password")
    repo = TradeRepository()
    trade = {"id": "abc123def0", "ticker": "msft", "trader": "me", "status": "open",
             "kind": "immediate", "opened_ts": 1000.0, "opened": "1970-01-01 00:16",
             "activated_ts": 1000.0, "entry": 100.0, "stop": 95.0, "target": 110.0,
             "stake": 1000.0, "shares": 10.0, "horizon_days": 3,
             "snapshot": {"score": 75, "setup": "test"}}
    repo.insert(owner.id, trade, trade["snapshot"])
    assert repo.list(other.id) == []
    closed = repo.close(owner.id, trade["id"], 110.0, "target_hit", now=1100.0)
    assert closed and closed["pnl_usd"] == 100.0
    assert repo.close(owner.id, trade["id"], 90.0, "manual", now=1200.0) is None


def test_trade_import_preserves_closed_lifecycle(database):
    owner = AuthService().create_user("legacy-owner", "correct horse battery staple")
    trade = {
        "id": "abc123def4", "ticker": "MSFT", "trader": "bot", "status": "closed",
        "kind": "market", "opened_ts": 1.0, "opened": "1970-01-01 00:00",
        "entry": 100.0, "stop": 95.0, "target": 110.0, "stake": 1000.0,
        "shares": 10.0, "horizon_days": 3, "exit_price": 110.0,
        "close_reason": "target_hit", "closed_ts": 2.0,
        "closed": "1970-01-01 00:00", "pnl_pct": 10.0, "pnl_usd": 100.0,
    }
    TradeRepository().insert(owner.id, trade, {"setup": "breakout"})
    loaded = TradeRepository().list(owner.id)[0]
    assert loaded["status"] == "closed"
    assert loaded["pnl_usd"] == 100.0
    assert loaded["close_reason"] == "target_hit"


def test_paper_trade_duplicate_and_owner_isolation(database):
    auth = AuthService()
    owner = auth.create_user("paper-owner", "correct horse battery staple")
    other = auth.create_user("paper-other", "another correct long password")
    repo = PaperTradeRepository()
    row = {"ts": 1000.0, "date": "date", "ticker": "msft", "level": 70,
           "entry": 100.0, "stop": 95.0, "target": 110.0, "status": "open"}
    assert repo.insert(owner.id, row)
    assert not repo.insert(owner.id, row)
    assert repo.list(other.id) == []


def test_paper_trade_legacy_source_ids_are_lossless_and_idempotent(database):
    owner = AuthService().create_user("paper-import-owner", "correct horse battery staple")
    repo = PaperTradeRepository()
    older = {"source_id": "legacy-sqlite:1", "ts": 1000.0, "date": "old",
             "ticker": "MSFT", "level": 70, "status": "closed"}
    newer = {"source_id": "legacy-sqlite:2", "ts": 1000.0 + 7 * 86400,
             "date": "new", "ticker": "MSFT", "level": 70, "status": "open"}
    assert repo.insert(owner.id, newer)
    assert repo.insert(owner.id, older)
    assert not repo.insert(owner.id, newer)
    assert {row["source_id"] for row in repo.list(owner.id)} == {
        "legacy-sqlite:1", "legacy-sqlite:2"}


def test_legacy_symbol_reader_is_normalized_and_source_unchanged(tmp_path):
    source = tmp_path / "watch.json"
    original = '[" msft ", "AAPL", "MSFT"]'
    source.write_text(original)
    assert read_symbol_file(source) == ["AAPL", "MSFT"]
    assert source.read_text() == original


def test_repository_batch_transaction_rolls_back_atomically(database):
    owner = AuthService().create_user("batch-owner", "correct horse battery staple")
    repository = WatchlistRepository()
    with pytest.raises(RuntimeError):
        with transaction_scope():
            repository.add(owner.id, "MSFT")
            raise RuntimeError("abort import")
    assert repository.list(owner.id) == []


def test_nested_transaction_behavior_is_explicit(database):
    with transaction_scope() as outer:
        with pytest.raises(RuntimeError, match="Nested transaction_scope"):
            with transaction_scope():
                pass
        with transaction_scope(join_existing=True) as inner:
            assert inner is outer


def test_csv_export_neutralizes_spreadsheet_formulas():
    exported = _safe_csv([{"ticker": "=HYPERLINK(\"bad\")", "note": "+cmd"}])
    assert "'=HYPERLINK" in exported
    assert "'+cmd" in exported


def test_restore_failure_keeps_application_stopped():
    script = Path("scripts/restore_db.sh").read_text()
    assert 'restore_succeeded=false' in script
    assert 'if [ "$restore_succeeded" = true ]' in script
    assert "application remains stopped" in script


def test_admin_audit_migration_rejects_direct_update_and_delete_on_sqlite(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'audit-migration.db'}"
    environment = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True, env=environment, capture_output=True, text=True,
    )
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO users "
            "(id, username, password_hash, is_active, is_admin, failed_login_count, "
            "created_at, updated_at, session_revision, must_change_password) VALUES "
            "('actor', 'actor', 'hash', 1, 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, 0), "
            "('target', 'target', 'hash', 1, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, 0)"
        ))
        connection.execute(text(
            "INSERT INTO admin_audit_events "
            "(actor_user_id, target_user_id, action, created_at, metadata_json) "
            "VALUES ('actor', 'target', 'user.created', CURRENT_TIMESTAMP, '{}')"
        ))

    for statement in (
        "UPDATE admin_audit_events SET action = 'tampered'",
        "DELETE FROM admin_audit_events",
    ):
        with pytest.raises(DatabaseError, match="append-only"):
            with engine.begin() as connection:
                connection.execute(text(statement))
    engine.dispose()


def test_admin_audit_migration_renders_postgresql_trigger_offline():
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        check=True,
        env={**os.environ, "DATABASE_URL": "postgresql://offline.invalid/stockanalyzer"},
        capture_output=True,
        text=True,
    )
    sql = result.stdout.lower()
    assert "create function" in sql
    assert "create trigger" in sql
    assert "before update or delete on admin_audit_events" in sql


def test_compose_has_private_database_and_loopback_only_application():
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    db = compose["services"]["db"]
    app = compose["services"]["app"]
    assert "ports" not in db
    assert app["ports"] == ["127.0.0.1:8501:8501"]
    assert compose["networks"]["database"]["internal"] is True
    assert "ALL" in app["cap_drop"]
    assert "no-new-privileges:true" in app["security_opt"]


def test_compose_runs_private_background_radar_worker():
    compose = yaml.safe_load(Path("compose.yaml").read_text())
    worker = compose["services"]["radar-worker"]
    assert worker["image"] == compose["services"]["app"]["image"]
    assert worker["command"] == ["python", "-m", "stockanalyzer.radar_worker"]
    assert "ports" not in worker
    assert worker["networks"] == ["database", "egress"]
    assert worker["depends_on"]["db"]["condition"] == "service_healthy"
    assert worker["depends_on"]["app"]["condition"] == "service_healthy"
    assert worker["environment"]["DATABASE_URL"].startswith("${DATABASE_URL:")
    assert "--healthcheck" in worker["healthcheck"]["test"]
    assert "ALL" in worker["cap_drop"]
    assert "no-new-privileges:true" in worker["security_opt"]

    entrypoint = Path("docker/entrypoint.sh").read_text()
    assert 'if [ "$#" -gt 0 ]' in entrypoint
    assert 'exec "$@"' in entrypoint
    assert "radar-worker" in Path("scripts/healthcheck.sh").read_text()


def test_entrypoint_command_path_executes_before_application_migration():
    entrypoint = Path("docker/entrypoint.sh").read_text()
    command_guard = entrypoint.index('if [ "$#" -gt 0 ]')
    command_exec = entrypoint.index('exec "$@"')
    migration = entrypoint.index("alembic upgrade head")

    assert command_guard < command_exec < migration

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pytest
import yaml
from sqlalchemy import insert, select

from stockanalyzer.auth import AuthService
from stockanalyzer.db.models import AnalysisRun, Base, OhlcvBar, OhlcvFetch, TradeEvent
from stockanalyzer.db.repositories.ohlcv import OhlcvRepository
from stockanalyzer.db.repositories.paper_trades import PaperTradeRepository
from stockanalyzer.db.repositories.radar_snapshots import RadarSnapshotRepository
from stockanalyzer.db.repositories.swingwatch import SwingWatchRepository
from stockanalyzer.db.repositories.trades import TradeRepository
from stockanalyzer.db.session import configure_engine, session_scope
from stockanalyzer.history_archive import combine_archives, make_archive, read_archive
from stockanalyzer.history_export import collect_history


@pytest.fixture()
def database(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'desktop.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("APP_ENV", "development")
    engine = configure_engine(url)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def owner(name="owner"):
    return AuthService().create_user(name, "test password long enough")


def trade(identity="trade-1", ticker="MSFT"):
    return {"id": identity, "ticker": ticker, "trader": "bot-GO", "status": "closed",
            "kind": "immediate", "opened_ts": 1.0, "opened": "1970-01-01",
            "entry": 100, "stop": 95, "init_stop": 95, "target": 110, "exit_price": 110,
            "close_reason": "target_hit", "closed_ts": 2.0, "pnl_pct": 10, "pnl_usd": 100}


def bars():
    return pd.DataFrame({"open": [10., 11.], "high": [12., 13.], "low": [9., 10.],
                         "close": [11., 12.], "volume": [100., 200.]},
                        index=pd.date_range("2026-10-01", periods=2, tz="UTC"))


def test_cache_keeps_unchanged_candles_and_removes_outgoing_bars(database):
    repository = OhlcvRepository()
    frame = bars()
    repository.store("yfinance:MSFT:1D", frame)
    old_stamp = datetime(2000, 1, 1, tzinfo=timezone.utc)
    with session_scope() as session:
        for row in session.scalars(select(OhlcvBar)):
            row.fetched_at = old_stamp
    repository.store("yfinance:MSFT:1D", frame)
    with session_scope() as session:
        assert all(row.fetched_at.year == 2000 for row in session.scalars(select(OhlcvBar)))
        assert session.scalar(select(OhlcvFetch.fetched_at)).year > 2000
    # Retention uses snapshot freshness, so unchanged current bars survive.
    assert repository.prune(datetime.now(timezone.utc) - timedelta(days=30)) == 0
    changed = frame.iloc[1:].copy()
    changed["close"] = 12.5
    repository.store("yfinance:MSFT:1D", changed)
    loaded = repository.load("yfinance:MSFT:1D", ttl=60)
    assert loaded is not None and len(loaded) == 1 and loaded["close"].iloc[0] == 12.5


def test_saved_dashboard_is_owner_scoped_and_reads_no_market_data(database):
    first, second = owner("first"), owner("second")
    SwingWatchRepository().add(first.id, "MSFT")
    repository = RadarSnapshotRepository()
    repository.save("MSFT", {"quote": {"price": 100}})
    assert repository.for_user(first.id)["MSFT"]["quote"]["price"] == 100
    assert repository.for_user(second.id) == {}


def test_worker_assignment_scope_stays_limited_to_the_original_radar(database):
    user = owner()
    row = trade()
    row["status"] = "open"
    TradeRepository().insert(user.id, row)
    assert SwingWatchRepository().list_assignments() == []
    SwingWatchRepository().add(user.id, "MSFT")
    assert SwingWatchRepository().list_assignments() == [(user.id, "MSFT")]


def test_closed_reports_load_on_demand_and_keep_all_history(database):
    import ast
    from streamlit.testing.v1 import AppTest

    user = owner()
    for index in range(3):
        TradeRepository().insert(user.id, trade(f"closed-{index}"), {"score": 75, "setup": "test"})
    # Exercise the real report fragment in isolation. AppTest cannot round-trip
    # the full page's passive keyed expanders; browser checks cover that page.
    tree = ast.parse((Path(__file__).resolve().parents[1] / "app.py").read_text())
    helpers = "\n\n".join(ast.unparse(node) for node in tree.body
                            if isinstance(node, ast.FunctionDef) and node.name in {
                                "_fmt_ts", "_portfolio_reports", "_render_portfolio_reports"})
    script = ("import time\nimport pandas as pd\nimport streamlit as st\n"
              "import ui_common\nfrom stockanalyzer import virtualbook\n"
              "_pnl_style = ui_common.pnl_style\n" + helpers +
              f"\nst.session_state.auth_user_id = {user.id!r}\n"
              "book = virtualbook.load(user_id=st.session_state.auth_user_id)\n"
              "s = virtualbook.stats(book)\ntot = s['totals']\nwr = str(tot['win_rate'])\n"
              "_portfolio_reports(book, s, tot, wr)\n")
    app = AppTest.from_string(script).run(timeout=60)
    assert not app.exception
    assert not app.dataframe

    app.toggle(key="portfolio_reports_open").set_value(True).run(timeout=30)
    assert not app.exception
    assert any("Trade history (3)" in element.value for element in app.markdown)
    assert len(app.dataframe[-1].value) == 3

    app.toggle(key="portfolio_reports_open").set_value(False).run(timeout=30)
    assert not app.exception
    assert not app.dataframe


def test_complete_export_is_lossless_scoped_and_has_no_pagination_limit(database):
    user, other = owner("first"), owner("other")
    TradeRepository().insert(user.id, trade(), {"setup": "test", "signals": [{"evidence": "detail"}]})
    TradeRepository().insert(other.id, trade("other-trade", "AAPL"))
    PaperTradeRepository().insert(user.id, {"ticker": "MSFT", "level": 70, "ts": 1., "guidance": "full detail"})
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        session.add(TradeEvent(trade_id="trade-1", ts=2., kind="stop", detail="stop moved", old_stop=95, new_stop=97))
        session.execute(insert(AnalysisRun), [{
            "id": f"analysis-{index}", "user_id": user.id, "idempotency_key": str(index),
            "ticker": "MSFT", "provider": "test", "started_at": now, "completed_at": now,
            "verdict": {"nested": {"detail": "kept"}},
        } for index in range(501)])
    document = collect_history(user.id, label="Maya")
    archive = make_archive(document)
    restored = read_archive(archive)
    assert restored["counts"]["analysis_runs"] == 501
    assert len(restored["tables"]["trades"]) == 1
    assert restored["tables"]["trade_events"][0]["new_stop"] == 97
    assert restored["tables"]["trades"][0]["snapshot"]["signals"][0]["evidence"] == "detail"
    assert restored["installation_id"] == collect_history(user.id)["installation_id"]
    assert not any(key in restored["tables"] for key in ("users", "ohlcv_bars", "api_cache_entries"))
    text = json.dumps(restored)
    assert "password_hash" not in text and other.id not in text


def test_combining_repeated_exports_updates_records_without_duplicates(database):
    user = owner()
    TradeRepository().insert(user.id, trade(), {"setup": "test", "app_version": "v1"})
    first = collect_history(user.id)
    first["exported_at"] = "2026-10-01T00:00:00+00:00"
    first["tables"]["trades"][0]["status"] = "open"
    latest = collect_history(user.id)
    latest["exported_at"] = "2026-10-02T00:00:00+00:00"
    other_install = json.loads(json.dumps(latest))
    other_install["installation_id"] = "another-machine"
    combined = combine_archives([make_archive(first), make_archive(latest), make_archive(latest), make_archive(other_install)])
    rows = [item for item in combined["records"] if item["table"] == "trades"]
    assert len(rows) == 2 and all(item["record"]["status"] == "closed" for item in rows)
    assert combined["report"]["unique_market_ideas"] == 1
    assert combined["report"]["trade_observations"] == 2
    assert len(combined["report"]["by_participant_and_trader"]) == 2
    repeated = combine_archives([make_archive(combined), make_archive(first)])
    assert repeated["records"] == combined["records"]
    other_install["tables"]["trades"][0]["app_version"] = "v2"
    assert combine_archives([make_archive(latest), make_archive(other_install)])["report"]["unique_market_ideas"] == 2


def test_archive_csv_is_safe_and_invalid_archives_are_rejected(database):
    user = owner()
    document = collect_history(user.id)
    document["tables"]["trades"] = [{"id": "one", "ticker": "=formula"}]
    with ZipFile(io.BytesIO(make_archive(document))) as archive:
        assert "'=formula" in archive.read("trades.csv").decode("utf-8-sig")
    with pytest.raises(ValueError):
        read_archive(b"not a zip")


def test_offline_merge_and_health_probe_need_no_third_party_imports(database, tmp_path):
    user = owner()
    source = tmp_path / "history.zip"
    source.write_bytes(make_archive(collect_history(user.id)))
    output = tmp_path / "team.zip"
    result = subprocess.run([sys.executable, "-S", "-m", "scripts.history", "merge", str(source),
                             "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert read_archive(output.read_bytes())["records"] == []
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text(str(datetime.now(timezone.utc).timestamp()))
    result = subprocess.run([sys.executable, "-S", "-m", "stockanalyzer.worker_health", "--healthcheck"],
                            env={**os.environ, "RADAR_WORKER_HEARTBEAT": str(heartbeat)}, capture_output=True)
    assert result.returncode == 0


def test_local_compose_is_independent_and_loopback_only():
    document = yaml.safe_load(Path("compose.local.yaml").read_text())
    assert all(not config.get("external") for config in document["networks"].values())
    assert "ports" not in document["services"]["db"]
    assert document["services"]["app"]["ports"] == ["127.0.0.1:${LOCAL_PORT:-8501}:8501"]
    assert "postgres_data" not in document["volumes"]
    assert ".local" in Path(".dockerignore").read_text().splitlines()


def test_second_local_launch_preserves_existing_admin(database, monkeypatch):
    from scripts.bootstrap_admin import main
    AuthService().create_user("admin", "test password long enough", is_admin=True)
    monkeypatch.setattr("getpass.getpass", lambda *_: pytest.fail("Existing admin must not be reset"))
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("Existing admin must not prompt"))
    assert main() == 0


def test_populated_radar_retains_its_original_analysis_execution():
    from scripts.benchmark_login import benchmark
    result = benchmark(Path("app.py").resolve(), tickers=14, delay=0.1)
    assert result["market_calls_during_login"] == 14
    print(f"LOGIN_BENCHMARK {json.dumps(result)}")


def test_source_identity_is_portable_across_line_endings(tmp_path):
    from scripts.source_version import source_fingerprint
    first, second = tmp_path / "linux", tmp_path / "windows"
    first.mkdir()
    second.mkdir()
    (first / "app.py").write_bytes(b"one\ntwo\n")
    (second / "app.py").write_bytes(b"one\r\ntwo\r\n")
    assert source_fingerprint(first) == source_fingerprint(second)
    (second / "app.py").write_bytes(b"different\n")
    assert source_fingerprint(first) != source_fingerprint(second)


def test_prepared_dashboard_payload_is_small_plain_json():
    import numpy as np
    from types import SimpleNamespace
    from stockanalyzer.dashboard import snapshot_result
    plan = SimpleNamespace(go=np.bool_(True), score=np.int64(80), entry=np.float64(100))
    result = SimpleNamespace(provider="test", reports={}, quote=None)
    payload = snapshot_result(result, plan)
    assert payload["plan"]["go"] is True
    assert payload["plan"]["score"] == 80
    assert len(json.dumps(payload)) < 2000

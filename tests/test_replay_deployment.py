"""Replay integration with authenticated users and read-only deployments."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import pandas as pd
import pytest
from sqlalchemy import func, select
from streamlit.testing.v1 import AppTest

from stockanalyzer import replay, virtualbook
from stockanalyzer.auth import AuthService
from stockanalyzer.db.models import Base, Trade
from stockanalyzer.db.session import configure_engine, session_scope


DAY = pd.Timestamp("2026-06-18")


def test_practice_users_have_separate_books_outside_application_source(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    source = tmp_path / "read-only-source"
    monkeypatch.setenv("STOCKANALYZER_REPLAY_DIR", str(runtime))
    owner = replay.practice_root(source, "owner")
    other = replay.practice_root(source, "other")
    attempt, book = replay.create_attempt(owner, "TEST", DAY)
    other_attempt, other_book = replay.create_attempt(other, "TEST", DAY)
    assert attempt == other_attempt == 1
    assert book != other_book and book.is_relative_to(runtime)
    assert not source.exists()
    virtualbook.open_position(
        ticker="TEST", trader="me", entry=100, stop=95, target=110,
        kind="immediate", path=book,
    )
    assert len(virtualbook.load(path=book)) == 1
    assert virtualbook.load(path=other_book) == []
    assert replay.next_attempt(owner, "TEST", DAY) == 2


def test_simultaneous_browser_sessions_reserve_distinct_books(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(lambda _: replay.create_attempt(tmp_path, "TEST", DAY),
                                 range(16)))
    assert len({book for _, book in attempts}) == 16
    assert sorted(number for number, _ in attempts) == list(range(1, 17))
    assert all(book.exists() and book.stat().st_mode & 0o077 == 0 for _, book in attempts)


@pytest.mark.parametrize("ticker", ["../../escape", "/tmp/escape", r"..\escape", "*"])
def test_practice_symbols_cannot_select_arbitrary_files(tmp_path, ticker):
    with pytest.raises(ValueError, match="ticker"):
        replay.create_attempt(tmp_path, ticker, DAY)
    assert list(tmp_path.iterdir()) == []


def test_practice_root_requires_a_user(tmp_path):
    with pytest.raises(ValueError, match="authenticated"):
        replay.practice_root(tmp_path, "")


@pytest.mark.parametrize("module", ["replay_page.py", "ui_common.py"])
def test_build_identity_tracks_shared_ui_modules(tmp_path, module):
    from scripts.source_version import source_fingerprint
    (tmp_path / module).write_text("first\n")
    before = source_fingerprint(tmp_path)
    (tmp_path / module).write_text("second\n")
    assert before != source_fingerprint(tmp_path)


def test_authenticated_replay_load_step_and_reveal_leave_live_history_untouched(tmp_path,
                                                                               monkeypatch):
    import streamlit as st
    from tests.test_replay import _history, _precompute

    url = f"sqlite:///{tmp_path / 'live.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("STOCKANALYZER_REPLAY_DIR", str(tmp_path / "practice"))
    engine = configure_engine(url)
    Base.metadata.create_all(engine)
    user = AuthService().create_user("replay-owner", "test password long enough")

    history = _history()
    session = _precompute(history)
    history["days"] = [DAY - pd.Timedelta(days=1), DAY]
    history["prev_close"] = {DAY: session.prev_close}
    monkeypatch.setattr(replay, "load_history", lambda *_args: history)
    monkeypatch.setattr(replay, "precompute_day", lambda *_args, **_kwargs: session)
    monkeypatch.setattr("stockanalyzer.pipeline.analyze_ticker",
                        lambda *_args, **_kwargs: pytest.fail("Replay reached live analysis"))
    st.cache_data.clear()
    try:
        app = AppTest.from_file("app.py")
        app.session_state["auth_user_id"] = user.id
        app.session_state["auth_username"] = user.username
        app.session_state["auth_session_revision"] = user.session_revision
        app.session_state["auth_last_activity"] = time.time()
        app.session_state["ticker"] = "TEST"
        app.run(timeout=30)
        next(button for button in app.button if button.label == "🎬 Replay").click().run(timeout=30)
        next(button for button in app.button if button.label == "🎬 Load day").click().run(timeout=30)
        assert not app.exception
        assert Path(app.session_state["rp_db"]).is_relative_to(tmp_path / "practice")

        next(button for button in app.button if button.label == "▶|").click().run(timeout=30)
        assert app.session_state["rp_i"] == 1
        assert not app.exception
        next(toggle for toggle in app.toggle if toggle.key == "rp_bot_orb").set_value(True).run(timeout=30)
        next(button for button in app.button if "Reveal the day now" in button.label).click().run(timeout=30)
        assert not app.exception
        assert app.session_state["rp_revealed"]
        practice = virtualbook.load(path=app.session_state["rp_db"])
        assert practice and all(row["status"] == "closed" for row in practice)
        with session_scope() as database:
            assert database.scalar(select(func.count()).select_from(Trade)) == 0
        next(button for button in app.button if button.label == "📊 Analyzer").click().run(timeout=30)
        assert app.session_state["page"] == "analyze"
        assert next(toggle for toggle in app.toggle if "Live mode" in toggle.label).value is False
        assert app.session_state["dashboard_auto_refresh"] is False
        assert not app.exception
    finally:
        st.cache_data.clear()
        engine.dispose()

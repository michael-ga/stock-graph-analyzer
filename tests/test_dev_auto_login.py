"""Local development sign-in without a password (DEV_AUTO_LOGIN).

It must work only for APP_ENV=development on SQLite, and never anywhere else.
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest

from stockanalyzer.auth import AuthService, dev_login_username
from stockanalyzer.db.models import Base
from stockanalyzer.db.session import configure_engine


def test_dev_login_needs_development_sqlite_and_a_name():
    sqlite = "sqlite:///.local/dev.db"
    assert dev_login_username("development", sqlite, " Local-Owner ") == "local-owner"
    assert dev_login_username("production", sqlite, "owner") is None
    assert dev_login_username("development", "postgresql+psycopg://db/app", "owner") is None
    assert dev_login_username("development", sqlite, "") is None
    assert dev_login_username("development", sqlite, None) is None


def _app(tmp_path, monkeypatch, env: str, user: str | None):
    database_url = f"sqlite:///{tmp_path / 'dev-login.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_ENV", env)
    if user is None:
        monkeypatch.delenv("DEV_AUTO_LOGIN", raising=False)
    else:
        monkeypatch.setenv("DEV_AUTO_LOGIN", user)
    engine = configure_engine(database_url)
    Base.metadata.create_all(engine)
    owner = AuthService().create_user("local-owner", "correct horse battery staple", is_admin=True)
    return engine, owner, AppTest.from_file("app.py").run(timeout=30)


def test_dev_auto_login_skips_the_password_form(tmp_path, monkeypatch):
    engine, owner, app = _app(tmp_path, monkeypatch, "development", "local-owner")
    assert app.session_state["auth_user_id"] == owner.id
    assert not any(button.label == "Sign in" for button in app.button)
    assert any("no password" in caption.value for caption in app.caption)
    engine.dispose()


def test_unknown_dev_user_still_shows_the_sign_in_form(tmp_path, monkeypatch):
    engine, _, app = _app(tmp_path, monkeypatch, "development", "nobody")
    assert any(button.label == "Sign in" for button in app.button)
    assert "auth_user_id" not in app.session_state
    engine.dispose()

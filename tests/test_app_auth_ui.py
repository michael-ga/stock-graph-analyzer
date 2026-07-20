from __future__ import annotations

from streamlit.testing.v1 import AppTest

from stockanalyzer.auth import AuthService
from stockanalyzer.db.models import Base
from stockanalyzer.db.session import configure_engine


def test_login_gate_accepts_database_user_and_exposes_logout(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'ui-auth.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_ENV", "development")
    engine = configure_engine(database_url)
    Base.metadata.create_all(engine)
    user = AuthService().create_user("mobile-owner", "correct horse battery staple")

    app = AppTest.from_file("app.py").run(timeout=30)
    assert any("Private access" in caption.value for caption in app.caption)
    app.text_input[0].set_value("mobile-owner")
    app.text_input[1].set_value("correct horse battery staple")
    sign_in = next(button for button in app.button if button.label == "Sign in")
    sign_in.click().run(timeout=30)

    assert app.session_state["auth_user_id"] == user.id
    assert any(button.label == "Log out" for button in app.button)
    assert not app.error
    engine.dispose()

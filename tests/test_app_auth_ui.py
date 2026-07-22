from __future__ import annotations

import ast
from pathlib import Path

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


def test_movers_radar_add_is_scoped_to_authenticated_user():
    tree = ast.parse(Path("app.py").read_text())
    movers_page = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_movers_page"
    )
    radar_add_calls = [
        node for node in ast.walk(movers_page)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "swingwatch"
        and node.func.attr == "add"
    ]

    assert len(radar_add_calls) == 1
    assert any(keyword.arg == "user_id" for keyword in radar_add_calls[0].keywords)

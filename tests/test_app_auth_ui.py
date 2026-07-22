from __future__ import annotations

import ast
from pathlib import Path

from streamlit.testing.v1 import AppTest

from stockanalyzer.auth import AuthService
from stockanalyzer.db.models import Base, User
from stockanalyzer.db.session import configure_engine, session_scope
from stockanalyzer.user_management import UserManagementService


def test_login_gate_accepts_database_user_and_exposes_logout(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'ui-auth.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_VERSION", "47f3fd9e56b560eba2804b99db348d883f1abdf3")
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
    assert any(caption.value == "Engineering version: eng-47f3fd9e56b5" for caption in app.caption)
    assert not any(button.label == "👥 Users" for button in app.button)
    assert not app.error
    engine.dispose()


def test_login_gate_rejects_and_clears_session_without_integer_revision(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'ui-legacy-session.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_ENV", "development")
    engine = configure_engine(database_url)
    Base.metadata.create_all(engine)
    user = AuthService().create_user("legacy-session", "correct horse battery staple")

    for invalid_revision in (None, "0", True):
        app = AppTest.from_file("app.py")
        app.session_state["auth_user_id"] = user.id
        app.session_state["auth_username"] = user.username
        app.session_state["auth_last_activity"] = 9_999_999_999.0
        if invalid_revision is not None:
            app.session_state["auth_session_revision"] = invalid_revision
        app.run(timeout=30)

        assert any("Private access" in caption.value for caption in app.caption)
        assert "auth_user_id" not in app.session_state
        assert "auth_session_revision" not in app.session_state
    engine.dispose()


def test_login_gate_revokes_session_after_admin_password_reset(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'ui-reset-revocation.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_ENV", "development")
    engine = configure_engine(database_url)
    Base.metadata.create_all(engine)
    admin = AuthService().create_user(
        "reset-admin", "correct horse battery staple", is_admin=True
    )
    user = AuthService().create_user("reset-target", "another sufficiently long password")

    app = AppTest.from_file("app.py").run(timeout=30)
    app.text_input[0].set_value("reset-target")
    app.text_input[1].set_value("another sufficiently long password")
    next(button for button in app.button if button.label == "Sign in").click().run(timeout=30)
    assert app.session_state["auth_user_id"] == user.id

    UserManagementService().reset_password(
        admin.id, user.id, "replacement password long enough"
    )
    app.run(timeout=30)

    assert any("Private access" in caption.value for caption in app.caption)
    assert "auth_user_id" not in app.session_state
    engine.dispose()


def test_users_navigation_is_visible_only_to_active_database_admin(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'ui-admin.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_ENV", "development")
    engine = configure_engine(database_url)
    Base.metadata.create_all(engine)
    AuthService().create_user(
        "admin", "correct horse battery staple", is_admin=True
    )

    app = AppTest.from_file("app.py").run(timeout=30)
    app.text_input[0].set_value("admin")
    app.text_input[1].set_value("correct horse battery staple")
    next(button for button in app.button if button.label == "Sign in").click().run(timeout=30)

    assert any(button.label == "👥 Users" for button in app.button)
    engine.dispose()


def _signed_in_admin_app(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'ui-admin-forms.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_ENV", "development")
    engine = configure_engine(database_url)
    Base.metadata.create_all(engine)
    AuthService().create_user("admin", "correct horse battery staple", is_admin=True)
    app = AppTest.from_file("app.py").run(timeout=30)
    app.text_input[0].set_value("admin")
    app.text_input[1].set_value("correct horse battery staple")
    next(button for button in app.button if button.label == "Sign in").click().run(timeout=30)
    next(button for button in app.button if button.label == "👥 Users").click().run(timeout=30)
    return app, engine


def test_users_page_create_requires_confirmation_and_shows_promised_fields(tmp_path, monkeypatch):
    app, engine = _signed_in_admin_app(tmp_path, monkeypatch)
    assert any(item.label == "Confirm temporary password" for item in app.text_input)
    assert any(
        item.label == "Confirm temporary password for admin" for item in app.text_input
    )
    page_text = " ".join(str(item.value) for item in app.markdown)
    assert "Created" in page_text and "Last login" in page_text and "Lock status" in page_text

    next(item for item in app.text_input if item.label == "New username").set_value("new-user")
    next(item for item in app.text_input if item.label == "Temporary password").set_value(
        "temporary password long enough"
    )
    next(item for item in app.text_input if item.label == "Confirm temporary password").set_value(
        "different password long enough"
    )
    next(button for button in app.button if button.label == "Create user").click().run(timeout=30)
    with session_scope() as session:
        assert session.query(User).filter_by(username="new-user").one_or_none() is None
    assert any("match" in error.value.lower() for error in app.error)
    engine.dispose()


def test_users_page_reset_requires_matching_confirmation(tmp_path, monkeypatch):
    app, engine = _signed_in_admin_app(tmp_path, monkeypatch)
    with session_scope() as session:
        before = session.query(User).filter_by(username="admin").one().password_hash
    next(
        item for item in app.text_input
        if item.label == "Temporary password for admin"
    ).set_value("replacement password long enough")
    next(
        item for item in app.text_input
        if item.label == "Confirm temporary password for admin"
    ).set_value("different replacement long enough")
    next(
        button for button in app.button if button.label == "Reset admin password"
    ).click().run(timeout=30)
    with session_scope() as session:
        assert session.query(User).filter_by(username="admin").one().password_hash == before
    assert any("match" in error.value.lower() for error in app.error)
    engine.dispose()


def test_forced_password_change_requires_matching_confirmation(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'ui-forced-change.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_ENV", "development")
    engine = configure_engine(database_url)
    Base.metadata.create_all(engine)
    admin = AuthService().create_user("admin", "correct horse battery staple", is_admin=True)
    user = UserManagementService().create_user(
        admin.id, "forced-user", "temporary password long enough"
    )
    app = AppTest.from_file("app.py").run(timeout=30)
    app.text_input[0].set_value("forced-user")
    app.text_input[1].set_value("temporary password long enough")
    next(button for button in app.button if button.label == "Sign in").click().run(timeout=30)
    assert any(item.label == "Confirm new password" for item in app.text_input)
    next(item for item in app.text_input if item.label == "Current password").set_value(
        "temporary password long enough"
    )
    next(item for item in app.text_input if item.label == "New password").set_value(
        "replacement password long enough"
    )
    next(item for item in app.text_input if item.label == "Confirm new password").set_value(
        "different replacement long enough"
    )
    next(button for button in app.button if button.label == "Change password").click().run(timeout=30)
    with session_scope() as session:
        assert session.get(User, user.id).must_change_password is True
    assert any("match" in error.value.lower() for error in app.error)
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

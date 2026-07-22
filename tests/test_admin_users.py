from __future__ import annotations

import pytest

from stockanalyzer.auth import AuthError, AuthService
from stockanalyzer.db.models import AdminAuditEvent, Base, User
from stockanalyzer.db.session import configure_engine, session_scope
from stockanalyzer.user_management import AdminAuthorizationError, UserManagementService


@pytest.fixture()
def db(tmp_path):
    engine = configure_engine(f"sqlite:///{tmp_path / 'admin.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_admin_methods_reload_actor_and_reject_non_admin_or_inactive(db):
    auth = AuthService()
    admin = auth.create_user("admin", "admin password long enough", is_admin=True)
    ordinary = auth.create_user("ordinary", "ordinary password long enough")
    service = UserManagementService()

    with pytest.raises(AdminAuthorizationError):
        service.list_users(ordinary.id)
    with session_scope() as session:
        session.get(User, admin.id).is_active = False
    with pytest.raises(AdminAuthorizationError):
        service.create_user(admin.id, "new", "new password long enough")


def test_admin_can_list_create_disable_enable_and_cannot_self_disable(db):
    auth = AuthService()
    admin = auth.create_user("admin", "admin password long enough", is_admin=True)
    service = UserManagementService()
    created = service.create_user(admin.id, "NewUser", "new password long enough")

    assert created.username == "newuser" and created.is_admin is False
    assert created.must_change_password is True
    assert [user.username for user in service.list_users(admin.id)] == ["admin", "newuser"]
    service.set_active(admin.id, created.id, False)
    assert auth.session_user(created.id) is None
    service.set_active(admin.id, created.id, True)
    assert auth.session_user(created.id) is not None
    AuthService().create_user("backup-admin", "backup admin password", is_admin=True)
    with pytest.raises(ValueError, match="yourself"):
        service.set_active(admin.id, admin.id, False)


def test_last_active_admin_cannot_be_disabled(db):
    admin = AuthService().create_user("admin", "admin password long enough", is_admin=True)
    service = UserManagementService()
    with pytest.raises(ValueError, match="last active admin"):
        service.set_active(admin.id, admin.id, False)


def test_password_reset_hashes_clears_lockout_and_revokes_sessions(db):
    auth = AuthService(max_failures=1)
    admin = auth.create_user("admin", "admin password long enough", is_admin=True)
    target = auth.create_user("target", "target password long enough")
    with pytest.raises(AuthError):
        auth.authenticate("target", "wrong")
    before = target.session_revision

    UserManagementService().reset_password(
        admin.id, target.id, "replacement password long enough"
    )
    with session_scope() as session:
        refreshed = session.get(User, target.id)
        assert refreshed.password_hash != "replacement password long enough"
        assert refreshed.failed_login_count == 0 and refreshed.locked_until is None
        assert refreshed.session_revision == before + 1
        assert refreshed.must_change_password is True
    assert auth.authenticate("target", "replacement password long enough").id == target.id


def test_admin_audit_is_append_only_and_redacts_password_material(db):
    auth = AuthService()
    admin = auth.create_user("admin", "admin password long enough", is_admin=True)
    target = auth.create_user("target", "target password long enough")
    service = UserManagementService()
    service.reset_password(admin.id, target.id, "replacement password long enough")

    with session_scope() as session:
        event = session.query(AdminAuditEvent).one()
        assert event.actor_user_id == admin.id and event.target_user_id == target.id
        assert event.action == "user.password_reset"
        serialized = str(event.metadata_json)
        assert "replacement" not in serialized and "hash" not in serialized.lower()

    with pytest.raises(ValueError, match="append-only"):
        with session_scope() as session:
            session.query(AdminAuditEvent).one().action = "tampered"
    with pytest.raises(ValueError, match="append-only"):
        with session_scope() as session:
            session.delete(session.query(AdminAuditEvent).one())


def test_forced_password_change_clears_flag_and_revises_session(db):
    auth = AuthService()
    user = auth.create_user("target", "target password long enough")
    with session_scope() as session:
        current = session.get(User, user.id)
        current.must_change_password = True
    changed = auth.change_password(
        user.id, "target password long enough", "new target password long enough"
    )
    assert changed.must_change_password is False
    assert changed.session_revision == 1
    assert auth.authenticate("target", "new target password long enough").id == user.id
    with pytest.raises(AuthError):
        auth.authenticate("target", "target password long enough")


def test_session_user_requires_matching_revision(db):
    auth = AuthService()
    user = auth.create_user("target", "target password long enough")
    assert auth.session_user(user.id, 0) is not None
    with session_scope() as session:
        session.get(User, user.id).session_revision += 1
    assert auth.session_user(user.id, 0) is None

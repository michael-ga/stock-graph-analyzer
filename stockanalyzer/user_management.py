"""Admin-only user lifecycle operations with append-only audit logging."""
from __future__ import annotations

from datetime import datetime, timezone

from argon2 import PasswordHasher
from sqlalchemy import func, select, text

from stockanalyzer.db.models import AdminAuditEvent, User
from stockanalyzer.db.session import session_scope


class AdminAuthorizationError(PermissionError):
    pass


class UserManagementService:
    def __init__(self) -> None:
        self.hasher = PasswordHasher()

    @staticmethod
    def _actor(session, actor_user_id: str) -> User:
        actor = session.get(User, actor_user_id)
        if actor is None or not actor.is_active or not actor.is_admin:
            raise AdminAuthorizationError("Active administrator access is required")
        return actor

    @staticmethod
    def _audit(session, actor_id: str, target_id: str, action: str, metadata: dict | None = None) -> None:
        session.add(AdminAuditEvent(
            actor_user_id=actor_id, target_user_id=target_id, action=action,
            metadata_json=metadata or {},
        ))

    def list_users(self, actor_user_id: str) -> list[User]:
        with session_scope() as session:
            self._actor(session, actor_user_id)
            return list(session.scalars(select(User).order_by(User.username)))

    def create_user(self, actor_user_id: str, username: str, password: str) -> User:
        username = username.strip().lower()
        if not username or len(password) < 12:
            raise ValueError("Username is required and password must be at least 12 characters")
        with session_scope() as session:
            actor = self._actor(session, actor_user_id)
            if session.scalar(select(User.id).where(User.username == username)):
                raise ValueError("Username already exists")
            target = User(
                username=username,
                password_hash=self.hasher.hash(password),
                is_admin=False,
                must_change_password=True,
            )
            session.add(target)
            session.flush()
            self._audit(session, actor.id, target.id, "user.created", {"username": username})
            return target

    def set_active(self, actor_user_id: str, target_user_id: str, active: bool) -> User:
        with session_scope() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                session.execute(text(
                    "SELECT pg_advisory_xact_lock(hashtext('user-management-active-admins'))"
                ))
            actor = self._actor(session, actor_user_id)
            target = session.get(User, target_user_id, with_for_update=True)
            if target is None:
                raise ValueError("User not found")
            if not active and target.is_admin and target.is_active:
                active_admins = session.scalar(select(func.count()).select_from(User).where(
                    User.is_active.is_(True), User.is_admin.is_(True)
                ))
                if active_admins <= 1:
                    raise ValueError("Cannot disable the last active admin")
            if not active and actor.id == target.id:
                raise ValueError("You cannot disable yourself")
            target.is_active = bool(active)
            target.session_revision += 1
            target.updated_at = datetime.now(timezone.utc)
            self._audit(session, actor.id, target.id,
                        "user.enabled" if active else "user.disabled")
            return target

    def reset_password(self, actor_user_id: str, target_user_id: str, password: str) -> User:
        if len(password) < 12:
            raise ValueError("Password must be at least 12 characters")
        with session_scope() as session:
            actor = self._actor(session, actor_user_id)
            target = session.get(User, target_user_id, with_for_update=True)
            if target is None:
                raise ValueError("User not found")
            target.password_hash = self.hasher.hash(password)
            target.failed_login_count = 0
            target.locked_until = None
            target.session_revision += 1
            target.must_change_password = True
            target.updated_at = datetime.now(timezone.utc)
            self._audit(session, actor.id, target.id, "user.password_reset")
            return target

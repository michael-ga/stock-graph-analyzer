"""Database-backed Argon2 authentication and lockout service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select

from stockanalyzer.db.models import User
from stockanalyzer.db.session import session_scope

_GENERIC = "Invalid username or password"


class AuthError(RuntimeError):
    pass


class AuthService:
    def __init__(self, max_failures: int = 5, lockout_minutes: int = 15):
        self.max_failures = max_failures
        self.lockout_minutes = lockout_minutes
        self.hasher = PasswordHasher()

    def create_user(self, username: str, password: str, *, is_admin: bool = False) -> User:
        username = username.strip().lower()
        if not username or len(password) < 12:
            raise ValueError("Username is required and password must be at least 12 characters")
        with session_scope() as session:
            if session.scalar(select(User).where(User.username == username)):
                raise ValueError("Username already exists")
            user = User(username=username, password_hash=self.hasher.hash(password), is_admin=is_admin)
            session.add(user)
            session.flush()
            return user

    def session_user(self, user_id: str, session_revision: int | None = None) -> User | None:
        """Return an active user, optionally checking a revision for non-browser callers."""
        with session_scope() as session:
            user = session.get(User, user_id)
            if user is None or not user.is_active:
                return None
            if session_revision is not None and user.session_revision != session_revision:
                return None
            return user

    def browser_session_user(self, user_id: str, session_revision: object) -> User | None:
        """Validate a browser session fail-closed against its integer revision."""
        if type(session_revision) is not int:
            return None
        return self.session_user(user_id, session_revision)

    def change_password(self, user_id: str, current_password: str, new_password: str) -> User:
        if len(new_password) < 12:
            raise ValueError("Password must be at least 12 characters")
        with session_scope() as session:
            user = session.get(User, user_id, with_for_update=True)
            if user is None or not user.is_active:
                raise AuthError(_GENERIC)
            try:
                valid = self.hasher.verify(user.password_hash, current_password)
            except (VerifyMismatchError, InvalidHashError):
                valid = False
            if not valid:
                raise AuthError(_GENERIC)
            user.password_hash = self.hasher.hash(new_password)
            user.must_change_password = False
            user.failed_login_count = 0
            user.locked_until = None
            user.session_revision += 1
            user.updated_at = datetime.now(timezone.utc)
            return user

    def authenticate(self, username: str, password: str) -> User:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            user = session.scalar(
                select(User)
                .where(User.username == username.strip().lower())
                .with_for_update()
            )
            if user is None or not user.is_active:
                raise AuthError(_GENERIC)
            locked = user.locked_until
            if locked is not None and locked.tzinfo is None:
                locked = locked.replace(tzinfo=timezone.utc)
            if locked is not None and locked > now:
                raise AuthError(_GENERIC)
            if locked is not None:
                user.locked_until = None
                user.failed_login_count = 0
            try:
                ok = self.hasher.verify(user.password_hash, password)
            except (VerifyMismatchError, InvalidHashError):
                ok = False
            if not ok:
                user.failed_login_count += 1
                if user.failed_login_count >= self.max_failures:
                    user.locked_until = now + timedelta(minutes=self.lockout_minutes)
                user.updated_at = now
                failed = True
            else:
                user.failed_login_count = 0
                user.locked_until = None
                user.last_login_at = now
                user.updated_at = now
                if self.hasher.check_needs_rehash(user.password_hash):
                    user.password_hash = self.hasher.hash(password)
                failed = False
        if failed:
            raise AuthError(_GENERIC)
        return user

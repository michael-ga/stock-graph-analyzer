"""First local launch creates an administrator; later launches preserve accounts."""
from __future__ import annotations

from sqlalchemy import select

from stockanalyzer.auth import AuthService
from stockanalyzer.config import Settings
from stockanalyzer.db.models import User
from stockanalyzer.db.session import configure_engine, session_scope


def admin_exists() -> bool:
    with session_scope() as session:
        return session.scalar(select(User.id).where(User.is_admin.is_(True), User.is_active.is_(True)).limit(1)) is not None


def main() -> int:
    import getpass

    configure_engine(Settings.from_env().database_url)
    if admin_exists():
        print("Existing users and administrator retained.")
        return 0
    print("Create the administrator for this installation.")
    username = input("Administrator username [admin]: ").strip() or "admin"
    while True:
        password = getpass.getpass("Password (at least 12 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            print("Passwords do not match. Please try again.")
            continue
        try:
            AuthService().create_user(username, password, is_admin=True)
        except ValueError as exc:
            print(str(exc))
            username = input("Administrator username [admin]: ").strip() or "admin"
            continue
        print(f"Administrator created: {username}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

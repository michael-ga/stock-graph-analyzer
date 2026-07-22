"""Validated, secret-safe runtime configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is invalid."""


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def redact_database_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.password is None:
            return url
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        username = parsed.username or ""
        netloc = f"{username}:***@{host}" if username else host
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        return "<redacted database URL>"


@dataclass(frozen=True, repr=False)
class Settings:
    database_url: str
    auth_session_minutes: int = 60
    auth_max_failures: int = 5
    auth_lockout_minutes: int = 15
    finnhub_key: str = ""
    twelvedata_key: str = ""
    app_env: str = "development"

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigurationError("DATABASE_URL is required")
        app_env = os.getenv("APP_ENV", "development").strip().lower()
        if app_env == "production" and not database_url.startswith(
            ("postgresql://", "postgresql+psycopg://")
        ):
            raise ConfigurationError("Production DATABASE_URL must use PostgreSQL")
        return cls(
            database_url=database_url,
            auth_session_minutes=_positive_int("AUTH_SESSION_MINUTES", 60),
            auth_max_failures=_positive_int("AUTH_MAX_FAILURES", 5),
            auth_lockout_minutes=_positive_int("AUTH_LOCKOUT_MINUTES", 15),
            finnhub_key=os.getenv("FINNHUB_KEY", "").strip(),
            twelvedata_key=os.getenv("TWELVEDATA_KEY", "").strip(),
            app_env=app_env,
        )

    def __repr__(self) -> str:
        return (
            "Settings(database_url="
            f"{redact_database_url(self.database_url)!r}, "
            f"auth_session_minutes={self.auth_session_minutes}, "
            f"auth_max_failures={self.auth_max_failures}, "
            f"auth_lockout_minutes={self.auth_lockout_minutes}, "
            "finnhub_key='***', twelvedata_key='***', "
            f"app_env={self.app_env!r})"
        )

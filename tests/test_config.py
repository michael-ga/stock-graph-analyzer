from __future__ import annotations

import pytest

from stockanalyzer.config import ConfigurationError, Settings


def test_settings_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        Settings.from_env()


def test_settings_redacts_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://app:supersecret@db/app")
    monkeypatch.setenv("FINNHUB_KEY", "finn-secret")
    settings = Settings.from_env()
    rendered = repr(settings)
    assert "supersecret" not in rendered
    assert "finn-secret" not in rendered
    assert "***" in rendered


def test_settings_rejects_non_postgres_production_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///trades.db")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ConfigurationError, match="PostgreSQL"):
        Settings.from_env()

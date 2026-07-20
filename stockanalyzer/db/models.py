"""SQLAlchemy schema for durable application state."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey,
                        Index, Integer, String, Text, UniqueConstraint, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


ID = BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("user_id", "ticker"),)
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SwingWatchItem(Base):
    __tablename__ = "swing_watch_items"
    __table_args__ = (UniqueConstraint("user_id", "ticker"),)
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    last_notice_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ProviderRateLimit(Base):
    __tablename__ = "provider_rate_limits"
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    bucket_date: Mapped[date] = mapped_column(Date, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class OhlcvBar(Base):
    __tablename__ = "ohlcv_bars"
    __table_args__ = (Index("ix_ohlcv_ticker_timeframe_time", "ticker", "timeframe", "bar_time"),)
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(16), primary_key=True)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ApiCacheEntry(Base):
    __tablename__ = "api_cache_entries"
    cache_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (Index("ix_analysis_owner_ticker_completed", "user_id", "ticker", "completed_at"),
                      UniqueConstraint("user_id", "idempotency_key"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(64))
    swing_pace: Mapped[str | None] = mapped_column(String(64))
    use_case: Mapped[str | None] = mapped_column(String(64))
    quote: Mapped[dict | None] = mapped_column(JSON)
    sentiment: Mapped[dict | None] = mapped_column(JSON)
    verdict: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    errors: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    notices: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timeframes: Mapped[list["AnalysisTimeframe"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class AnalysisTimeframe(Base):
    __tablename__ = "analysis_timeframes"
    __table_args__ = (UniqueConstraint("analysis_run_id", "timeframe"),)
    id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    bias_score: Mapped[float] = mapped_column(Float, nullable=False)
    trend_direction: Mapped[str] = mapped_column(String(32), nullable=False)
    last_close: Mapped[float | None] = mapped_column(Float)
    bar_count: Mapped[int] = mapped_column(Integer, nullable=False)
    signals: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    levels: Mapped[dict | list] = mapped_column(JSON, default=dict, nullable=False)
    trendlines: Mapped[dict | list] = mapped_column(JSON, default=dict, nullable=False)
    trend_change: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index(
            "uq_trade_active_owner_ticker_trader",
            "user_id", "ticker", "trader",
            unique=True,
            postgresql_where=text("status IN ('open', 'pending')"),
            sqlite_where=text("status IN ('open', 'pending')"),
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trader: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="immediate", nullable=False)
    opened_ts: Mapped[float] = mapped_column(Float, nullable=False)
    opened: Mapped[str] = mapped_column(String(64), nullable=False)
    activated_ts: Mapped[float | None] = mapped_column(Float)
    broke_out_ts: Mapped[float | None] = mapped_column(Float)
    entry: Mapped[float] = mapped_column(Float, nullable=False)
    stop: Mapped[float] = mapped_column(Float, nullable=False)
    target: Mapped[float] = mapped_column(Float, nullable=False)
    trigger_price: Mapped[float | None] = mapped_column(Float)
    stake: Mapped[float] = mapped_column(Float, default=1000, nullable=False)
    shares: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float)
    close_reason: Mapped[str | None] = mapped_column(String(64))
    closed_ts: Mapped[float | None] = mapped_column(Float)
    closed: Mapped[str | None] = mapped_column(String(64))
    pnl_pct: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    pnl_usd: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    cohort_id: Mapped[str | None] = mapped_column(String(64), index=True)


class TradeSignal(Base):
    __tablename__ = "trade_signals"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    timeframe: Mapped[str] = mapped_column(String(16)); name: Mapped[str] = mapped_column(String(128))
    direction: Mapped[str] = mapped_column(String(32)); strength: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(64)); evidence: Mapped[str | None] = mapped_column(Text)


class TradeIndicator(Base):
    __tablename__ = "trade_indicators"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    timeframe: Mapped[str] = mapped_column(String(16)); payload: Mapped[dict] = mapped_column(JSON, default=dict)


class TradeVerdict(Base):
    __tablename__ = "trade_verdict"
    trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class TradeSwingCheck(Base):
    __tablename__ = "trade_swing_checks"
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128)); ok: Mapped[bool] = mapped_column(Boolean)
    na: Mapped[bool] = mapped_column(Boolean, default=False); detail: Mapped[str | None] = mapped_column(Text)
    weight: Mapped[int] = mapped_column(Integer)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    __table_args__ = (UniqueConstraint("user_id", "source_id", name="uq_paper_trade_source"),)
    id: Mapped[int] = mapped_column(ID, primary_key=True)
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    ts: Mapped[float] = mapped_column(Float); date: Mapped[str] = mapped_column(String(32))
    ticker: Mapped[str] = mapped_column(String(32), index=True); level: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON, default=dict); status: Mapped[str] = mapped_column(String(32), default="open")
    result_pct: Mapped[float] = mapped_column(Float, default=0)

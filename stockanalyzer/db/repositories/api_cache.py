from __future__ import annotations
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from stockanalyzer.db.models import ApiCacheEntry
from stockanalyzer.db.session import session_scope


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class ApiCacheRepository:
    def put(self, key: str, provider: str, payload, *, ttl_seconds: int) -> None:
        now = datetime.now(timezone.utc)
        with session_scope() as s:
            row = s.get(ApiCacheEntry, (key, provider))
            if row is None:
                s.add(ApiCacheEntry(cache_key=key, provider=provider, payload=payload,
                                    created_at=now, expires_at=now + timedelta(seconds=ttl_seconds)))
            else:
                row.payload = payload; row.created_at = now
                row.expires_at = now + timedelta(seconds=ttl_seconds)

    def get(self, key: str, *, provider: str | None = None, allow_expired: bool = False):
        with session_scope() as s:
            if provider is None:
                row = s.scalar(select(ApiCacheEntry).where(
                    ApiCacheEntry.cache_key == key).limit(1))
            else:
                row = s.get(ApiCacheEntry, (key, provider))
            if row is None:
                return None
            if not allow_expired and _aware(row.expires_at) <= datetime.now(timezone.utc):
                return None
            return row.payload

    def prune_expired(self) -> int:
        with session_scope() as s:
            result = s.execute(delete(ApiCacheEntry).where(ApiCacheEntry.expires_at <= datetime.now(timezone.utc)))
            return result.rowcount or 0

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from stockanalyzer.db.models import ProviderRateLimit
from stockanalyzer.db.session import session_scope


class RateLimitRepository:
    def increment(self, provider: str, *, limit: int, bucket_date=None) -> int | None:
        """Atomically consume one daily request across concurrent workers."""
        provider = provider.strip().lower()
        day = bucket_date or datetime.now(timezone.utc).date()
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            table = ProviderRateLimit.__table__
            updated = session.execute(
                update(table)
                .where(
                    table.c.provider == provider,
                    table.c.bucket_date == day,
                    table.c.request_count < limit,
                )
                .values(request_count=table.c.request_count + 1, updated_at=now)
                .returning(table.c.request_count)
            ).scalar_one_or_none()
            if updated is not None:
                return int(updated)

            dialect = session.get_bind().dialect.name
            insert = pg_insert(table) if dialect == "postgresql" else sqlite_insert(table)
            created = session.execute(
                insert.values(
                    provider=provider,
                    bucket_date=day,
                    request_count=1,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["provider", "bucket_date"])
                .returning(table.c.request_count)
            ).scalar_one_or_none()
            if created is not None:
                return int(created)

            # Another transaction inserted between our UPDATE and INSERT.
            retried = session.execute(
                update(table)
                .where(
                    table.c.provider == provider,
                    table.c.bucket_date == day,
                    table.c.request_count < limit,
                )
                .values(request_count=table.c.request_count + 1, updated_at=now)
                .returning(table.c.request_count)
            ).scalar_one_or_none()
            return int(retried) if retried is not None else None

    def import_count(self, provider: str, bucket_date, count: int) -> None:
        """Idempotently retain the larger imported/current counter."""
        provider = provider.strip().lower()
        with session_scope() as session:
            row = session.scalar(
                select(ProviderRateLimit)
                .where(
                    ProviderRateLimit.provider == provider,
                    ProviderRateLimit.bucket_date == bucket_date,
                )
                .with_for_update()
            )
            if row is None:
                session.add(ProviderRateLimit(
                    provider=provider,
                    bucket_date=bucket_date,
                    request_count=max(0, int(count)),
                    updated_at=datetime.now(timezone.utc),
                ))
            else:
                row.request_count = max(row.request_count, max(0, int(count)))
                row.updated_at = datetime.now(timezone.utc)

    def remaining(self, provider: str, *, limit: int, bucket_date=None) -> int:
        provider = provider.strip().lower()
        day = bucket_date or datetime.now(timezone.utc).date()
        with session_scope() as session:
            count = session.scalar(
                select(ProviderRateLimit.request_count).where(
                    ProviderRateLimit.provider == provider,
                    ProviderRateLimit.bucket_date == day,
                )
            ) or 0
            return max(0, limit - count)

from __future__ import annotations
import os
from alembic import context
from sqlalchemy import engine_from_config, pool
from stockanalyzer.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=os.environ["DATABASE_URL"], target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction(): context.run_migrations()


def run_migrations_online():
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = os.environ["DATABASE_URL"]
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()

run_migrations_offline() if context.is_offline_mode() else run_migrations_online()

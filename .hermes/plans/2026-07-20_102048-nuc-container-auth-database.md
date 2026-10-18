# NUC Container, Remote Login, and Database Implementation Plan

> **For Hermes:** Use subagent-driven-development and test-driven-development to implement this plan task-by-task. This document is a plan only; the PR containing it must not implement the application changes.

**Goal:** Run Stock Graph Analyzer as a durable Docker deployment on a personal Intel NUC, provide private authenticated access from anywhere, replace file-backed application state with PostgreSQL, and expose a safe in-app database history/data explorer.

**Architecture:** Run two Docker Compose services: an unprivileged Streamlit application and an internal-only PostgreSQL database. Publish Streamlit to host loopback only and expose it privately with host-level Tailscale Serve; do not publish PostgreSQL or use Tailscale Funnel. Introduce a repository/service layer so UI and analysis code never issue ad-hoc SQL, migrate existing SQLite/JSON state idempotently, store provider caches as structured database records instead of pickle/parquet files, and add an authenticated History page for querying/exporting stored data.

**Tech Stack:** Python 3.12, Streamlit, Docker/Compose, PostgreSQL 16, SQLAlchemy 2.x, Alembic, psycopg 3, Argon2 password hashing, Tailscale Serve, pytest, Ruff.

---

## 1. Scope and acceptance criteria

### Required outcomes

1. `docker compose up -d` starts the application and PostgreSQL on the NUC.
2. The application is not exposed on the public internet or LAN by Docker; it binds only to `127.0.0.1:8501` on the NUC.
3. Host-level Tailscale Serve provides private HTTPS access to authorized tailnet devices from anywhere.
4. The application displays a login screen before any stock, watchlist, portfolio, radar, or history data is shown.
5. Passwords are stored only as Argon2 hashes; plaintext passwords are never logged, committed, or stored in PostgreSQL.
6. PostgreSQL is the durable source of truth for:
   - Users and authentication audit metadata.
   - Watchlist entries.
   - Swing-radar entries.
   - Provider rate-limit counters.
   - OHLCV market-data cache.
   - Finnhub/company/news cache.
   - Analysis-run summaries and timeframe reports.
   - Existing trades, signals, indicators, verdicts, checks, and paper trades.
7. Application code no longer writes `.watchlist.json`, `.swingwatch.json`, `.ratelimit.json`, `.pkl`, or `.parquet` state during normal operation.
8. Existing `trades.db`, watchlist JSON, and swing-watch JSON can be imported once without creating duplicates.
9. An authenticated **History** page can retrieve stored analysis data by ticker, date range, provider, timeframe, and record type and can download the current result set as CSV.
10. Restarting/recreating the application container preserves data in the PostgreSQL volume.
11. PostgreSQL is not published to the host or tailnet; only the application service can connect to it.
12. The existing test suite remains green and new database/authentication/container tests are added.

### Explicit non-goals

- No public signup or multi-tenant SaaS behavior.
- No public internet exposure, router port forwarding, or Tailscale Funnel.
- No live brokerage integration or real-money order execution.
- No change to technical-analysis scoring in this project.
- No Kubernetes, Redis, Celery, or external managed database.
- No NordVPN gateway in the first deployment. It can be added later only after confirming Yahoo/Finnhub/Twelve Data work reliably through the selected exit node.

---

## 2. Current-state findings that shape the work

- `stockanalyzer/data/store.py` already stores trades and paper trades in SQLite schema version 4. This data must be migrated, not redesigned from scratch without compatibility tests.
- `stockanalyzer/watchlist.py` writes `.watchlist.json`.
- `stockanalyzer/swingwatch.py` writes `.swingwatch.json`.
- `stockanalyzer/data/ratelimit.py` writes `.ratelimit.json`.
- `stockanalyzer/data/cache.py` writes OHLCV frames as parquet under `.cache/`.
- `stockanalyzer/data/finnhub.py` writes and loads pickle cache files under `.cache/`.
- `app.py` is a single 2,000+ line Streamlit entry point. Authentication must be enforced before `main()` renders private state.
- The repository has no `Dockerfile`, Compose file, Alembic configuration, or CI workflow.
- Render configuration does not provide durable local storage and is not the target deployment for this work.
- Tailscale already runs on the intended NUC host. Keep Tailscale on the host so container networking cannot disrupt tailnet SSH/access.

---

## 3. Target topology

```text
Authorized phone/laptop
        |
        | Tailscale private HTTPS
        v
NUC host: Tailscale Serve
        |
        | http://127.0.0.1:8501
        v
Docker: stock-analyzer app (non-root)
        |
        | private Compose network only
        v
Docker: PostgreSQL 16
        |
        v
Named volume: postgres_data
```

### Network invariants

- Compose publishes `127.0.0.1:8501:8501`, never `0.0.0.0:8501:8501`.
- PostgreSQL has no `ports:` entry.
- The application and PostgreSQL share a private Compose network.
- Tailscale remains installed and managed on the host.
- Tailscale Serve points to the loopback listener.
- Tailnet ACLs/grants restrict access to the owner’s devices/account.
- A non-tailnet device must fail to reach the service.

---

## 4. Proposed database model

Use Alembic migrations and SQLAlchemy models. Keep numeric market values as `DOUBLE PRECISION` unless exact decimal semantics are required for a field. Use UTC timestamps throughout.

### Identity and authorization

#### `users`

- `id UUID PRIMARY KEY`
- `username TEXT UNIQUE NOT NULL`
- `password_hash TEXT NOT NULL`
- `is_active BOOLEAN NOT NULL DEFAULT true`
- `is_admin BOOLEAN NOT NULL DEFAULT false`
- `failed_login_count INTEGER NOT NULL DEFAULT 0`
- `locked_until TIMESTAMPTZ NULL`
- `last_login_at TIMESTAMPTZ NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

The first administrator is created by a one-shot bootstrap command. Do not automatically recreate or reset the account on every container start.

### User state

#### `watchlist_items`

- `id BIGSERIAL PRIMARY KEY`
- `user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE`
- `ticker TEXT NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- Unique constraint on `(user_id, ticker)`.

#### `swing_watch_items`

- `id BIGSERIAL PRIMARY KEY`
- `user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE`
- `ticker TEXT NOT NULL`
- `last_notice_level INTEGER NOT NULL DEFAULT 0`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`
- Unique constraint on `(user_id, ticker)`.

### Provider state and caches

#### `provider_rate_limits`

- `provider TEXT NOT NULL`
- `bucket_date DATE NOT NULL`
- `request_count INTEGER NOT NULL DEFAULT 0`
- `updated_at TIMESTAMPTZ NOT NULL`
- Primary key on `(provider, bucket_date)`.

Minute-level token buckets remain in memory; the database stores durable daily counters. Increment daily counters atomically to avoid lost updates.

#### `ohlcv_bars`

- `provider TEXT NOT NULL`
- `ticker TEXT NOT NULL`
- `timeframe TEXT NOT NULL`
- `bar_time TIMESTAMPTZ NOT NULL`
- `open DOUBLE PRECISION NOT NULL`
- `high DOUBLE PRECISION NOT NULL`
- `low DOUBLE PRECISION NOT NULL`
- `close DOUBLE PRECISION NOT NULL`
- `volume DOUBLE PRECISION NOT NULL DEFAULT 0`
- `fetched_at TIMESTAMPTZ NOT NULL`
- Primary key on `(provider, ticker, timeframe, bar_time)`.
- Index on `(ticker, timeframe, bar_time DESC)`.

Upsert fetched frames in one transaction. Freshness is determined from `MAX(fetched_at)` for a provider/ticker/timeframe key. Stale-while-error reads the same rows regardless of expiration.

#### `api_cache_entries`

- `cache_key TEXT PRIMARY KEY`
- `provider TEXT NOT NULL`
- `payload JSONB NOT NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `expires_at TIMESTAMPTZ NOT NULL`
- Index on `expires_at`.

Use this for Finnhub profile, metrics, recommendations, targets, news, and earnings responses. Serialize only JSON-compatible primitives; never use pickle.

### Analysis history

#### `analysis_runs`

- `id UUID PRIMARY KEY`
- `user_id UUID NOT NULL REFERENCES users(id)`
- `ticker TEXT NOT NULL`
- `provider TEXT NOT NULL`
- `strategy TEXT NULL`
- `swing_pace TEXT NULL`
- `use_case TEXT NULL`
- `quote JSONB NULL`
- `sentiment JSONB NULL`
- `verdict JSONB NOT NULL`
- `errors JSONB NOT NULL DEFAULT '{}'`
- `notices JSONB NOT NULL DEFAULT '[]'`
- `started_at TIMESTAMPTZ NOT NULL`
- `completed_at TIMESTAMPTZ NOT NULL`
- Index on `(user_id, ticker, completed_at DESC)`.

#### `analysis_timeframes`

- `id BIGSERIAL PRIMARY KEY`
- `analysis_run_id UUID NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE`
- `timeframe TEXT NOT NULL`
- `bias_score DOUBLE PRECISION NOT NULL`
- `trend_direction TEXT NOT NULL`
- `last_close DOUBLE PRECISION NULL`
- `bar_count INTEGER NOT NULL`
- `signals JSONB NOT NULL`
- `levels JSONB NOT NULL`
- `trendlines JSONB NOT NULL`
- `trend_change JSONB NOT NULL`
- Unique constraint on `(analysis_run_id, timeframe)`.

Do not duplicate every OHLCV bar into each analysis run; `analysis_timeframes` stores the decision snapshot and references market history by ticker/timeframe/time. This prevents unnecessary database growth on the NUC.

### Existing trade schema

Port the existing SQLite v4 tables to PostgreSQL with equivalent constraints and indexes:

- `trades`
- `trade_signals`
- `trade_indicators`
- `trade_verdict`
- `trade_swing_checks`
- `paper_trades`

Add `user_id` to user-owned trade and paper-trade records. Preserve `cohort_id` behavior and all existing analytics. Do not change trade-calculation logic during the migration.

---

## 5. Implementation tasks

### Task 1: Add packaging and database dependencies

**Objective:** Establish reproducible application tooling for PostgreSQL, migrations, authentication, linting, and tests.

**Files:**

- Create: `pyproject.toml`
- Create: `requirements.lock`
- Modify: `requirements.txt`
- Create: `.python-version` only if the current file does not already pin the selected runtime.

**Steps:**

1. Add `SQLAlchemy`, `alembic`, `psycopg[binary]`, and `argon2-cffi`.
2. Add development groups for `pytest`, `ruff`, and migration tests.
3. Preserve all current runtime dependencies.
4. Generate a lock file for Python 3.12 on Linux x86_64.
5. Run `python -m pytest -q`; expect the current suite to pass.
6. Run `ruff check .`; establish an explicit baseline rather than silently enabling auto-fix across unrelated code.

**Commit:** `build: add database and authentication dependencies`

### Task 2: Introduce application configuration

**Objective:** Centralize database, authentication, and runtime settings with validation and secret-safe errors.

**Files:**

- Create: `stockanalyzer/config.py`
- Create: `tests/test_config.py`
- Modify: `.env.example`

**Required settings:**

- `DATABASE_URL`
- `AUTH_SESSION_MINUTES`
- `AUTH_MAX_FAILURES`
- `AUTH_LOCKOUT_MINUTES`
- Existing `FINNHUB_KEY` and `TWELVEDATA_KEY`

**Steps:**

1. Write failing tests for missing/invalid `DATABASE_URL` and safe error rendering.
2. Implement a typed settings object.
3. Ensure configuration string representations redact passwords and API keys.
4. Verify the application refuses to start with an unusable production database URL.
5. Run `pytest tests/test_config.py -v`; expect all tests to pass.

**Commit:** `feat: add validated runtime configuration`

### Task 3: Add SQLAlchemy engine and transactional session management

**Objective:** Provide one safe database boundary for all repositories.

**Files:**

- Create: `stockanalyzer/db/__init__.py`
- Create: `stockanalyzer/db/session.py`
- Create: `tests/db/test_session.py`

**Steps:**

1. Test connection creation, transaction commit, and rollback.
2. Configure connection health checks with `pool_pre_ping=True`.
3. Use a session-per-operation/context-manager pattern; do not share a global mutable connection across Streamlit threads.
4. Verify failed writes roll back without poisoning the next operation.

**Commit:** `feat: add transactional database session layer`

### Task 4: Create models and the initial Alembic schema

**Objective:** Materialize the target PostgreSQL schema without changing application behavior.

**Files:**

- Create: `stockanalyzer/db/models.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/<revision>_initial_postgres_schema.py`
- Create: `tests/db/test_schema.py`

**Steps:**

1. Write schema tests for required tables, indexes, foreign keys, and unique constraints.
2. Add models described in Section 4.
3. Port the existing trade schema exactly before extending it with `user_id`.
4. Run `alembic upgrade head` against a disposable PostgreSQL test container.
5. Run `alembic downgrade base` and upgrade again to verify reversibility.
6. Assert PostgreSQL has no externally published port in test Compose configuration.

**Commit:** `feat: add initial PostgreSQL schema`

### Task 5: Add administrator bootstrap and authentication service

**Objective:** Require a valid database-backed user session before rendering private application state.

**Files:**

- Create: `stockanalyzer/auth.py`
- Create: `scripts/create_admin.py`
- Create: `tests/test_auth.py`
- Modify: `app.py`

**Steps:**

1. Test Argon2 hashing and verification.
2. Test disabled users, failed-attempt counting, temporary lockout, successful reset, and generic error messages.
3. Implement one-shot `scripts/create_admin.py --username <name>` with interactive password confirmation or a password supplied through protected stdin—not a command-line argument.
4. Store only the Argon2 hash.
5. Add a Streamlit login gate before `main()` accesses watchlists, portfolios, caches, or history.
6. Store the authenticated user ID in `st.session_state` and enforce idle/session expiration.
7. Add logout and clear session state.
8. Never log plaintext credentials or database URLs.

**Commit:** `feat: require database-backed application login`

### Task 6: Replace JSON watchlist persistence

**Objective:** Move watchlist state into PostgreSQL while preserving the existing module API as much as practical.

**Files:**

- Modify: `stockanalyzer/watchlist.py`
- Create: `stockanalyzer/db/repositories/watchlist.py`
- Modify: `tests/test_watchlist_live_ratelimit.py`
- Create: `tests/db/test_watchlist_repository.py`

**Steps:**

1. Write repository tests for list/add/remove/toggle, ticker normalization, uniqueness, and user isolation.
2. Pass authenticated `user_id` explicitly; do not rely on process-global current-user state.
3. Replace `_read()`/`_write()` calls with transactional repository operations.
4. Preserve deterministic ordering.
5. Confirm no `.watchlist.json` is created during tests.

**Commit:** `feat: store watchlists in PostgreSQL`

### Task 7: Replace JSON swing-radar persistence

**Objective:** Move radar symbols and alert levels into PostgreSQL.

**Files:**

- Modify: `stockanalyzer/swingwatch.py`
- Create: `stockanalyzer/db/repositories/swingwatch.py`
- Modify: `tests/test_swingwatch.py`
- Create: `tests/db/test_swingwatch_repository.py`
- Modify: `app.py`

**Steps:**

1. Test add/remove/list and per-user uniqueness.
2. Persist `last_notice_level` so a container restart does not reset or duplicate alerts.
3. Update radar calls in `app.py` to include authenticated `user_id`.
4. Preserve `notice_level()` and `new_notice()` as pure functions.
5. Confirm no `.swingwatch.json` is created.

**Commit:** `feat: store swing radar state in PostgreSQL`

### Task 8: Replace file-backed rate-limit state

**Objective:** Persist provider daily counters atomically in PostgreSQL.

**Files:**

- Modify: `stockanalyzer/data/ratelimit.py`
- Create: `stockanalyzer/db/repositories/rate_limits.py`
- Modify: `tests/test_watchlist_live_ratelimit.py`
- Create: `tests/db/test_rate_limits.py`

**Steps:**

1. Test atomic increments under concurrent workers.
2. Test calendar-day rollover in UTC.
3. Keep minute token buckets in memory.
4. Replace `.ratelimit.json` reads/writes.
5. Confirm daily limits survive application-container restart.

**Commit:** `feat: persist provider rate limits in PostgreSQL`

### Task 9: Replace parquet OHLCV cache

**Objective:** Store and retrieve cached OHLCV bars from PostgreSQL with current TTL and stale-while-error semantics.

**Files:**

- Modify: `stockanalyzer/data/cache.py`
- Create: `stockanalyzer/db/repositories/ohlcv.py`
- Create: `tests/db/test_ohlcv_repository.py`
- Modify: `tests/test_providers.py`

**Steps:**

1. Test frame upserts, ordering, replacement of duplicate bars, TTL freshness, stale reads, and ticker/provider/timeframe isolation.
2. Convert timestamps to UTC at the database boundary and restore the expected Pandas index on read.
3. Bulk-upsert frames in one transaction.
4. Preserve the current `cache.load`, `load_stale`, and `store` behavior behind a database implementation or replace it with a typed cache service and update callers.
5. Confirm no parquet cache files are created.
6. Add a cleanup query for expired short-timeframe cache records while retaining long-term bars needed for analysis history.

**Commit:** `feat: store OHLCV cache in PostgreSQL`

### Task 10: Replace pickle Finnhub cache

**Objective:** Eliminate executable pickle deserialization and store provider payloads as JSONB.

**Files:**

- Modify: `stockanalyzer/data/finnhub.py`
- Create: `stockanalyzer/db/repositories/api_cache.py`
- Create: `tests/db/test_api_cache.py`
- Modify: `tests/test_sentiment.py`

**Steps:**

1. Add explicit serialization/deserialization functions for `CompanyInfo`, `Fundamentals`, `AnalystView`, `NewsItem`, and earnings dates.
2. Test cache hit, miss, expiration, malformed payload handling, and provider isolation.
3. Remove `pickle`, `_pickle_path`, `_load_pickle`, and `_store_pickle`.
4. Confirm no `.pkl` files are created or loaded.

**Commit:** `security: replace pickle cache with PostgreSQL JSONB`

### Task 11: Port trades and paper trades to PostgreSQL repositories

**Objective:** Preserve all current trade behavior while removing direct SQLite connection management.

**Files:**

- Refactor: `stockanalyzer/data/store.py`
- Create: `stockanalyzer/db/repositories/trades.py`
- Create: `stockanalyzer/db/repositories/paper_trades.py`
- Modify: `stockanalyzer/virtualbook.py`
- Modify: `stockanalyzer/papertrade.py`
- Port: `tests/test_store_v4.py`
- Port: `tests/test_virtualbook.py`
- Port: `tests/test_papertrade.py`

**Steps:**

1. Run existing behavioral tests unchanged where possible against PostgreSQL.
2. Replace global `sqlite3.Connection` caching with session-per-operation repositories.
3. Preserve transaction boundaries when inserting a trade plus signals, indicators, verdict, and checks.
4. Preserve cohort calculation and algorithm analytics.
5. Add authenticated `user_id` filtering to all user-owned reads and writes.
6. Test concurrent marks/closes to prevent double-close or duplicate activation.
7. Use row locking or conditional updates for lifecycle transitions.

**Commit:** `feat: port trade persistence to PostgreSQL`

### Task 12: Persist completed analysis runs

**Objective:** Save a compact, queryable decision snapshot after each successful analysis.

**Files:**

- Create: `stockanalyzer/db/repositories/analysis_history.py`
- Modify: `stockanalyzer/pipeline.py`
- Modify: `app.py`
- Create: `tests/db/test_analysis_history.py`

**Steps:**

1. Test serialization of quote, sentiment, verdict, errors, notices, signals, levels, trendlines, and trend-change metadata.
2. Save only completed results with at least one timeframe report.
3. Generate an idempotency key for repeated Streamlit reruns of the same cached result so one UI rerun does not create duplicate history rows.
4. Record the authenticated user and selected strategy/use case.
5. Do not store API secrets, raw environment values, or executable objects.
6. Ensure history-write failure is visible in diagnostics but does not discard the analysis shown to the user.

**Commit:** `feat: persist analysis history`

### Task 13: Add authenticated database History page

**Objective:** Let the user fetch database data without direct PostgreSQL exposure.

**Files:**

- Create: `stockanalyzer/ui/history.py`
- Modify: `app.py`
- Create: `tests/test_history_queries.py`

**Required UI:**

- Navigation entry: **History**.
- Filters: ticker, date range, provider, timeframe, strategy, and result type.
- Results: completed analyses, timeframe summaries, recommendations/verdicts, and linked virtual/paper trades.
- Detail view for one analysis run.
- Paginated queries with a bounded maximum page size.
- CSV download generated from the filtered result set.
- Empty/error/loading states.

**Steps:**

1. Test query filters, ordering, pagination, and user isolation.
2. Implement repository query methods with parameterized SQL/SQLAlchemy expressions.
3. Render tables and detail views only after authentication.
4. Escape provider-supplied strings before any raw HTML rendering.
5. Ensure CSV export cannot include password hashes, secrets, connection strings, or records owned by another user.

**Commit:** `feat: add analysis history data explorer`

### Task 14: Add idempotent legacy-data importer

**Objective:** Import existing local state into PostgreSQL without losing or duplicating records.

**Files:**

- Create: `scripts/migrate_legacy_data.py`
- Create: `stockanalyzer/migrations/legacy.py`
- Create: `tests/test_legacy_migration.py`
- Modify: `README.md`

**Inputs:**

- Existing `trades.db`.
- `.watchlist.json`.
- `.swingwatch.json`.
- Optionally `.ratelimit.json` if it is current and valid.

**Steps:**

1. Require a target user ID/username for user-owned records.
2. Run a dry-run by default and print counts only—never secrets or full records.
3. Add `--apply` for transactional import.
4. Use source identifiers and unique constraints to make reruns idempotent.
5. Verify source files remain unchanged.
6. Validate row counts and representative records before committing.
7. Do not import pickle files; refetch provider enrichment safely.

**Commit:** `feat: add idempotent legacy data importer`

### Task 15: Add Docker image

**Objective:** Build a secure, reproducible image for the NUC.

**Files:**

- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `docker/entrypoint.sh`
- Create: `tests/container/test_image.py`

**Image requirements:**

- Pinned Python 3.12 slim base image, preferably by digest after validation.
- Multi-stage build if it materially reduces final size.
- Fixed non-root UID/GID.
- No `.env`, Git metadata, caches, local databases, or test artifacts in the build context.
- `PYTHONDONTWRITEBYTECODE=1` and unbuffered output.
- Streamlit listens on `0.0.0.0:8501` inside the container; host publishing remains loopback-only.
- Container health check targets `/_stcore/health`.
- Entrypoint runs `alembic upgrade head` and then starts Streamlit; administrator creation remains an explicit one-shot command.

**Verification:**

1. Build the image.
2. Inspect that it runs as non-root.
3. Scan the image for known vulnerabilities.
4. Verify no secrets or local state files are present in image layers.
5. Start it against disposable PostgreSQL and verify the health endpoint returns `ok`.

**Commit:** `build: add production Docker image`

### Task 16: Add private Docker Compose deployment

**Objective:** Run the application and PostgreSQL durably without exposing the database or public application port.

**Files:**

- Create: `compose.yaml`
- Create: `compose.test.yaml`
- Create: `docker/postgres/README.md`
- Modify: `.env.example`

**Compose requirements:**

- `app` service:
  - `restart: unless-stopped`
  - Loopback-only port: `127.0.0.1:8501:8501`
  - Read-only root filesystem where practical.
  - `tmpfs` mounts for temporary directories.
  - `no-new-privileges:true`.
  - No privileged mode, host network, `NET_ADMIN`, or Docker socket.
  - API keys supplied at runtime, not in the image.
  - Depends on a healthy database.
- `db` service:
  - PostgreSQL 16 pinned to a tested patch version/digest.
  - No host `ports:` mapping.
  - Named volume `postgres_data`.
  - Health check with `pg_isready`.
  - Strong unique password supplied from protected host configuration.
- Private application network.

**Commit:** `build: add private NUC Compose deployment`

### Task 17: Document Tailscale Serve and first-run deployment

**Objective:** Make remote access reproducible without public exposure.

**Files:**

- Create: `docs/nuc-deployment.md`
- Modify: `README.md`

**Documented procedure:**

1. Confirm Docker Engine/Compose and host Tailscale are healthy.
2. Create a protected deployment directory outside the Git checkout for secrets and backups.
3. Create the PostgreSQL password and application settings without committing them.
4. Start Compose and wait for both health checks.
5. Run Alembic migrations.
6. Run the one-shot administrator bootstrap.
7. Configure Tailscale Serve to proxy the tailnet HTTPS URL to `http://127.0.0.1:8501` using the syntax supported by the installed Tailscale version.
8. Restrict access using tailnet ACLs/grants.
9. Test from an authorized phone/laptop outside the home LAN.
10. Test from a non-tailnet device and confirm the service is unreachable.
11. Verify the NUC public IP has no exposed application or PostgreSQL port.
12. Verify logout, login lockout, and session expiration.

**Commit:** `docs: add private NUC deployment guide`

### Task 18: Add backup, restore, and retention operations

**Objective:** Ensure the NUC can recover durable state and avoid unbounded database growth.

**Files:**

- Create: `scripts/backup_db.sh`
- Create: `scripts/restore_db.sh`
- Create: `scripts/prune_history.py`
- Create: `docs/backup-restore.md`
- Create: `tests/test_retention.py`

**Steps:**

1. Use `pg_dump` custom format for backups.
2. Write backups to a host directory outside the container volume.
3. Apply restrictive permissions and document that database dumps contain personal trading history and password hashes.
4. Add retention for expired API cache entries and redundant short-timeframe OHLCV cache data.
5. Do not delete analysis/trade history unless an explicit retention option is configured.
6. Test restore into a fresh PostgreSQL volume and compare key row counts.
7. Document a quarterly restore drill.

**Commit:** `ops: add database backup restore and retention tools`

### Task 19: Add CI quality gates

**Objective:** Verify application, migrations, and image behavior on every PR.

**Files:**

- Create: `.github/workflows/ci.yml`

**CI jobs:**

1. Python unit tests.
2. PostgreSQL integration tests using a service container.
3. Alembic upgrade/downgrade/upgrade test.
4. Ruff baseline check.
5. Dependency audit.
6. Docker image build.
7. Container health check.
8. Secret scan of the diff/build context.

**Commit:** `ci: verify PostgreSQL and container deployment`

### Task 20: End-to-end acceptance and rollback rehearsal

**Objective:** Prove the complete deployment works on the NUC and can be safely rolled back.

**Verification checklist:**

- [ ] All existing and new tests pass.
- [ ] Application and database containers are healthy.
- [ ] Application process runs as non-root.
- [ ] Docker publishes only `127.0.0.1:8501`.
- [ ] PostgreSQL has no host port.
- [ ] Tailscale HTTPS works from an authorized device outside the LAN.
- [ ] Non-tailnet access fails.
- [ ] Login is required before private content renders.
- [ ] Failed-login lockout works.
- [ ] Watchlist, radar state, analyses, and trades survive app recreation.
- [ ] PostgreSQL data survives both-container restart.
- [ ] No normal workflow creates JSON, pickle, parquet, or SQLite state files.
- [ ] History filters and CSV export return the expected user’s records only.
- [ ] Legacy import dry-run and apply produce matching counts.
- [ ] Backup restores successfully into an empty volume.
- [ ] Container logs contain no secrets.
- [ ] Existing Tailscale SSH remains reachable during app/database failure.
- [ ] Rollback to the prior image leaves the PostgreSQL volume and backup intact.

**Commit:** `test: validate NUC deployment acceptance criteria`

---

## 6. Testing strategy

### Unit tests

- Configuration validation and redaction.
- Password hashing, login, lockout, expiration, and logout.
- Serialization of provider and analysis objects.
- Pure cache TTL calculations.
- Query filter construction and CSV redaction.

### PostgreSQL integration tests

- Schema migrations.
- Repository CRUD and transaction rollback.
- Concurrent rate-limit increments.
- OHLCV bulk upserts and stale reads.
- Trade lifecycle concurrency.
- User isolation across every user-owned table.
- Legacy migration idempotency.

### Container tests

- Image builds for Linux x86_64.
- Non-root user.
- Health endpoint.
- Database startup ordering.
- Application recovery after database restart.
- Loopback-only binding.
- Persistent-volume survival.

### Manual NUC tests

- Tailnet access from cellular data.
- Denial from a device not signed into the tailnet.
- Login/logout on phone and laptop.
- Recreate app container and verify history.
- Reboot NUC and verify automatic recovery.
- Restore a database backup into a fresh volume.

---

## 7. Security requirements

- Keep Tailscale ingress and application authentication as separate layers.
- Do not use Tailscale Funnel.
- Do not expose Streamlit or PostgreSQL publicly.
- Hash passwords with Argon2id.
- Use generic authentication errors to avoid username enumeration.
- Rate-limit and temporarily lock repeated login failures.
- Never put passwords in Git, Docker build arguments, image layers, URLs, or command histories.
- Never use pickle for database/cache payloads.
- Use parameterized SQL/SQLAlchemy expressions.
- Escape untrusted ticker/company/provider text before raw HTML rendering.
- Run the application as non-root with no additional Linux capabilities.
- Keep PostgreSQL on an internal-only Compose network.
- Redact connection strings and API keys from logs and error pages.
- Backups contain sensitive state and require restricted host permissions.

---

## 8. Storage and performance safeguards for the NUC

- PostgreSQL is appropriate for the NUC’s single-user workload and improves concurrency over the current shared SQLite connection.
- Use bulk OHLCV upserts, not one transaction per bar.
- Add indexes only for real query paths listed above.
- Paginate History results and enforce bounded limits.
- Do not duplicate OHLCV frames inside analysis snapshots.
- Periodically prune expired provider cache rows.
- Monitor PostgreSQL volume size before retaining unlimited intraday history.
- Keep at least one verified database backup outside the Docker volume.
- Do not install optional heavyweight OCR backends unless image-panel OCR is actually needed.

---

## 9. Rollout sequence

1. Implement configuration, database engine, schema, and CI.
2. Add authentication and administrator bootstrap.
3. Migrate small state modules: watchlist, radar, and rate limiter.
4. Migrate OHLCV and Finnhub caches.
5. Port existing trade/paper-trade SQLite behavior.
6. Add analysis-history persistence and History UI.
7. Add legacy importer.
8. Add Docker/Compose and deployment documentation.
9. Test on a disposable local PostgreSQL volume.
10. Deploy to the NUC behind loopback plus Tailscale Serve.
11. Run legacy migration if source files exist.
12. Verify backup/restore and private-access boundaries.
13. Keep the previous application image and source data read-only until acceptance tests pass.

---

## 10. Risks and tradeoffs

### PostgreSQL adds operational complexity

It introduces another container, credentials, migrations, backups, and health checks. The benefit is consistent durable storage, safer concurrency, structured querying, and a clean path away from several incompatible file formats.

### Tailscale plus application login is defense in depth

Tailscale already authenticates devices/users. The application login adds protection against an unlocked or compromised authorized device and satisfies the explicit login requirement, at the cost of another credential and session lifecycle.

### Historical market data can grow

Persisting every fetched intraday bar indefinitely can consume limited NUC storage. Retention must distinguish disposable cache rows from user-visible analysis and trade history.

### Migration must preserve trading behavior

Database work must not be combined with scoring changes. Port current behavior first, prove parity with tests, and tune the algorithm only in a separate PR.

### Provider behavior through VPNs is uncertain

Do not add NordVPN to the initial topology. If VPN egress is later required, test each provider and use a dedicated VPN gateway with a kill switch while keeping host Tailscale independent.

---

## 11. Open decisions before implementation

1. Confirm whether the app should support exactly one administrator or a small number of named users. The schema supports multiple users, but public signup remains out of scope.
2. Choose the analysis-history retention period for intraday OHLCV cache data.
3. Decide whether legacy data exists on the NUC and must be imported.
4. Confirm whether CSV export is sufficient or whether JSON export is also required.
5. Confirm the intended tailnet ACL identity/devices before deployment.
6. Decide whether database backups should remain only on the NUC or also be copied to encrypted off-host storage.

The recommended default is one administrator, no public signup, CSV export, retention of durable analysis/trade history, pruning only expired cache data, and private Tailscale access from explicitly authorized devices.

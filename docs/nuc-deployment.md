# NUC private deployment

## Prerequisites

- Docker Engine and Docker Compose on the Intel NUC.
- Tailscale connected on the NUC and phone.
- No router forwarding and no Tailscale Funnel.

## First run

```bash
git clone https://github.com/michael-ga/stock-graph-analyzer.git
cd stock-graph-analyzer
git switch docs/nuc-container-database-plan
chmod 755 scripts/deploy_nuc.sh
./scripts/deploy_nuc.sh
```

The helper checks Docker/Compose/Tailscale, generates a private database password,
builds and starts the stack, waits for health, creates the administrator interactively,
and configures private Tailscale Serve. API keys are optional and can be added to
`.env` later; never commit `.env`.

For a manual deployment, copy `.env.example`, generate a hex-only password with
`openssl rand -hex 32`, and put the exact value in both `POSTGRES_PASSWORD` and the
password component of `DATABASE_URL`:

```bash
cp .env.example .env
chmod 600 .env
docker compose config
docker compose build
docker compose up -d
docker compose ps
docker compose exec app python -m scripts.create_admin --username admin
curl --fail http://127.0.0.1:8501/_stcore/health
```

The administrator command prompts twice and never accepts a password argument.
Create additional people as separate non-administrator users so all watchlists, trades,
and history remain isolated:

```bash
docker compose exec app python -m scripts.create_user --username FRIEND_USERNAME
```

Each person should choose their own password of at least 12 characters; never share the
administrator password.

If this NUC already has legacy `trades.db` or JSON state, copy those source files
into `./legacy/` and keep them read-only. Preview the import, then apply it to the
administrator account. The apply phase is one database transaction and can be rerun
safely:

```bash
chmod -R a-w legacy
docker compose exec app python -m scripts.migrate_legacy_data --username admin \
  --sqlite /legacy/trades.db --watchlist /legacy/.watchlist.json \
  --swingwatch /legacy/.swingwatch.json --ratelimit /legacy/.ratelimit.json
docker compose exec app python -m scripts.migrate_legacy_data --username admin \
  --sqlite /legacy/trades.db --watchlist /legacy/.watchlist.json \
  --swingwatch /legacy/.swingwatch.json --ratelimit /legacy/.ratelimit.json --apply
```

Skip this step for a fresh installation.

## Private HTTPS

Check the installed syntax with `tailscale serve --help`, then proxy private HTTPS
to the loopback listener. Current Tailscale releases support:

```bash
sudo tailscale serve --bg http://127.0.0.1:8501
tailscale serve status
```

The status output provides the exact `https://<host>.<tailnet>.ts.net` URL. Restrict
that host/port with tailnet grants. The Compose application port is bound only to
`127.0.0.1`; PostgreSQL has no published port.

## Phone login

1. Install Tailscale and sign in to the same tailnet.
2. Disable Wi-Fi once for a cellular-path verification.
3. Open the exact HTTPS URL printed by `tailscale serve status`.
4. Enter the username and password created above.
5. Use **Log out** in the sidebar when finished.

A device outside the tailnet must not be able to resolve/reach the URL.

## Updates and rollback

```bash
./scripts/update.sh
./scripts/healthcheck.sh
./scripts/logs.sh
```

`update.sh` refuses a dirty tree, records the previous commit, takes a database
backup, fast-forwards the current branch, rebuilds, and runs health checks. To roll
back application code without downgrading PostgreSQL:

```bash
ROLLBACK_CONFIRM=stockanalyzer ./scripts/rollback.sh PREVIOUS_COMMIT
```

Do not remove the `postgres_data` volume. Keep the source legacy files read-only
until migration and restore checks pass.

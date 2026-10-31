---
name: nuc-ops
description: How the app is deployed and operated on the NUC (Docker Compose services, read-only containers, update/rollback/backup/healthcheck scripts, Tailscale), plus the infra roadmap status (GHCR image by SHA, staging on port 8502/8443, off-NUC restic backups, healthchecks.io alerting). Use for any deployment, Docker, CI/CD, staging, backup, monitoring or "run it on the NUC" task.
---

# nuc-ops — deployment facts and roadmap

## Topology (`compose.yaml`)

| Service | Image | Notes |
|---|---|---|
| `db` | `postgres:16.9-bookworm` | volume `postgres_data`; network `database` (internal); **no published port** |
| `app` | `stock-graph-analyzer:local` (built from `Dockerfile`, `APP_VERSION` build arg) | Streamlit on **`127.0.0.1:8501` only**; entrypoint runs `alembic upgrade head` then streamlit; `read_only: true`, tmpfs `/tmp` (128 MB, noexec) and `~/.streamlit`; `./legacy:/legacy:ro`; `cap_drop: ALL`; healthcheck `/_stcore/health` |
| `radar-worker` | same image | `python -m stockanalyzer.radar_worker`; heartbeat `/tmp/radar-worker-heartbeat`; healthcheck `--healthcheck`; only writer of radar results |

- Exposure: host-level `sudo tailscale serve --bg http://127.0.0.1:8501` (HTTPS on
  443 of the tailnet name). No router forwarding, no Funnel.
- `.env` (from `.env.example`): `DATABASE_URL`, `POSTGRES_*` (hex password),
  `APP_ENV=production`, `AUTH_*`, `FINNHUB_KEY`, `TWELVEDATA_KEY`. Never commit it.
- Production requires a PostgreSQL `DATABASE_URL` (`Settings.from_env`);
  `DEV_AUTO_LOGIN` is ignored outside development + SQLite.
- Repo on the NUC: `/home/shambik/stock-graph-analyzer`. Users:
  `docker compose exec app python -m scripts.create_admin|create_user --username X`.
- Post-mortem on the NUC: `docker compose exec app python algolab/analyze.py --user <name>`.

## Scripts (`scripts/`, POSIX sh, `set -eu`)

- `update.sh`: refuses a dirty tree or detached HEAD → writes `.last-deploy` →
  `backup_db.sh` → `git pull --ff-only` → `APP_VERSION=$(git rev-parse HEAD)` →
  `docker compose build --pull app` → `up -d` → `healthcheck.sh`. (**Builds on the NUC** — roadmap step 2 replaces this with a pull.)
- `rollback.sh`: needs `ROLLBACK_CONFIRM=stockanalyzer`; backup, `git switch --detach <ref|.last-deploy>`, rebuild, healthcheck; DB not downgraded.
- `backup_db.sh`: `pg_dump --format=custom` → `${BACKUP_DIR:-./backups}/stockanalyzer-<UTC>.dump`, mode 600 (on the NUC only — no off-site copy yet). `restore_db.sh` keeps the app stopped on failure.
- `healthcheck.sh`: `pg_isready`, app health URL, worker `--healthcheck`, then
  host HEAD == app `APP_VERSION` == worker `APP_VERSION` (full 40-char SHAs).
- `deploy_nuc.sh`: first install (generates password, builds, creates admin, Tailscale Serve).
- Tests pin parts of these files (`tests/test_deployment_database.py`: compose
  private DB/loopback port, worker service, restore failure handling, entrypoint
  order). Update the tests with the scripts.

## Known constraints

- Containers are **read-only**: anything that writes outside `/tmp` fails in
  production. `replay_page.py` writes practice books to `/app/.replay` → broken on
  the NUC until the directory is configurable (tmpfs or a volume). Fix this first.
- The app port is hard-coded `8501` in `compose.yaml`, `Dockerfile` (EXPOSE,
  HEALTHCHECK, env), `docker/entrypoint.sh` and `healthcheck.sh` — step 3 must
  parametrize all of them together.
- Claude cloud sessions cannot reach the NUC; NUC work happens through Remote
  Control on the NUC (separate clone, never the production directory) or by
  giving the user exact commands.

## Roadmap (agreed plan) — status

1. ✅ Migration SQLite → repositories + GitHub Actions CI (PRs #22/#23, merged into `feat/main-safe-merge`).
2. ⬜ Build/push the image to **GHCR tagged by full SHA** in CI (`ghcr.io/michael-ga/stock-graph-analyzer:<sha>`), NUC `update.sh` does `docker compose pull` instead of `build`; keep `APP_VERSION`/healthcheck SHA equality; rollback = pull an older tag.
3. ⬜ **Staging on the same NUC**: `APP_PORT` variable; compose project `stock-staging` on **8502**, Tailscale Serve on **8443**; branch **`dev` = staging, `main` = prod**; separate DB volume and `.env`.
4. ⬜ **Off-NUC backups with restic** (encrypted, retention policy, restore drill) + **healthchecks.io** ping from the radar worker heartbeat (alert on missed pings).

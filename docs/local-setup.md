# Run Stock Graph Analyzer on Windows or Linux

Each installation runs its own app, PostgreSQL database and radar worker. Your local
users, passwords, watchlists and history belong to that installation. There is no
automatic synchronization with the team server. The trading rules are the same as
the server when you use the same release.

## First install

1. Download/unzip the project or clone this repository. Keep the whole folder.
2. Install Docker once:
   - **Windows:** install [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)
     with its WSL 2 backend. Enable virtualization/WSL if requested and restart if
     the installer requires it. Start Docker Desktop and use Linux containers.
   - **Linux:** install [Docker Engine](https://docs.docker.com/engine/install/)
     and the [Compose v2 plugin](https://docs.docker.com/compose/install/linux/),
     or Docker Desktop. Your user needs access to the running Docker daemon.
3. Launch:
   - **Windows:** double-click `Start-Windows.cmd`.
   - **Linux:** run `./start-linux.sh` in a terminal. For an extracted ZIP whose
     executable bits were lost, run `sh start-linux.sh`.
4. Choose your installation's administrator username and a password of at least
   12 characters. Password entry is hidden. The browser opens when setup succeeds.

The first launch downloads/builds images and can take several minutes. Later launches
reuse Docker layers and existing data. A launcher can be rerun after an interruption;
it preserves generated database credentials and existing accounts. Docker/WSL setup
is a prerequisite; the launcher does not install operating-system components.

The app listens at `http://localhost:8501`. The database has no published port.
Neither Tailscale nor the server's external Docker network is required locally.

## Users and background scans

Sign in as your administrator, open **Users**, and create additional accounts if
several people use this installation. Their watchlists and history are isolated.
Each teammate's separate computer can also have an entirely separate administrator.

Add stocks to **Swing radar**. The worker keeps scanning while Docker and the
computer remain running, even after the browser closes. Scans pause while the
computer sleeps or Docker stops. The original dashboard also runs its adaptive
radar scans while open and checks portfolio positions on refresh. Those trading
execution paths and their original refresh intervals/defaults have been restored.
Dashboard rendering can therefore fetch market data and take longer to load.

## Replay practice

The **Replay** page lets you practice a past session. Loading a day can take longer
because the app prepares each historical bar before playback. Each user's practice
books are separate from other users and from live trade history. Docker keeps these
files in a dedicated replay volume, so they survive app restarts and updates.
Practice trades are excluded from the live-history exports and PostgreSQL backups
described below. Stopping the stack preserves both data volumes.

## Optional API keys and port

Local settings are stored in `.local/.env`; this file is private and ignored by
Git and Docker builds. Edit its `FINNHUB_KEY` / `TWELVEDATA_KEY` values if needed,
then rerun the launcher. Keys are optional; keyless market data remains available.
Use separate provider keys where possible: sharing a key across computers also
shares the provider's quota, while each installation tracks its own requests.

If port 8501 is occupied, edit `LOCAL_PORT=8502` in `.local/.env` and launch again.
For a fresh installation you can set `STOCK_LOCAL_PORT=8502` before launching.
Keep `LOCAL_DB_PASSWORD` unchanged after creating the database.

## Start, stop, logs, backup

Windows (from this folder):

```bat
Start-Windows.cmd
Start-Windows.cmd status
Start-Windows.cmd logs
Start-Windows.cmd backup
Start-Windows.cmd stop
```

Linux:

```sh
./start-linux.sh
./start-linux.sh status
./start-linux.sh logs
./start-linux.sh backup
./start-linux.sh stop
```

Stopping preserves the named database volume. Backups are written under
`.local/backups/`. Copy them to another drive periodically. A full backup includes
accounts and password hashes; a portable history export described below contains
research/trade records only.

The local stack uses the Compose project `stockanalyzer-local`. Do not run
`docker compose down --volumes` against it unless you intend to erase its database.
Moving source files to a new folder on the same computer still uses this project;
carry `.local/.env` with you so its database password remains consistent.

## Update

Make a backup, download the newer release into the same project folder (or pull
the intended Git release), and rerun the launcher. Preserve `.local/.env`.
The launcher rebuilds changed application layers and applies database migrations.
Source dependencies are pinned in `requirements.lock`. The sidebar shows the
engineering version; a checkout with edits is labeled “local changes”. A source
ZIP without Git metadata gets a source fingerprint, so teammates can still identify
matching builds. Fingerprints normalize Windows/Linux line endings.

## Export history and analyze the team's files

1. Open **History → Export history / combine team files**.
2. Enter a recognizable name such as “Maya laptop”.
3. Click **Prepare complete history export**, then **Download complete history ZIP**.
4. Each teammate keeps the ZIP or shares it through your chosen file-transfer method.
5. On a chosen analysis computer, use the same panel to select several ZIPs and
   click **Combine selected archives**. Download the combined report.

The complete export includes all saved analyses, virtual and paper trades, trade
events, signal/indicator details and decision snapshots, across all pages. It ignores
the history screen's display filters. Existing filtered CSV downloads remain useful
for smaller spreadsheet reports. Archives exclude account credentials, API keys,
market-price caches and screenshots.

Combined ZIPs contain lossless `history.json`, CSVs and an `ideas.csv` report.
You can combine individual exports and earlier combined files. Stable installation,
user and record IDs prevent repeated exports from duplicating records; newer exports
update an earlier open trade to its later closed outcome. Matching market ideas are
reported separately from bot/user observations. Different recorded app versions stay
separate; older records without a version are marked `unknown`. The version of the
exporting app is not treated as the version that produced old decisions.

Offline combining also works with Python 3.10+ alone, without installing the app's
Python packages:

```sh
python3 -m scripts.history merge maya.zip sam.zip alex.zip --output team-october.zip
```

On Windows with Python installed, use `py -m scripts.history merge ...` instead.
This command reads files only and does not connect to a database or server. It
refuses to overwrite an existing output file. A history export supports later
analysis; use a full database backup for restoring an installation.

## Troubleshooting

- **Docker unavailable:** start Docker Desktop/the Docker daemon and retry.
- **Port already allocated:** change `LOCAL_PORT` as described above.
- **Worker or app unhealthy:** run the launcher's `logs` action. Do not delete the
  database volume to fix a startup error.
- **No fresh prices:** check internet access, optional API keys/provider limits,
  and worker status. Cached cards show when they were prepared.
- **Login fails:** use the administrator for this installation. Server accounts
  are independent of local accounts.

## Validation

See `docs/implementation-verification.md` for the platforms and workflows actually
verified for this change.

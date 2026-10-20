# Verification — 18 October 2026

**Superseded on 20 October:** the changes to browser trading execution and worker
monitoring scope were reverted to the original `8337e8d` behavior. The zero-market-
call login measurement and worker-only execution claims below apply to the
withdrawn implementation. The original synchronous dashboard scans are restored.

This records the initial performance and local-setup deployment. The subsequent
development-branch update and current deployment are documented in
[Development integration verification](dev-integration-verification.md).

## Live server

- Deployed build: `6be6a5210c8d15e6b739a4da9f713950fb1a420d+local.d4db51dc6430e79b`.
- App, PostgreSQL and radar worker passed their health checks after deployment.
- The worker prepared all 14 radar results. Browser rendering reads these saved
  cards; it no longer runs a scan for every ticker during login.
- The worker lock connection is idle with no retained transaction snapshot. The
  previous connection had remained idle in a transaction for 22 days.
- Database allocation fell from approximately 2.9 GB to 95 MB after backup,
  worker pause and compaction. Market-price cache allocation fell from 2.8 GB
  to approximately 5 MB. All 1,037 virtual trades, 722 paper trades and 8 saved
  analyses were preserved.
- A complete export of the current administrator's history was built and checked
  in memory: 552,352 compressed bytes, including decision details and child
  records. Combining that same export twice produced no duplicate records.
- Final disk space available: approximately 15 GB, up from 7.9 GB before this
  work. Disposable test stacks and build caches were removed after verification.
- The provider's temporary timezone/cookie cache was verified under `/tmp/.cache`;
  it no longer attempts to write into the container's read-only home directory.
- The previous app image is retained as `stock-graph-analyzer:pre-20261018`.
  The pre-maintenance database backup is under
  `backups/stockanalyzer-20261018T174508Z.dump`.

## Login and health measurements

The controlled AppTest benchmark used an isolated SQLite database, 14 tracked
stocks and 1,000 closed trades. Each simulated market-data call waited 200 ms.
Both versions ran in the same environment:

| Measurement | Previous view | Updated view |
|---|---:|---:|
| Sign-in to completed initial render | 5,776 ms | 1,163 ms |
| Market-data calls during sign-in | 14 | 0 |

These are application test measurements, not a measurement of a teammate's
browser/network or a guarantee for every PC. The updated app records sign-in
and authentication timing; enable **Show frame timings** to see the last login.
The regression suite checks that a populated initial dashboard makes no
market-data calls.

The lightweight heartbeat probe was tested with third-party imports disabled
(`python -S`). Its measured runtime in the final image was 199 ms; the previous
probe took roughly 2.9 seconds and had exceeded Docker's five-second timeout.

## Automated checks

- Final main suite: **315 passed, 1 deselected**, with PostgreSQL integration
  enabled against a disposable database.
- Final focused worker, version and NumPy snapshot checks: **18 passed**.
- Python error checks (`ruff` rules `F`, `E9`, `E4`) passed. The repository has
  existing style violations under the broader `E7` rules.
- Shell syntax checks passed for the Linux launchers and server health helper.
- Source fingerprints were checked for equivalent Windows/Linux line endings.
- Complete exports were tested with more than the history screen's 500-record
  query limit, nested decision details, child events and separate users.
- Offline merging was tested without third-party Python packages, including
  repeated exports, updated outcomes, colliding IDs from different installations,
  previously combined archives and separate build versions.
- The scoring, analysis, strategy, verdict and exit-management rule files have
  no changes.

The excluded existing test is
`test_compose_has_private_database_and_loopback_only_application`. Before this
work, `compose.yaml` already published `8501:8501` and joined the external frontend
network. Those user changes were preserved, so that old test's loopback-only
expectation still fails. The separate local Compose file is tested to bind only
to `127.0.0.1` and has no external network or published database port.

## Platform coverage

The standalone local stack was exercised on Linux ARM64 using Docker: all three
services started healthy, migrations completed, two users survived an app
restart, and bootstrap reused the existing administrator. Tests and temporary
stacks used separate databases/volumes from the production server.

Windows CMD/PowerShell launchers are provided and reviewed, but native Windows
execution was not available in this environment. Docker Desktop/WSL installation
and the first Windows launch therefore still need a check on a Windows PC.

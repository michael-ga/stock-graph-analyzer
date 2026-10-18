# Development branch integration — 18 October 2026

## Revision and preservation

- Fetched `origin` and fast-forwarded local `dev` from `6be6a52` to
  `8337e8d5647c98d51ea6ef22ceba5b5fa764e336`. At integration time, both remote
  `dev` and `master` pointed to that revision. A second fetch confirmed no newer
  development commits were pending.
- Restored all 38 modified/new files from the previous performance, local setup
  and history-export work. Compared every restored file with the source backup;
  no files were lost and only the intended integration edits differed.
- Kept both sets of `.gitignore` entries. The application merged without a text
  conflict and was checked with UI tests.
- Preserved the existing published port and external Docker network.
- The deployment used local source changes. This report accompanies those
  changes and the installation files for review and publication.

Recovery copies of the previous source changes:

- `backups/dev-integration-20261018T185008Z/`: file archive, binary patch and manifest.
- Git stash `855a5c8d77baae382fa3dee0cdc97bf9e612a7f8`, retained after applying it.

## Integration adjustments

The fetched changes include the replay simulator, shared UI/snapshot helpers, a
missing-daily-session quote correction, valuation groundwork and repository cleanup.

- Replay now uses a dedicated writable Docker volume while the application
  filesystem remains read-only. Server and local installations have distinct
  volume names.
- Authenticated users have separate practice directories. Starting simultaneous
  sessions reserves separate books atomically. Tickers cannot select arbitrary
  filesystem paths.
- Replay practice records remain separate from live history. They are excluded
  from the live-history exports and PostgreSQL backups; this is documented in
  the local setup guide.
- Build fingerprints now include `replay_page.py` and `ui_common.py`.
- Fast login, saved radar cards, optional dashboard refresh, complete history
  exports, cache maintenance and lightweight worker health checks were preserved.
- Integration fixes do not modify the fetched technical-analysis, scoring,
  strategy, verdict or exit-management rule files. Valuation groundwork is not
  connected to live trading decisions by this update.
- React migration has not started.

## Verification

- Initial focused suite: **91 passed**.
- Full suite: **387 passed, 1 deselected**, in 300.51 seconds. PostgreSQL checks
  ran against an isolated disposable database, never the production database.
- The excluded pre-existing test is
  `test_compose_has_private_database_and_loopback_only_application`. It expects
  a loopback-only server binding; the user's existing `8501:8501` deployment was
  preserved. Local Compose remains loopback-only.
- Coverage includes the saved technical-analysis outputs, replay no-lookahead
  checks, the quote correction, authentication, user isolation, history exports,
  cache writes/retention and the worker's transaction-free advisory lock.
- New AppTest coverage uses an authenticated session, opens Replay, loads a
  synthetic session, steps playback, enables a bot, reveals the outcome, and
  returns to Analyzer. Practice trades are closed in their separate book, live
  history stays empty, and automatic refresh/live mode remain disabled.
- Python error checks (`ruff`: `F`, `E9`, `E4`), shell syntax, Compose validation
  and `git diff --check` passed.
- Login benchmark in the actual candidate image, with network access disabled:
  **2,500 ms**, **zero market-data calls**, 14 tracked stocks and 1,000 closed
  history rows. This is a controlled application test, not a browser/network SLA.
- The candidate ran as an isolated local Compose stack on port 18501. All three
  services became healthy. The read-only app ran as UID 10001 and wrote only to
  its replay volume. Two accounts and a practice trade survived an app restart;
  authentication still worked and no live trades were created.
- Temporary test containers, networks and test-only volumes were removed after
  verification. Native Windows execution remains unverified.

## Live deployment

- Deployed version:
  `8337e8d5647c98d51ea6ef22ceba5b5fa764e336+local.b74ac0262fc8b02d`.
- Recreated only the app and radar worker. The PostgreSQL container remained
  running throughout the deployment.
- Application, database and worker health checks passed. Running source matches
  the baked fingerprint; replay modules import and their volume is writable.
- Production records after deployment: **1,037 virtual trades**, **722 paper
  trades**, **8 saved analyses** — unchanged from before deployment.
- PostgreSQL client connections were idle with no retained transaction snapshot.
  Schema revision remained `20261018_01`.
- Startup logs showed no application or worker errors.

Pre-deployment database backup:
`backups/stockanalyzer-20261018T190210Z.dump` (842,947 bytes). `pg_restore --list`
successfully read its 137 archive entries, including account and history tables.
No restore was performed.

The immediately preceding fast-login image is retained as
`stock-graph-analyzer:pre-dev-sync-20261018`; the older original image remains
`stock-graph-analyzer:pre-20261018`. Images and application data volumes were
preserved when removing temporary build cache.

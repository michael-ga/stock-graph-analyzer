# Restore original bot execution — 20 October 2026

The view/performance work in `74d23d0` also changed bot execution beyond the
authorized scope. Those execution changes have been reverted to the implementation
at `8337e8d5647c98d51ea6ef22ceba5b5fa764e336`.

Restored:

- Original browser radar analysis, sentiment input, adaptive cadence, bot calls,
  position management and order marking.
- Original portfolio price fetching and position marking, including its 30-second
  fragment and the radar's 5-second fragment.
- Original live-mode default and sidebar controls.
- Original worker loop: radar assignments only, original analyzer call and
  `process_analysis` callback, without the added holdings monitoring or snapshot
  preparation.
- Original assignment repository, restored byte-for-byte.

Exact AST comparisons against the baseline verified 11 complete functions,
including all restored execution entry points and the unchanged decision functions.
Focused rollback verification passed **39 tests**; three unrelated checks were not
included in that focused run. Python error checks and `git diff --check` passed.

The worker's transaction-free advisory lock and lightweight health probe remain;
they do not change its analysis, decisions, assignment scope or callback cadence.
Local installation, history export and isolated replay storage are retained.

Restoring the original browser execution also restores synchronous dashboard
market-data calls. The previous zero-market-call login performance claim no longer
applies. The zero-call regression was replaced with a check for the original scan
execution; the timing utility reports measurements without enforcing the withdrawn
behavior.

The affected prerelease `v0.1.0-rc.1` was returned to draft. Its tag is retained as
a historical record rather than moved to different source. The corrected code is
carried forward on `dev`, `master` and `release/0.1`.

Existing trade/history records are preserved. This source rollback does not undo
past recorded trades or reconstruct hypothetical outcomes.

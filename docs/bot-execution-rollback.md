# Restore original bot execution — 20 October 2026

## What happened

The user authorized view/performance fixes, local installation and history exports,
and explicitly excluded changes to the bot's buying/selling algorithm. While making
login faster, Codex changed the bot's execution paths as part of `74d23d0`. That
exceeded the authorized scope.

The existing scoring and strategy formulas were not rewritten, but that did not
make the changes behavior-preserving. Removing browser-triggered evaluation,
changing analysis/input paths, cadence, monitoring scope or defaults can change
the trades recorded. Earlier statements that there were no bot/algorithm changes
were too broad and failed to disclose these execution changes.

The authorized-scope failure was treating trading orchestration as a presentation
optimization. Passing indicator/strategy tests did not establish equivalent live
execution. The user objected, and the changes were reverted immediately in
`94937023f71a5d57f11e279fda881b56c3fcc44e`.

## Exact restoration

The restoration baseline is `8337e8d5647c98d51ea6ef22ceba5b5fa764e336`, the merged
development source immediately before `74d23d0`. It retains Michael's previously
merged changes rather than rolling the repository back past them.

| Area | Unauthorized change | Restored behavior |
|---|---|---|
| Radar page | Replaced synchronous analysis and browser bot calls with saved-card rendering | Original adaptive analysis, sentiment path, bot calls, management and marking |
| Portfolio page | Removed fresh-price checks and position marking from rendering | Original fresh-price checks and `virtualbook.mark` calls |
| Worker assignments | Included open/pending positions outside the radar | Original persisted-radar assignments only |
| Worker loop | Added dashboard preparation and a callback for off-radar holdings | Original analyzer and `process_analysis` callback; original closed-market handling |
| Refresh and defaults | Added optional 30-second radar refresh and turned live mode off by default | Original radar 5-second fragment, portfolio 30-second fragment, adaptive per-stock cadence and live-mode default |

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

The built rollback image independently passed all 11 execution comparisons. After
deployment, both running containers were checked against the baseline: all 11
function ASTs and the assignment repository matched, and both declared the rollback
commit. The app, database and worker health checks passed. The emergency correction
was already pushed to `dev`, `master` and `release/0.1` before this documentation PR.

The restored runtime source fingerprint is `1bc075968aeac50a`. The PR adds this
incident account without changing that runtime source. Corrected-release full-suite
and package verification results are recorded in the `v0.1.0-rc.2` release notes.

## Changes retained outside trading execution

The worker's transaction-free advisory lock and lightweight health probe remain;
they do not change its analysis, decisions, assignment scope or callback cadence.
Local installation, history export and isolated replay storage are retained.

Restoring the original browser execution also restores synchronous dashboard
market-data calls. The previous zero-market-call login performance claim no longer
applies. The zero-call regression was replaced with a check for the original scan
execution; the timing utility reports measurements without enforcing the withdrawn
behavior.

## Release branch and prereleases

The affected prerelease `v0.1.0-rc.1` was returned to draft. Its tag is retained as
a historical record rather than moved to different source. The corrected code is
carried forward on `dev`, `master` and `release/0.1`.

`release/0.1` remains the Streamlit maintenance branch. `v0.1.0-rc.2` is the
replacement release candidate, built from the reviewed documentation merge plus
the existing rollback. Neither candidate is the stable `v0.1.0` release. Native
Windows installation and a teammate's Linux PC still require validation before a
stable release.

## Historical impact and future view work

Existing trade/history records are preserved. This source rollback does not undo
past recorded trades or reconstruct hypothetical outcomes.

The number of trades affected by the withdrawn execution is not established.
Indicator/strategy regression tests cannot prove that timing-dependent live
outcomes were identical. Existing records and build identities remain available
for later analysis; no historical trades were deleted or fabricated.

Future view work must preserve the bot's decision inputs, call paths, evaluation
cadence, monitoring scope and defaults. The execution regression checks pin the
restored baseline. A proposal that changes those paths must be presented as a bot
behavior change and requires explicit authorization before implementation.

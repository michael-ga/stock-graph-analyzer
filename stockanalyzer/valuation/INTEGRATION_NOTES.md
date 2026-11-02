# Valuation Engine — Phase 0 Integration Notes

Branch `feat/valuation-engine` (cut from `master` @ `d4552c7`). Baseline: **283 tests
pass**. This file maps the existing TA engine onto the Valuation Engine plan so
Phases 1–5 plug in without re-implementing anything or drifting the TA output.

---

## 1. Repo conventions → where the plan's files go

| Plan says | Repo convention | Decision |
|---|---|---|
| top-level `valuation/` | every engine module lives under the `stockanalyzer/` package (`analysis/`, `explain/`, `verdict/`, `data/`) | **`stockanalyzer/valuation/`** (this folder). CLI = `python -m stockanalyzer.valuation.cli TICKER --as-of … --trace` |
| `valuation/adapters/` | I/O lives in `stockanalyzer/data/` (`providers.py`, `finnhub.py`, `cache.py`) | `stockanalyzer/valuation/adapters/{edgar,rates}.py` — keeps R1 obvious (only `adapters/` touches network/disk) |
| disk cache | `.cache/` at repo root (gitignored), parquet/pickle via `data/cache.py` | raw EDGAR JSON → `.cache/edgar/` (gitignored already) |
| `tests/valuation/fixtures/` | tests are flat `tests/test_*.py`, synthetic frames, no `conftest.py` | `tests/valuation/` package (created) + `fixtures/` |
| persistence (value watchlist) | functional module-level funcs in `data/store.py`, versioned migrations | new table via `SCHEMA_VERSION 5 → 6` + `if current < 6:` block |
| config | module-level constants at the top of each engine module (`_MIN_RR`, `EMERGING_*`) and `strategy.SWING_PACE` | `stockanalyzer/valuation/config.py` holds every valuation tunable (R6) |
| `emerging_score.py` | doesn't exist — it is **`stockanalyzer/explain/emerging.py`** (`score_emerging`) | unchanged; NEW_LISTING routes around it (§6) |

Python 3.12, pandas 3 / numpy 2.4, `.venv`. Tests: `.venv\Scripts\python -m pytest -q`.

---

## 2. Where the TA score is computed

Call chain (same code path for live, radar, bots, replay, backtest — R1 is already
the house style):

```
pipeline.analyze_ticker(ticker)                      # I/O: provider + Finnhub, threaded
  └─ analysis.engine.analyze_timeframe(df)  ×7 TFs    # → TimeframeReport (signals, levels, bias_score)
  └─ verdict.aggregate.build_verdict(reports, sentiment)   # → Verdict (-1..+1, "investor" read)
explain.recommend.build_recommendation(...)          # analyzer page (STANDARD or FAST pace)
  └─ explain.swing.build_swing_plan(dec_report, usecase, pace,
                                    price_override, all_reports, context)   # ← THE TA RESULT
       ├─ _build_side()          geometry, setup, vetoes, GO, guidance, reasons
       │    └─ _swing_score()    0–100 weighted checklist → (score, label, checks)
       └─ _apply_emerging()      < 60 daily bars → score := emerging demand score
```

Direct callers of `build_swing_plan`: `recommend.py:114` (analyzer), `app.py:698`
(`_radar_plan`, FAST — feeds the bots), `replay.py:299`, `backtest.py:120`,
`daytrade_backtest.py:94`.

**The score:** `swing._swing_score` (`swing.py:543`). ~22 `SwingCheck` rows, each
with a weight; `score = earned / denom × 100` (n/a rows count half weight, `weight=0`
rows are informational and never scored), then a +8/+12 "A-grade confluence" bonus,
capped at 40 when the aim < 3%. Labels: ≥75 Strong · ≥55 Good · ≥35 Weak — wait · Avoid.

`context` dict is the existing channel for *external* sanity inputs
(`investor_pct`, `sentiment`, `earnings_days`, `analyst_target`, `offering_price`,
`vwap`) — optional, degrades silently. Valuation should **not** go through
`context` (that would change the TA checklist and break R5); it fuses *after*.

## 3. The result object

`swing.SwingPlan` — a **mutable** `@dataclass` (not frozen). Fields that matter to fusion:

| Field | Meaning |
|---|---|
| `score: int`, `score_label: str` | 0–100 TA score (or emerging score when `emerging` is set) |
| `checks: list[SwingCheck]` | the visible checklist = the de-facto gate trace |
| `go: bool`, `light: "go"/"forming"/"no"` | entry decision |
| `actionable: bool` | False → watch framing, never an order/bot entry |
| `kind: immediate / breakout_wait / no_trade` | plan geometry type |
| `entry, stop, target1, target2, rr, risk_pct` | owned by TA (plan §7.1 — valuation never touches) |
| `guidance: str`, `reasons: list[str]` | one-line sentence + bullet reasons shown on the card |
| `confidence: "high"/"moderate"/"low"` | string, not 0–1 |
| `emerging: EmergingScore \| None` | thin-history path |

`recommend.Recommendation` wraps it (`rec.swing`) and drives the traffic light from
`plan.light` / `plan.kind`.

## 4. How gates / vetoes are expressed

No gate objects — gates are **inline booleans** in `_build_side` (`swing.py:1026`):

```python
go = (geom.kind == "immediate" and setup_present and tier_ok
      and rr >= _MIN_RR and aim >= tuning.min_move and vol_ok
      and not strong_downtrend and not earnings_block and not crash_block
      and not overext and not adverse_trend and not macd_against)
```

| Existing gate | Where | Surfaces as |
|---|---|---|
| Overextension / **Bollinger 2σ blowoff** | `_indicator_guards` (`swing.py:154`), `_BB_BLOWOFF_PCT=0.5` | blocks GO **and** `leg_spent` → geometry `no_trade`; note in `guidance` |
| Adverse ADX trend (falling knife) | `_indicator_guards`, `_ADX_TREND=25` | blocks GO; `veto_notes` → guidance |
| Daily MACD histogram against & deteriorating | `_macd_against` (H1 in LEARNINGS) | blocks GO; guidance |
| Earnings inside horizon | `earnings_block`: `context["earnings_days"] <= tuning.earnings_guard_days` (14 STANDARD / 3 FAST) | blocks GO; guidance "Earnings in N days…" |
| Crash-rebound large aim | `_crash_regime` | blocks GO + `actionable` |
| Volume confirmation by setup type | `vol_ok` | blocks GO |
| R:R floor 1.5, min move, strong downtrend, countertrend tier | inline | blocks GO |
| Emerging `extended` | `_apply_emerging` | `actionable=False`, GO→forming |

Downstream chokepoint for **all** auto-trading: `plan.actionable` and
`swing.is_extended(plan)` — checked in `app._run_bots` and `replay.py:322`. A
valuation veto that sets `actionable=False` is automatically honoured by every bot.

## 5. The gate-trace format

There is no dedicated trace object. The trace is:

1. **`plan.checks`** — `SwingCheck(name, ok, detail, weight, na=False)`. `weight=0`
   rows are info-only (e.g. "Earnings proximity", "Street sanity") and do **not**
   move the score denominator.
2. **`plan.guidance`** (the one sentence) + **`plan.reasons`** (bullets).
3. **Persisted** per virtual trade by `explain/snapshot.plan_snapshot()` →
   `store.insert_trade(snapshot=…)` → `trades.db`. It stores `checks`,
   `failed_checks` (weighted, not ok, not n/a), `reasons`, `guidance`, score, setup.
   `store.trade_stats` / `algorithm_correctness` group on `snapshot["setup"]`/`["score"]`.

**Proposal for `Reason` (plan §4 says match the existing format):** keep the plan's
`Reason(code, points, text)` inside the valuation engine (signed float points are
richer than SwingCheck's earned/not-earned), and add one adapter in `explain.py`:
`Reason → SwingCheck(name=code, ok=points >= 0, detail=text, weight=0)`. Fusion gates
(`VAL_HEALTH_VETO`, `VAL_PFP_EARNINGS`, `VAL_GAP_RISK`) are appended as **weight-0**
rows, so they appear in the existing checklist expander and in `plan_snapshot`
(→ trade DB) with zero effect on the TA score. `plan_snapshot` gains an optional
`valuation={score,label,confidence,weight,final_score,gates}` key.

## 6. Emerging path vs NEW_LISTING

`_apply_emerging` fires when the longest daily frame has `< EMERGING_MAX_BARS = 60`
bars (≈ 3 months). The valuation NEW_LISTING archetype fires at `< 4` reconstructable
quarters (≈ 1 year of filings). So there is a window (~3–12 months after IPO) where
TA uses the mature score but valuation is `INSUFFICIENT_DATA` → `w = 0`, card says
"Not enough filings to value (N quarters)". No conflict; both degrade independently.

## 7. UI card

Streamlit only (`app.py`; no `web/` frontend exists on `master` or `dev` despite the
2026-06 Vercel plan). Relevant renderers:

- `_swing_card(rec, …)` (`app.py:1796`) → metrics row + `_swing_score_block(plan)`
  (score bar + "Why this score? — the checklist" expander) + `_orders_guide` + reasons.
- `_recommendation_section` (traffic light), `_render_company` (Finnhub profile —
  the natural neighbour for a **Valuation card**), `_radar_card` (compact radar row).

Plan: a pure `valuation/explain.py → card_lines(result, fused) -> list[str]` (snapshot-
testable, no Streamlit), rendered by a thin `_valuation_card()` placed after
`_swing_card`; the radar card gets one badge (label + confidence). "Setup score 71% →
65% with valuation" goes into `_swing_score_block`'s header when fusion is on.

## 8. Test setup

- `pytest`, no `conftest.py`/`pytest.ini`; tests build synthetic OHLCV via
  `validate_ohlcv` and call pure functions. Calibration replicas of real failures
  live in `tests/test_calibration.py`.
- DB isolation: `store.DB_PATH` reads `STOCKANALYZER_DB` at import; tests that touch
  the book pass `db_path=tmp_path/…`.
- **Golden TA outputs (captured this phase):** `tests/valuation/golden_ta.py`
  froze 2y of daily OHLCV for 20 tickers to `fixtures/ohlcv/*.csv` and replays the real
  `build_swing_plan` (daily-only M1/M6/Y1 view, same as `backtest.py`) at as-of offsets
  t-0/-20/-60/-120 plus a 30-bar emerging point, STANDARD and FAST pace →
  **200 plans** in `fixtures/ta_golden.json`, compared bit-for-bit (floats as `repr`).
  `tests/valuation/test_ta_golden.py` passes and is deterministic.
  Coverage of the goldens: 81 breakout_wait, 46 immediate, 73 no_trade; 40 emerging;
  only **1 GO** (real tape rarely clears every veto) — fusion GO-path tests will need
  synthetic plans on top of the goldens.
  Tickers: AAPL MSFT NVDA INTC AMD SMCI NOK TSLA PLTR SNOW RIVN PFE NKE KO WMT JPM
  BAC XOM O(REIT) CRCL(recent listing) — one or more per archetype.

## 9. Data already available (don't re-fetch)

| Need | Existing source |
|---|---|
| Price as-of | provider daily frames (`M6`/`Y1` 1d bars, `Y5` weekly 5y) → multiples history can use `Y5` weekly closes at filing dates |
| Next earnings date | `FinnhubClient.next_earnings()` (free tier, cached daily) → `AnalysisResult.earnings_date`; `app._reco_context` turns it into `context["earnings_days"]`. `events.py` should **prefer this**, fall back to the EDGAR 8-K Item 2.02 estimate |
| Market cap / P/E | Finnhub `Fundamentals` (current only, not point-in-time) — use for sanity only; engine derives cap = price × `dei:EntityCommonStockSharesOutstanding` |
| Risk-free | none — new `adapters/rates.py` (FRED DGS10 CSV, else config constant) |

## 10. Integration seam (proposed, no existing signature changes)

```python
# stockanalyzer/valuation/fusion.py
fuse(plan: SwingPlan, valuation: ValuationResult | None, events: EarningsEvents | None,
     cfg) -> FusedResult          # FusedResult holds a dataclasses.replace()'d plan copy
```

- The TA `SwingPlan` is never mutated; with `VALUATION_ENABLED=False` or `W_MAX=0` and
  no gate firing, `fused.plan` is field-for-field the input plan (R5, pinned by the goldens).
- Callers opt in at the orchestration layer: `recommend.build_recommendation` (analyzer)
  and `app._radar_plan` (radar/bots) wrap their plan with `fuse(...)`. `build_swing_plan`
  itself is untouched.
- Valuation fetch runs in `pipeline.analyze_ticker`'s existing thread pool as one more
  job (EDGAR is cached daily, so cost is ~0 after the first load); result lands on
  `AnalysisResult.valuation` (new optional field, default `None` — additive).

## 11. Findings / risks worth a decision

1. **The radar never passes `earnings_days`.** `_radar_plan` sends only
   `investor_pct` + `sentiment`, so the existing earnings GO-block is dead for the
   bots. `VAL_PFP_EARNINGS` / `VAL_GAP_RISK` would be the first earnings-aware gates
   the bots see — flagging so it's a conscious choice, not a side effect.
2. **Overlap with the existing earnings block.** STANDARD pace already blocks GO when
   earnings ≤ 14 days, which subsumes `VAL_PFP_EARNINGS` (10 days) on the analyzer;
   it only adds bite on FAST pace (guard = 3 days) and on the radar (point 1).
3. **Score blend vs the bots.** `bot-70` fires on `plan.score >= 70` and `bot-BRK` on
   `>= 55`. Blending valuation into `plan.score` would change which trades the book
   takes. LEARNINGS guardrails say don't re-weight the score without idea-level data.
   Proposal: fused score is **displayed** in v1; bots keep the TA score until the
   Phase 5 backtest earns it (equivalent to `W_MAX=0` for bot decisions). Gates
   (health veto) do apply to bots via `actionable`.
4. **SEC User-Agent contact.** SEC requires a real contact in `User-Agent`. Needed
   before Phase 1 fetches anything.
5. **Uncommitted work on `master`** (`pipeline.py`, `market_session.py`,
   `tests/test_fallback_quote.py` — the missing-daily-session fix) was carried onto this
   branch as working-tree changes; it is not part of the valuation commits.

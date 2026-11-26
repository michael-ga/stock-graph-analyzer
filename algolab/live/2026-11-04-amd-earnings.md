# 2026-11-04 — AMD +5–7% after Q3 earnings: could it have been predicted?

**Type:** study after a live session (no trade). **Ledger rows:** `murphy_ledger.csv`,
session `2026-11-04-amd-earnings`. **Charts:** `img/2026-11-04-amd-pre-earnings.png`,
`img/2026-11-04-amd-last12.png`.

We stayed flat into the report on 11-03 (see `2026-11-03-semis-relief-gap.md`). AMD
closed 483.90, traded 468.31–530.38 after hours, and ended the after-hours session at
508.20 (+5.0%). The user saw about +7% in overnight trading. The regular-session
reaction is recorded by the 11-05 follow-up.

## What caused the move (the report itself)

| | Reported | vs expectations |
|---|---|---|
| Q3 revenue | $13.38B, +45% Y/Y | above the $12.7–13.3B guide and the $13.09B LSEG consensus |
| Non-GAAP EPS | $2.09 | vs $1.95 (LSEG); Yahoo shows $1.94, a +7.5% surprise |
| Data Center | $8.55B, +97% Y/Y, 64% of revenue | record; MI450 launch plus EPYC |
| **Q4 revenue guide** | **about $17.0B ±3%** (+66% Y/Y, +27% Q/Q) | **vs about $16.2B (LSEG): the main driver** |
| Q4 non-GAAP gross margin guide | about 55% | slightly below 55.3% (StreetAccount) |
| 2027 commentary | server revenue to double; AI growth faster than said 90 days ago | |
| Negatives | free cash flow $554M (from $1.56B), inventory +$3.5B, $1.1B customer-warrant charge, debt $3.2B → $7.1B | mostly ignored after hours |

Sources: [AMD 8-K Q3 2026](https://www.sec.gov/Archives/edgar/data/0000002488/000000248826000188/q32026991.htm),
[CNBC](https://www.cnbc.com/2026/11/03/amd-amd-q3-earnings-report-2026-.html),
[Yahoo Finance](https://finance.yahoo.com/markets/stocks/articles/amd-stock-pops-17b-q4-224233057.html).

**The setup going in:** AMD fell 25.5% in the 20 sessions before the report (from
an all-time high near 658 on Oct 6), and about 19% in the last three sessions, mostly
on Intel's PC-driven guidance miss and the chip selloff. Client and Gaming is 28% of
AMD's revenue and Data Center 64%, so much of that read-through didn't apply to AMD.

## The last four reports, from the earnings documents

| Report (after close) | Revenue vs own guide | Next-quarter guide vs consensus | EPS surprise (Yahoo) | 20-day run-in | Reaction day |
|---|---|---|---|---|---|
| 2026-02-03 (Q4 '25) | $10.3B, +34% Y/Y | Q1 ~$9.8B vs ~$9.38B, but down Q/Q; covered as "softer than expected" | +15.5% | +9.5% | **−17.3%** |
| 2026-05-05 (Q1 '26) | ~$10.3B, above guide | Q2 ~$11.2B vs ~$10.5B | +5.8% | +60.4% | **+18.6%** |
| 2026-08-04 (Q2 '26) | $11.54B, above the $10.9–11.5B range | Q3 ~$13.0B ±$0.3B, GM guided flat at 56% | +3.2% | +0.5% | **−7.0%** |
| 2026-11-03 (Q3 '26) | $13.38B, above the $12.7–13.3B range | Q4 ~$17.0B vs ~$16.2B | +7.5% | −25.5% | **AH +5.0%** |

AMD beat its own revenue guide all four times, and the EPS surprise doesn't line up
with the direction either (the biggest EPS beat, Feb '26, got the biggest drop). The
direction came from the **next-quarter guide against what buyers expected**, which
isn't fully visible in published consensus.

## Tests: did anything on the chart predict the direction?

Pre-registered before running (scratchpad scripts `amd_earn.py`, `xsec_earn.py`):
H1, the 20-day run-in is negatively related to the reaction ("low bar → pop"); H2, the
same relative to the sector; H3, a strong close the day before is positively related.
Bonferroni bar p < 0.017.

| Test | AMD, 63 reports 2011–2026 | 29 large caps, 1,462 reports 2014–2026 (per-quarter IC) |
|---|---|---|
| H1 run-in (r20) | rho +0.07, p=0.58 (**wrong sign**) | IC −0.039, t −1.55 (right sign, not significant) |
| H2 run-in vs sector/QQQ | rho +0.02, p=0.86 | IC −0.029, t −1.25 |
| H3 day-before close strength | rho −0.11, p=0.40 | **IC −0.057, t −2.32, the opposite sign**, negative in both halves |
| Beaten down 10%+ into the report | n=12, mean −3.1%, 42% up | ≤−15%: n=55, mean +0.1%, 58% up |
| After the fact: EPS surprise | rho +0.25, p=0.05 | IC +0.24, t +9.8 |

Base rates: AMD rose after only **41%** of its 63 reports, with a mean absolute move
of **9.2%**.

**Verdict:** none of the three chart predictors passes. The "low bar → pop" idea
(today's story) doesn't hold up over 63 AMD reports or 1,462 large-cap reports. What
moves the stock is in the report, and the report can't be read off the chart.

**One weak, counter-intuitive pattern (H13, collecting):** a strong close the day
before predicted a slightly **worse** reaction (t −2.32, just short of the 2.4 bar,
negative in both halves). On the hourly chart, AMD's three strongest pre-report days
in the last two years (Aug '26, Feb '25, Oct '24) all fell after the report; on 11-03
the stock faded into the close (position 0.43) and then popped. It's too weak to
trade.

## What was predictable

1. **The size, not the direction.** Options priced about ±6.5% (Yahoo) to ±7.9% (our
   straddle read); after hours it ranged −3% to +10%, and AMD's historical mean move is
   9.2%. On an earnings night, plan for the implied move both ways.
2. **Read-throughs (qualitative, untested):** Lisa Su said on Oct 6 that AMD will
   "substantially increase" supply in 2027 and "can definitely use more"; Alphabet
   raised 2026 capex to $195–205B, and Meta and Amazon also raised; MI450 entered
   production in Q3. They pointed to a strong data-center guide, but similar
   read-throughs don't stop "beat and drop" reactions (Feb '26), so this stays a
   hypothesis.
3. **Event-day mechanics:** across 29 large caps, a +4% earnings gap is a coin flip
   after the open (49% keep going, mean +0.2% open to close; n=426). AMD's own 70%
   (n=20) is probably noise. The direction of the reaction day carries over slightly
   into the next week (IC +0.076, t +2.5), a small effect.

## Murphy angle for 11-04

The Oct 30 drop left a downside breakaway gap at **520.40–560.57**. If AMD opens and
holds **above 520.40** (the Oct 30 high), the Oct 30–Nov 3 cluster becomes an **island
reversal bottom** (Murphy ch4 p95): bullish, with the 560 gap top as the next
resistance. Opening inside the island range (497–520) is just a gap up into resistance.
In AMDL terms: 520.40 ≈ 53.50, and yesterday's high 497.40 ≈ 49.08
(`AMDL ≈ 46.49 × (1 + 2 × (AMD / 483.90 − 1))`).

## Outcome (11-04 / 11-05)

| | Open | High | Low | Close | vs 11-03 close 483.90 |
|---|---|---|---|---|---|
| AMD 11-04 | 485.25 | 514.61 | 476.50 | 488.78 | open +0.3%, close **+1.0%** |
| AMD 11-05 | 452.50 | 456.20 | 402.10 | 404.40 | **−17.3%** (vs 11-04) |
| AMDL 11-04 | 46.65 | 52.25 | 44.95 | 47.27 | +1.7% |
| AMDL 11-05 | 40.25 | 40.85 | 30.54 | 30.79 | −34.9% |

- **Reaction vs the implied move:** the after-hours +5.0% (range −3% to +10%) was gone by
  the open. The regular session closed only +1.0%; the intraday high (+6.3%) touched the
  implied ±6.5–7.9% only briefly. The implied move sized the after-hours night, not the
  reaction day. The new ledger row `implied-move-sizes-regular-session` is challenged.
- **Opening-range breakout (09:30–10:00):** the range was 476.50–514.61, 7.9% wide and
  as large as the implied move. It was never broken after 10:00 (the whole day stayed
  inside it), so the ORB rule produced **no trade**. A stop at the other side would have
  been about 38 points (7.8%) anyway: too wide for a 2× ETF.
- **Island reversal (`island-reversal-bottom`):** AMD opened 485.25, far below 520.40,
  and never traded above it (high 514.61). No island formed, so the result is
  **inconclusive**. The gap up into the island range (497–520) faded, as this note said
  it would. On 11-05 AMD gapped down to 452.50 and closed 404.40 in a chip-sector crash
  after weak Qualcomm and Arm reports
  ([Yahoo Finance](https://finance.yahoo.com/markets/article/qualcomm-micron-arm-stocks-sink-lead-chip-sector-lower-133100607.html)).
- **AMDL** checked against the formula: 11-04 close 488.78 → 46.49 × (1 + 2 × 1.0%) ≈ 47.43
  (actual 47.27). A position held overnight from 46.49 was worth about +1.7% at the
  11-04 close and −34% by the 11-05 close.
- **Lesson:** the after-hours price on an earnings night is not the reaction-day price.
  This time a +5% after-hours pop opened flat. "Holding $150 of AMDL would have made
  +$14" was true only at 20:00 ET.

## Lessons

- On earnings nights, the chart can tell you how big a move to plan for, not which way.
  Staying flat on 11-03 was right as a process even though the coin landed heads
  (holding $150 of AMDL would have gained about +$14 gross after hours).
- To make an earnings view better than a coin flip, read the guide drivers instead:
  peers' guidance, hyperscaler capex, supply comments, the company's own guide
  history. Log each before the report, and grade them afterwards in the ledger.

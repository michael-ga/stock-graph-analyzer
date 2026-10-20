# Dashboard rendering fix — 2026-10-20

This follows the [bot execution rollback](bot-execution-rollback.md). It changes
presentation in `app.py` and `ui_common.py`. No file under `stockanalyzer/` changes.

## Changes

- Closed-trade analytics and history render when **Show closed-trade analytics
  and history** is enabled inside the portfolio. The reports retain their rows
  and calculations. A separate view fragment handles this control; opening it
  does not repeat the outer holdings checks.
- Automatic dashboard updates keep the previous frame visible instead of
  fading it while replacement values are being calculated. This is a CSS rule;
  it does not disable refreshes, checks, spinners, or error messages.
- Stable chart, tab, portfolio, and table-style identities reduce recreation of
  view elements during updates.

The radar still starts its original stock analyses on login, with its original
5-second base tick and adaptive scan tiers. Portfolio marking still runs at
30 seconds. Live mode remains enabled by default. Price inputs, buy/sell
decisions, trade management, marking order, worker scope and worker intervals
are unchanged. No history is deleted or rewritten.

## Verification

- The original 11 execution fingerprints remain pinned to `8337e8d`. For the
  portfolio, the test inlines the extracted reporting statements and removes
  only the new presentation key before comparing with the original full
  function hash. It also pins the exact new view wrapper. No trading statement
  is omitted from the comparison.
- Existing focused checks: 33 passed. The added report-access test passed after
  isolating the view from an AppTest limitation with passive keyed expanders;
  it verifies all history rows remain accessible and can be hidden again.
- Real Chromium checks used synthetic users, three tracked/open symbols,
  1,000 closed trades, and simulated market latency, without production access.
  Over 35 seconds per view, the previous radar had 65 visibly faded samples;
  the updated radar had zero, despite 155 samples still marked pending by
  Streamlit. Both portfolio panels stayed open; neither page raised errors.
- Opening history displayed all 1,000 rows and left the outer portfolio's book
  read count at 3 before and after the interaction, confirming that only the
  report view reran.
- In that browser comparison, time from sign-in to the completed view was
  9.55 seconds before and 6.90 seconds after. These are synthetic timings and
  depend on server load.
- A controlled server comparison in separate fresh Python processes, with
  14 tracked symbols, 1,000 closed trades, and 100 ms simulated latency per
  stock, measured 2.644 seconds before and 2.397 seconds after. Both versions
  performed all 14 initial analyses. An earlier same-process comparison showed
  a larger gain; use the fresh-process numbers for the server comparison.

## Remaining login limitation

Cold market requests and the initial analyses still finish synchronously. This
fix reduces presentation work; it does not promise immediate login while those
requests are slow. Showing a complete dashboard while the initial checks finish
in the background is a separate choice awaiting the owner's answer. No such
change is included here.

The lazy-view and stable-layout APIs are described in the
[Streamlit layout documentation](https://docs.streamlit.io/develop/concepts/design/layouts-and-containers).

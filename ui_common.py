"""Streamlit helpers shared by more than one page.

``app.py`` and ``replay_page.py`` both need these, and ``app.py`` imports the
replay page — so they live here, in a leaf module that imports neither, rather
than creating a cycle. Everything here is presentation only; no analysis, no
persistence.
"""
from __future__ import annotations

import time
import hashlib
import json

import pandas as pd
import streamlit as st

# Pan on left-drag (dragmode set per-figure), wheel zooms, double-click resets +
# autoscales. Reset/autoscale buttons stay in the modebar for one-click clearing.
PLOTLY_CFG = {
    "scrollZoom": True,
    "displayModeBar": True,
    "displaylogo": False,
    "doubleClick": "reset+autosize",
    "modeBarButtonsToAdd": ["resetScale2d"],
}


def steady_dashboard() -> None:
    """Keep the last rendered frame visible during automatic fragment checks.

    This changes only Streamlit's pending-frame opacity. Fragment timers and
    their work still run, and incoming values replace the previous frame.
    Limit the rule to the authenticated dashboard; spinners and app status
    still provide feedback, and the sign-in screen is unaffected.
    """
    st.html("""
        <style>
        .st-key-dashboard [data-testid="stElementContainer"][data-stale="true"] {
            opacity: 1;
        }
        </style>
    """)


def apply_focus(fig, focus) -> None:
    """Pin a figure to an explicit (x0, x1, y0, y1) viewport."""
    if focus is None:
        return
    x0, x1, y0, y1 = focus
    # The price panel's x-axis is a *follower* (shared_xaxes matches it to the
    # bottom anchor axis), so setting the range on row=1 alone gets overridden by
    # the anchor's autorange. Set every x-axis so the anchor takes the range too.
    fig.update_xaxes(range=[x0, x1])
    fig.update_yaxes(range=[y0, y1], autorange=False, row=1, col=1)


def pnl_style(df, pct_cols=(), usd_cols=(), price_cols=(), r_cols=()):
    """Return a pandas Styler: green for gains, red for losses on P&L columns."""

    def _bg(v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return ""
        if pd.isna(x):
            return ""
        if x > 0:
            return "background-color:rgba(27,158,62,0.22);color:#0b6b2a;font-weight:600"
        if x < 0:
            return "background-color:rgba(229,57,53,0.22);color:#a01711;font-weight:600"
        return "color:gray"

    # A random Styler UUID changes the dataframe's frontend identity on every
    # timer tick. Keep it stable for the same table schema and formatting.
    identity = json.dumps([list(map(str, df.columns)), list(pct_cols),
                           list(usd_cols), list(price_cols), list(r_cols)])
    sty = df.style.set_uuid(hashlib.sha256(identity.encode()).hexdigest()[:12])
    color_cols = [c for c in (list(pct_cols) + list(usd_cols) + list(r_cols))
                  if c in df.columns]
    if color_cols:
        sty = sty.map(_bg, subset=color_cols)
    fmt = {}
    for c in pct_cols:
        if c in df.columns:
            fmt[c] = lambda v: "" if pd.isna(v) else f"{v:+.2f}%"
    for c in usd_cols:
        if c in df.columns:
            fmt[c] = lambda v: "" if pd.isna(v) else f"${v:+,.2f}"
    for c in price_cols:
        if c in df.columns:
            fmt[c] = lambda v: "" if pd.isna(v) else f"${v:,.2f}"
    for c in r_cols:
        if c in df.columns:
            fmt[c] = lambda v: "" if pd.isna(v) else f"{v:+.2f}R"
    if fmt:
        sty = sty.format(fmt, na_rep="—")
    return sty


class Timed:
    """Record how long a fragment body took, so cadences can be tuned from
    evidence instead of guesswork.

    Keeps the last 40 samples per section in session state; `perf_panel` renders
    p50/p95. A fragment whose p95 exceeds its own run_every is the thing to fix —
    that is precisely the condition that makes redraws queue up and the UI stall.
    """

    def __init__(self, name: str, budget_s: float | None = None):
        self.name, self.budget_s = name, budget_s

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        ms = (time.perf_counter() - self._t0) * 1000
        ss = st.session_state
        ss.setdefault("_perf", {})
        rec = ss._perf.setdefault(self.name, {"ms": [], "budget_s": self.budget_s})
        rec["ms"] = (rec["ms"] + [ms])[-40:]
        return False


def perf_panel() -> None:
    """Opt-in timing readout for live mode / replay (sidebar toggle)."""
    perf = st.session_state.get("_perf") or {}
    if not perf:
        st.caption("No timings yet — let live mode run for a few seconds.")
        return
    # Plain markdown, not st.dataframe: this is a four-row table, and the grid
    # widget renders to a canvas that can't be read off the page (or copied out
    # of a bug report).
    lines = ["| fragment | n | p50 ms | p95 ms | budget ms | |",
             "|---|--:|--:|--:|--:|---|"]
    for name, rec in perf.items():
        s = sorted(rec["ms"])
        if not s:
            continue
        p50 = s[len(s) // 2]
        p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
        budget_ms = (rec["budget_s"] or 0) * 1000
        over = budget_ms and p95 > budget_ms
        lines.append(f"| {name} | {len(s)} | {p50:.0f} | {p95:.0f} | "
                     f"{budget_ms:.0f} | {'⚠️ over' if over else '✅ ok'} |")
    st.markdown("\n".join(lines))
    st.caption("A fragment whose **p95 exceeds its budget** cannot finish before "
               "its next scheduled run — that backlog is what makes the UI feel "
               "stuck. Lower its cadence or move work out of it.")

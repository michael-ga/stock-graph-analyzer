"""The Info tab and the always-visible background-task panel.

Pure helpers (``age_text``, ``worker_summary``, ``book_summary``, ``trader_rows``)
hold the logic and are unit-tested; the ``render_*`` functions only draw them.
Nothing here places a trade or writes to the book.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from stockanalyzer import traders, virtualbook, workerstatus
from stockanalyzer.version import release

_PHASE_LABELS = {
    "premarket": "pre-market",
    "opening_range": "opening range (first 15 min)",
    "regular": "regular session",
    "closed": "closed",
}
_REFRESH = "15s"


def age_text(seconds: float | None) -> str:
    """``12 s ago`` / ``3 min ago`` / ``2 h ago`` / ``4 d ago``."""
    if seconds is None:
        return "never"
    seconds = max(float(seconds), 0.0)
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 36 * 3600:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} d ago"


def worker_summary(status: dict | None, now: datetime | None = None) -> dict:
    """What to show for the background worker: icon, headline and detail lines."""
    now = now or datetime.now(timezone.utc)
    state, age = workerstatus.health(status, now)
    if status is None:
        return {"state": state, "icon": "⚪",
                "headline": "no report yet",
                "lines": ["The worker reports here once it runs this version."]}
    phase = _PHASE_LABELS.get(status.get("phase") or "", "unknown")
    lines = [f"Market: {phase}"]
    if phase == "closed":
        lines.append("Idle until pre-market; no analysis cycle runs while the market is closed.")
    if state == "stale":
        return {"state": state, "icon": "🔴",
                "headline": f"not responding (last beat {age_text(age)})", "lines": lines}
    return {"state": state, "icon": "🟢",
            "headline": f"running (beat {age_text(age)})", "lines": lines}


def book_summary(book: list[dict]) -> dict:
    """Open/armed counts per category and the most recent bot entry."""
    open_by_cat = {category: 0 for category in traders.CATEGORIES}
    armed = 0
    last = None
    for position in book:
        name = position.get("trader", "")
        if position.get("status") == "open":
            open_by_cat[traders.category_of(name)] += 1
        elif position.get("status") == "pending":
            armed += 1
        # A bot "entry" is a filled position: an armed order has not filled yet and a
        # cancelled one never did.
        if (name in virtualbook.HUMAN_TRADERS or not position.get("opened_ts")
                or position.get("status") == "pending"
                or position.get("close_reason") == "cancelled"):
            continue
        if last is None or position["opened_ts"] > last["ts"]:
            last = {"trader": name, "ticker": position.get("ticker", "?"),
                    "ts": position["opened_ts"], "status": position.get("status")}
    return {"open": open_by_cat, "armed": armed, "last_action": last}


def _et_clock(ts: float) -> str:
    moment = pd.Timestamp(float(ts), unit="s", tz="UTC").tz_convert("America/New_York")
    return moment.strftime("%a %H:%M ET")


def trader_rows(category: str, *, live: bool, report_rows: list[dict]) -> list[dict]:
    """One row per registered trader of ``category`` — including bots with no trade yet."""
    by_name = {row["trader"]: row for row in report_rows}
    out = []
    for trader in traders.by_category(category, live=live):
        row = by_name.get(trader.name, {})
        closed = row.get("closed", 0)
        out.append({
            "trader": trader.name,
            "closed trades": closed,
            "win %": row.get("win_rate") if closed else None,
            "realized $": row.get("realized_usd", 0.0),
            "open": row.get("open", 0),
            "armed": row.get("armed", 0),
            "rule": trader.rule,          # last: it is the long column
        })
    return out


def _frame(rows: list[dict]) -> pd.DataFrame:
    """Rows as a table; a bot with no closed trade shows ‘–’ for win %, not ``None``."""
    frame = pd.DataFrame(rows)
    frame["win %"] = frame["win %"].map(
        lambda value: "–" if value is None or pd.isna(value) else f"{value:.0f}%")
    return frame


def _version_line(status: dict | None) -> str:
    text = f"Release **{release()}**"
    if status is not None and status["release"] != release():
        text += f" · ⚠ worker still runs {status['release']} (restart pending)"
    return text


def render_background(user_id: str) -> None:
    """Sidebar panel on every page: what the background worker is doing now."""

    @st.fragment(run_every=_REFRESH)
    def _panel() -> None:
        summary = worker_summary(workerstatus.current())
        st.markdown(f"{summary['icon']} **Radar worker** — {summary['headline']}")
        for line in summary["lines"]:
            st.caption(line)
        try:
            book = virtualbook.load(user_id=user_id)
        except Exception:
            st.caption("Book not readable right now.")
            return
        sums = book_summary(book)
        parts = [f"{sums['open'][traders.DAY]} day",
                 f"{sums['open'][traders.LONG]} long-term"]
        if sums["open"][traders.MANUAL]:
            parts.append(f"{sums['open'][traders.MANUAL]} manual")
        if sums["armed"]:
            parts.append(f"{sums['armed']} armed")
        st.caption("Open positions: " + " · ".join(parts))
        last = sums["last_action"]
        if last:
            st.caption(f"Last bot entry: **{last['trader']}** {last['ticker']} "
                       f"· {_et_clock(last['ts'])}")
        else:
            st.caption("No bot has entered a trade yet.")

    st.markdown("### 🛰 Background tasks")
    _panel()
    st.caption("Details and every bot: **ℹ️ Info** (top right).")


def render_info(user_id: str) -> None:
    """The Info page: version, the background worker, and every trader by kind."""
    st.header("ℹ️ Info")
    status = workerstatus.current()
    st.markdown(_version_line(status))
    st.caption(f"Build {os.environ.get('APP_VERSION', 'development')[:12]} · "
               "the release number goes up by 1 with every push.")

    st.subheader("Background worker")
    summary = worker_summary(status)
    st.markdown(f"{summary['icon']} **Radar worker** — {summary['headline']}")
    for line in summary["lines"]:
        st.caption(line)
    if status is not None:
        st.caption(f"Started {age_text((datetime.now(timezone.utc) - status['started_at']).total_seconds())}"
                   f" · worker release {status['release']}")

    book = virtualbook.load(user_id=user_id)
    report_rows = virtualbook.bot_report(book)["rows"]
    st.caption("Bots are listed even before their first trade. Results are the virtual book's "
               "closed trades; ‘open’ is positions held right now.")

    for category in (traders.DAY, traders.LONG, traders.MANUAL):
        live_rows = trader_rows(category, live=True, report_rows=report_rows)
        retired_rows = trader_rows(category, live=False, report_rows=report_rows)
        if not live_rows and not retired_rows:
            continue
        st.subheader(traders.CATEGORY_LABELS[category])
        if live_rows:
            st.dataframe(_frame(live_rows), hide_index=True, use_container_width=True)
        else:
            st.caption("No live trader in this group.")
        if retired_rows:
            with st.expander(f"Retired ({len(retired_rows)}) — no new entries"):
                st.dataframe(_frame(retired_rows), hide_index=True,
                             use_container_width=True)

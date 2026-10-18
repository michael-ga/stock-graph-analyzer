from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

import pandas as pd
import streamlit as st

from stockanalyzer.db.repositories.analysis_history import AnalysisHistoryRepository
from stockanalyzer.db.repositories.paper_trades import PaperTradeRepository
from stockanalyzer.db.repositories.trades import TradeRepository

_PAGE_SIZE = 25
_TIMEFRAMES = ("", "1D", "5D", "1M", "6M", "YTD", "1Y", "5Y")

_HISTORY_CSS = """
<style>
.history-hero {
  padding: 1.1rem 1.25rem;
  margin: .35rem 0 1rem;
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(113,112,255,.13), rgba(255,255,255,.025));
}
.history-hero h2 { margin: 0; letter-spacing: -.5px; font-weight: 590; }
.history-hero p { margin: .35rem 0 0; color: #8a8f98; }
.history-eyebrow {
  color: #828fff; font-size: .72rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: .09em; margin-bottom: .35rem;
}
div[data-testid="stMetric"] {
  background: rgba(255,255,255,.025);
  border: 1px solid rgba(255,255,255,.07);
  padding: .75rem .9rem;
  border-radius: 8px;
}
div[data-testid="stExpander"] {
  border-color: rgba(255,255,255,.08);
  border-radius: 8px;
  background: rgba(255,255,255,.018);
}
.history-section {
  color: #d0d6e0; font-size: .76rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: .07em;
  margin: 1rem 0 .35rem;
}
.history-positive { color: #10b981; }
.history-negative { color: #f87171; }
.history-neutral { color: #a8adb7; }
</style>
"""


def _label(value: Any) -> str:
    return str(value or "").replace("_", " ").strip().title()


def _format_number(value: Any, *, money: bool = False) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _label(value)
    if money:
        return f"${number:,.2f}"
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _format_percent(value: Any, *, signed: bool = False, ratio: bool = False) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if ratio:
        number *= 100
    return f"{number:+.0f}%" if signed else f"{number:.0f}%"


def _format_datetime(value: Any) -> str:
    if value is None or value == "":
        return "Unknown time"
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(float(value), timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%b %d, %Y · %H:%M")
    except (TypeError, ValueError, OSError):
        return str(value)


def _verdict_view(verdict: dict | None) -> dict[str, Any]:
    verdict = verdict or {}
    score_value = verdict.get("score")
    try:
        score_number = float(score_value)
    except (TypeError, ValueError):
        score_number = 0.0
    direction = str(verdict.get("direction") or "").lower()
    label = str(verdict.get("label") or "Hold / Neutral")
    if not direction:
        direction = "bullish" if score_number > 0.15 else "bearish" if score_number < -0.15 else "neutral"
    tone = "positive" if direction in {"bull", "bullish", "buy"} else (
        "negative" if direction in {"bear", "bearish", "sell"} else "neutral"
    )
    explanation = verdict.get("explanation") or []
    if isinstance(explanation, str):
        explanation = [explanation]
    return {
        "label": label,
        "direction": _label(direction),
        "score": _format_percent(score_number, signed=True, ratio=True),
        "confidence": _format_percent(verdict.get("confidence"), ratio=True),
        "tone": tone,
        "explanation": [str(item) for item in explanation if item],
    }


def _timeframe_table(timeframes: list[dict] | dict | None) -> list[dict]:
    if isinstance(timeframes, dict):
        items = [dict(value or {}, timeframe=key) for key, value in timeframes.items()]
    else:
        items = list(timeframes or [])
    return [
        {
            "Timeframe": item.get("timeframe", "—"),
            "Trend": _label(item.get("trend_direction") or item.get("trend_dir") or "neutral"),
            "Bias": _format_percent(item.get("bias_score"), signed=True, ratio=True),
            "Last close": _format_number(item.get("last_close"), money=True),
            "Bars": int(item.get("bar_count") or 0),
        }
        for item in items
    ]


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    text = str(value).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "—"


def _flatten_details(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if nested in (None, "", [], {}):
                continue
            name = f"{prefix} · {_label(key)}" if prefix else _label(key)
            if isinstance(nested, dict):
                rows.extend(_flatten_details(nested, name))
            elif isinstance(nested, list):
                if all(not isinstance(item, (dict, list)) for item in nested):
                    rows.append((name, "; ".join(_display_value(item) for item in nested)))
                else:
                    for index, item in enumerate(nested, 1):
                        rows.extend(_flatten_details(item, f"{name} {index}"))
            else:
                rows.append((name, _display_value(nested)))
    elif isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            rows.append((prefix or "Items", "; ".join(_display_value(item) for item in value)))
        else:
            base = prefix or "Item"
            for index, item in enumerate(value, 1):
                rows.extend(_flatten_details(item, f"{base} {index}"))
    elif prefix:
        rows.append((prefix, _display_value(value)))
    return rows


def _timeframe_names(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key) for key in value}
    if isinstance(value, list):
        return {
            str(item.get("timeframe")) if isinstance(item, dict) else str(item)
            for item in value
        }
    return set()


def _paginate(rows: list[dict], page: int, *, page_size: int = _PAGE_SIZE) -> tuple[list[dict], int, int]:
    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    normalized = max(1, min(int(page), total_pages))
    start = (normalized - 1) * page_size
    return rows[start : start + page_size], total_pages, normalized


def _virtual_trade_stats(rows: list[dict]) -> dict[str, int | float | None]:
    decided = [
        row for row in rows
        if row.get("status") == "closed" and row.get("close_reason") != "cancelled"
    ]
    wins = sum(float(row.get("pnl_pct") or 0) > 0 for row in decided)
    return {
        "active": sum(row.get("status") in {"open", "pending"} for row in rows),
        "decided": len(decided),
        "wins": wins,
        "win_rate": round(wins / len(decided) * 100) if decided else None,
        "total_pnl": round(sum(float(row.get("pnl_usd") or 0) for row in decided), 2),
    }


def _paper_trade_stats(rows: list[dict]) -> dict[str, int | None]:
    decided = [row for row in rows if row.get("status") in {"target_hit", "stop_hit", "expired"}]
    wins = sum(
        row.get("status") == "target_hit"
        or (row.get("status") == "expired" and float(row.get("result_pct") or 0) > 0)
        for row in decided
    )
    return {
        "decided": len(decided),
        "wins": wins,
        "win_rate": round(wins / len(decided) * 100) if decided else None,
        "not_triggered": sum(row.get("status") == "not_triggered" for row in rows),
    }


def _status_filter(rows: list[dict], status: str, result_type: str) -> list[dict]:
    if status == "Any":
        return rows
    normalized = status.lower().replace(" ", "_")
    if result_type == "Virtual trades" and normalized == "cancelled":
        return [row for row in rows if row.get("close_reason") == "cancelled"]
    return [row for row in rows if str(row.get("status", "")).lower() == normalized]


def _filtered_rows(
    rows: list[dict], ticker: str | None, provider: str | None,
    timeframe: str | None, strategy: str | None,
    start: datetime | None, end: datetime | None,
) -> list[dict]:
    filtered = []
    for row in rows:
        snapshot = row.get("snapshot") or {}
        if ticker and str(row.get("ticker", "")).upper() != ticker.strip().upper():
            continue
        row_provider = row.get("provider") or snapshot.get("provider")
        if provider and str(row_provider or "").lower() != provider.strip().lower():
            continue
        row_strategy = row.get("strategy") or snapshot.get("strategy")
        if strategy and str(row_strategy or "").lower() != strategy.strip().lower():
            continue
        row_timeframes = row.get("timeframes") or snapshot.get("timeframes") or {}
        if timeframe and timeframe not in _timeframe_names(row_timeframes):
            continue
        timestamp = row.get("opened_ts", row.get("ts"))
        if timestamp is not None:
            completed = datetime.fromtimestamp(float(timestamp), timezone.utc)
            if start and completed < start:
                continue
            if end and completed > end:
                continue
        elif start or end:
            continue
        filtered.append(row)
    return filtered


def _safe_csv(rows: list[dict]) -> str:
    sanitized = []
    for row in rows:
        sanitized.append({
            key: "'" + value
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
            else value
            for key, value in row.items()
            if key not in {"snapshot", "signals", "levels", "trendlines"}
        })
    return pd.DataFrame(sanitized).to_csv(index=False)


def _render_key_values(value: Any, *, empty_message: str = "No additional details recorded.") -> None:
    rows = _flatten_details(value)
    if not rows:
        st.caption(empty_message)
        return
    st.dataframe(
        pd.DataFrame(rows, columns=["Field", "Value"]),
        use_container_width=True,
        hide_index=True,
    )


def _render_analysis_detail(detail: dict) -> None:
    verdict = _verdict_view(detail.get("verdict"))
    st.markdown("<div class='history-section'>Analysis overview</div>", unsafe_allow_html=True)
    with st.container(border=True):
        heading, metadata = st.columns([2, 3])
        heading.subheader(f"{detail.get('ticker', '—')} · {verdict['label']}")
        metadata.caption(
            f"{_format_datetime(detail.get('completed_at'))} · "
            f"{_label(detail.get('provider'))} · {_label(detail.get('strategy') or 'Unspecified strategy')}"
        )
        one, two, three, four = st.columns(4)
        one.metric("Direction", verdict["direction"])
        two.metric("Technical score", verdict["score"])
        three.metric("Confidence", verdict["confidence"])
        quote = detail.get("quote") or {}
        four.metric("Last price", _format_number(
            quote.get("price") or quote.get("current") or quote.get("last"), money=True
        ))
        tags = [detail.get("use_case"), detail.get("swing_pace")]
        tags = [_label(tag) for tag in tags if tag]
        if tags:
            st.caption(" · ".join(tags))

    if verdict["explanation"]:
        st.markdown("<div class='history-section'>Why this verdict</div>", unsafe_allow_html=True)
        for line in verdict["explanation"]:
            st.markdown(f"- {line}")

    st.markdown("<div class='history-section'>Timeframe comparison</div>", unsafe_allow_html=True)
    timeframe_rows = _timeframe_table(detail.get("timeframes"))
    if timeframe_rows:
        st.dataframe(timeframe_rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No timeframe details were recorded.")

    for item in detail.get("timeframes") or []:
        name = item.get("timeframe", "Timeframe")
        with st.expander(f"{name} signals, levels and trend details"):
            tab1, tab2, tab3, tab4 = st.tabs(("Signals", "Levels", "Trend lines", "Trend change"))
            with tab1:
                _render_key_values(item.get("signals"), empty_message="No signals recorded.")
            with tab2:
                _render_key_values(item.get("levels"), empty_message="No levels recorded.")
            with tab3:
                _render_key_values(item.get("trendlines"), empty_message="No trend lines recorded.")
            with tab4:
                _render_key_values(item.get("trend_change"), empty_message="No trend change recorded.")

    left, right = st.columns(2)
    with left.expander("Quote and market data"):
        _render_key_values(detail.get("quote"))
    with right.expander("Sentiment"):
        _render_key_values(detail.get("sentiment"))
    notices = detail.get("notices") or []
    errors = detail.get("errors") or {}
    if notices:
        with st.expander(f"Notices ({len(notices)})"):
            _render_key_values({"notices": notices})
    if errors:
        with st.expander("Data warnings"):
            _render_key_values(errors)


def _render_analysis_history(user_id: str, ticker: str | None, start: datetime | None,
                             end: datetime | None) -> tuple[list[dict], str]:
    with st.expander("Analysis filters", expanded=True):
        one, two, three, four = st.columns(4)
        provider = one.text_input("Data provider", placeholder="Any provider").strip() or None
        timeframe = two.selectbox("Timeframe", _TIMEFRAMES, format_func=lambda value: value or "Any") or None
        strategy = three.selectbox("Strategy", ("", "investor", "swing"),
                                   format_func=lambda value: _label(value) if value else "Any") or None
        page = int(four.number_input("Page", min_value=1, value=1, step=1))

    repository = AnalysisHistoryRepository()
    fetched = repository.query(
        user_id, ticker=ticker, provider=provider, timeframe=timeframe, strategy=strategy,
        start=start, end=end, limit=_PAGE_SIZE + 1, offset=(page - 1) * _PAGE_SIZE,
    )
    has_more = len(fetched) > _PAGE_SIZE
    rows = fetched[:_PAGE_SIZE]
    if not rows:
        st.info("No analyses match these filters on this page. Try an earlier page or wider filters.")
        return [], "analysis-history.csv"

    verdicts = [_verdict_view(row.get("verdict")) for row in rows]
    positive = sum(view["tone"] == "positive" for view in verdicts)
    negative = sum(view["tone"] == "negative" for view in verdicts)
    one, two, three, four = st.columns(4)
    one.metric("Analyses on page", len(rows))
    two.metric("Bullish", positive)
    three.metric("Bearish", negative)
    four.metric("Neutral", len(rows) - positive - negative)
    st.caption(f"Page {page}" + (" · More records are available on the next page." if has_more else ""))

    labels = {
        row["id"]: f"{row['ticker']} · {_verdict_view(row.get('verdict'))['label']} · "
                   f"{_format_datetime(row.get('completed_at'))}"
        for row in rows
    }
    selected = st.selectbox("Choose an analysis", list(labels), format_func=labels.get)
    detail = repository.get_detail(user_id, selected)
    if detail:
        _render_analysis_detail(detail)
    return rows, "analysis-history.csv"


def _render_trade_card(row: dict) -> None:
    snapshot = row.get("snapshot") or {}
    status = _label(row.get("status"))
    title = f"{row.get('ticker', '—')} · {status} · {_label(row.get('trader'))}"
    with st.expander(title):
        one, two, three, four = st.columns(4)
        one.metric("Entry", _format_number(row.get("entry"), money=True))
        two.metric("Exit", _format_number(row.get("exit_price"), money=True))
        three.metric("P&L", _format_percent(row.get("pnl_pct"), signed=True))
        four.metric("P&L value", _format_number(row.get("pnl_usd"), money=True))
        prices = [{
            "Stop": _format_number(row.get("stop"), money=True),
            "Target": _format_number(row.get("target"), money=True),
            "Stake": _format_number(row.get("stake"), money=True),
            "Shares": _format_number(row.get("shares")),
            "Horizon": f"{row.get('horizon_days', '—')} days",
        }]
        st.dataframe(prices, use_container_width=True, hide_index=True)
        st.caption(
            f"Opened {_format_datetime(row.get('opened_ts'))}"
            + (f" · Closed {_format_datetime(row.get('closed_ts'))}" if row.get("closed_ts") else "")
            + (f" · {_label(row.get('close_reason'))}" if row.get("close_reason") else "")
        )
        if snapshot:
            st.markdown("**Decision snapshot**")
            _render_key_values(snapshot)


def _render_virtual_trades(rows: list[dict], *, summary_rows: list[dict] | None = None) -> None:
    summary_rows = rows if summary_rows is None else summary_rows
    stats = _virtual_trade_stats(summary_rows)
    one, two, three, four = st.columns(4)
    one.metric("Matching trades", len(summary_rows))
    two.metric("Active", stats["active"])
    three.metric("Win rate", f"{stats['win_rate']}%" if stats["win_rate"] is not None else "—")
    four.metric("Realized P&L", f"${stats['total_pnl']:+,.2f}")
    for row in rows:
        _render_trade_card(row)


def _render_paper_trades(rows: list[dict], *, summary_rows: list[dict] | None = None) -> None:
    summary_rows = rows if summary_rows is None else summary_rows
    stats = _paper_trade_stats(summary_rows)
    one, two, three, four = st.columns(4)
    one.metric("Matching records", len(summary_rows))
    two.metric("Decided", stats["decided"])
    three.metric("Win rate", f"{stats['win_rate']}%" if stats["win_rate"] is not None else "—")
    four.metric("Not triggered", stats["not_triggered"])
    for row in rows:
        title = (
            f"{row.get('ticker', '—')} · {_label(row.get('status'))} · "
            f"alert {row.get('level', '—')}% · {_format_datetime(row.get('ts'))}"
        )
        with st.expander(title):
            one, two, three, four = st.columns(4)
            one.metric("Alert level", f"{row.get('level', '—')}%")
            two.metric("Entry", _format_number(row.get("entry"), money=True))
            three.metric("Target", _format_number(row.get("target"), money=True))
            four.metric("Result", _format_percent(row.get("result_pct"), signed=True))
            _render_key_values({
                key: value for key, value in row.items()
                if key not in {"id", "source_id", "ticker", "status", "level", "ts", "date",
                               "entry", "target", "result_pct"}
            })


def render_history(user_id: str) -> None:
    st.markdown(_HISTORY_CSS, unsafe_allow_html=True)
    st.markdown(
        "<div class='history-hero'><div class='history-eyebrow'>Private activity archive</div>"
        "<h2>History dashboard</h2><p>Review decisions, compare timeframes, and filter trades "
        "without digging through database JSON.</p></div>",
        unsafe_allow_html=True,
    )

    result_type = st.radio(
        "History type", ("Completed analyses", "Virtual trades", "Paper trades"),
        horizontal=True, label_visibility="collapsed",
    )
    first, second, third = st.columns([1.2, 1, 1])
    ticker = first.text_input("Ticker", placeholder="All tickers").strip() or None
    start_date = second.date_input("From", value=None)
    end_date = third.date_input("To", value=None)
    start = datetime.combine(start_date, time.min, timezone.utc) if start_date else None
    end = datetime.combine(end_date, time.max, timezone.utc) if end_date else None

    try:
        if result_type == "Completed analyses":
            rows, filename = _render_analysis_history(user_id, ticker, start, end)
            export = AnalysisHistoryRepository.to_csv(rows) if rows else ""
        else:
            with st.expander("Trade filters", expanded=True):
                if result_type == "Virtual trades":
                    one, two, three = st.columns(3)
                    timeframe = one.selectbox(
                        "Timeframe", _TIMEFRAMES, format_func=lambda value: value or "Any"
                    ) or None
                    status_options = ("Any", "Open", "Pending", "Closed", "Cancelled")
                    status = two.selectbox("Status", status_options)
                    requested_page = int(three.number_input("Page", min_value=1, value=1, step=1,
                                                            key="virtual_history_page"))
                else:
                    one, two = st.columns(2)
                    timeframe = None
                    status_options = (
                        "Any", "Open", "Target hit", "Stop hit", "Expired", "Not triggered",
                    )
                    status = one.selectbox("Status", status_options)
                    requested_page = int(two.number_input("Page", min_value=1, value=1, step=1,
                                                          key="paper_history_page"))
            all_rows = (
                TradeRepository().list(user_id)
                if result_type == "Virtual trades"
                else PaperTradeRepository().list(user_id)
            )
            rows = _filtered_rows(all_rows, ticker, None, timeframe, None, start, end)
            rows = _status_filter(rows, status, result_type)
            page_rows, total_pages, current_page = _paginate(rows, requested_page)
            if not rows:
                st.info(f"No {result_type.lower()} match these filters.")
                export = ""
            else:
                st.caption(f"Page {current_page} of {total_pages} · {len(rows)} matching records")
                if result_type == "Virtual trades":
                    _render_virtual_trades(page_rows, summary_rows=rows)
                else:
                    _render_paper_trades(page_rows, summary_rows=rows)
                export = _safe_csv(rows)
            filename = result_type.lower().replace(" ", "-") + ".csv"
    except Exception:
        st.error("History could not be loaded. Try again.")
        return

    if export:
        st.download_button(
            "Download filtered CSV", export, file_name=filename, mime="text/csv",
            use_container_width=True,
        )

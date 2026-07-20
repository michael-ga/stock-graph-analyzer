from __future__ import annotations

from datetime import datetime, time, timezone

import pandas as pd
import streamlit as st

from stockanalyzer.db.repositories.analysis_history import AnalysisHistoryRepository
from stockanalyzer.db.repositories.paper_trades import PaperTradeRepository
from stockanalyzer.db.repositories.trades import TradeRepository

_PAGE_SIZE = 100


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
        if timeframe and timeframe not in row_timeframes:
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
        })
    return pd.DataFrame(sanitized).to_csv(index=False)


def render_history(user_id: str) -> None:
    st.header("🕘 Database History")
    result_type = st.selectbox(
        "Record type", ("Completed analyses", "Virtual trades", "Paper trades")
    )
    first, second, third = st.columns(3)
    ticker = first.text_input("Ticker").strip() or None
    provider = second.text_input("Provider").strip() or None
    timeframe = third.selectbox(
        "Timeframe", ("", "1D", "5D", "1M", "6M", "YTD", "1Y", "5Y")
    ) or None
    fourth, fifth, sixth = st.columns(3)
    strategy = fourth.text_input("Strategy").strip() or None
    start_date = fifth.date_input("From", value=None)
    end_date = sixth.date_input("To", value=None)
    page = int(st.number_input("Page", min_value=1, value=1, step=1))
    start = datetime.combine(start_date, time.min, timezone.utc) if start_date else None
    end = datetime.combine(end_date, time.max, timezone.utc) if end_date else None

    try:
        if result_type == "Completed analyses":
            repository = AnalysisHistoryRepository()
            rows = repository.query(
                user_id,
                ticker=ticker,
                provider=provider,
                timeframe=timeframe,
                strategy=strategy,
                start=start,
                end=end,
                limit=_PAGE_SIZE,
                offset=(page - 1) * _PAGE_SIZE,
            )
            if not rows:
                st.info("No matching completed analyses.")
                return
            summaries = [
                {key: value for key, value in row.items() if key not in ("verdict", "timeframes")}
                for row in rows
            ]
            st.dataframe(summaries, use_container_width=True)
            selected = st.selectbox("Analysis detail", [row["id"] for row in rows])
            detail = repository.get_detail(user_id, selected)
            if detail is not None:
                st.json(detail)
            export = repository.to_csv(rows)
            filename = "analysis-history.csv"
        else:
            all_rows = (
                TradeRepository().list(user_id)
                if result_type == "Virtual trades"
                else PaperTradeRepository().list(user_id)
            )
            filtered = _filtered_rows(
                all_rows, ticker, provider, timeframe, strategy, start, end)
            rows = filtered[(page - 1) * _PAGE_SIZE : page * _PAGE_SIZE]
            if not rows:
                st.info(f"No matching {result_type.lower()}.")
                return
            # Streamlit and pandas escape provider/user strings; no raw HTML is used.
            st.dataframe(rows, use_container_width=True)
            selected = st.selectbox(
                "Record detail", list(range(len(rows))), format_func=lambda index: str(rows[index].get("id", index))
            )
            st.json(rows[selected])
            export = _safe_csv(filtered)
            filename = result_type.lower().replace(" ", "-") + ".csv"
    except Exception:
        st.error("History could not be loaded. Try again.")
        return

    st.download_button(
        "Download filtered CSV",
        export,
        file_name=filename,
        mime="text/csv",
        use_container_width=True,
    )

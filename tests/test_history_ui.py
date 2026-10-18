from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import time as time_module

from streamlit.testing.v1 import AppTest

from stockanalyzer.auth import AuthService
from stockanalyzer.db.models import Base
from stockanalyzer.db.repositories.analysis_history import AnalysisHistoryRepository
from stockanalyzer.db.session import configure_engine
from stockanalyzer.ui import history


def test_history_page_never_renders_raw_json():
    source = Path("stockanalyzer/ui/history.py").read_text()
    assert "st.json(" not in source


def test_verdict_view_presents_human_readable_direction_and_percentages():
    view = history._verdict_view({
        "label": "Buy (lean)",
        "direction": "bullish",
        "score": 0.62,
        "confidence": 0.78,
        "explanation": ["Momentum and structure agree."],
    })

    assert view == {
        "label": "Buy (lean)",
        "direction": "Bullish",
        "score": "+62%",
        "confidence": "78%",
        "tone": "positive",
        "explanation": ["Momentum and structure agree."],
    }


def test_timeframe_table_turns_nested_analysis_into_readable_rows():
    rows = history._timeframe_table([
        {
            "timeframe": "1D",
            "bias_score": 0.42,
            "trend_direction": "bullish",
            "last_close": 214.35,
            "bar_count": 120,
            "signals": [{"name": "RSI", "direction": "bullish"}],
        }
    ])

    assert rows == [{
        "Timeframe": "1D",
        "Trend": "Bullish",
        "Bias": "+42%",
        "Last close": "$214.35",
        "Bars": 120,
    }]


def test_nested_details_are_flattened_into_labels_instead_of_json():
    rows = history._flatten_details({
        "setup": "breakout_wait",
        "risk": {"stop": 95.5, "target": 112.0},
        "checks": ["volume confirmed", "trend aligned"],
    })

    assert ("Setup", "Breakout wait") in rows
    assert ("Risk · Stop", "95.50") in rows
    assert ("Risk · Target", "112.00") in rows
    assert ("Checks", "Volume confirmed; Trend aligned") in rows


def test_top_level_signal_lists_are_rendered_as_readable_fields():
    rows = history._flatten_details([
        {"name": "RSI momentum", "direction": "bullish", "strength": 0.8}
    ])

    assert ("Item 1 · Name", "RSI momentum") in rows
    assert ("Item 1 · Direction", "Bullish") in rows
    assert ("Item 1 · Strength", "0.80") in rows


def test_trade_timeframe_filter_matches_list_shaped_snapshots():
    now = datetime.now(timezone.utc).timestamp()
    rows = [{
        "ticker": "AAPL",
        "opened_ts": now,
        "snapshot": {"timeframes": [{"timeframe": "1D"}, {"timeframe": "1M"}]},
    }]

    assert history._filtered_rows(rows, None, None, "1D", None, None, None) == rows


def test_virtual_trade_stats_exclude_cancelled_positions():
    stats = history._virtual_trade_stats([
        {"status": "closed", "close_reason": "target_hit", "pnl_pct": 10, "pnl_usd": 100},
        {"status": "closed", "close_reason": "stop_hit", "pnl_pct": -5, "pnl_usd": -50},
        {"status": "closed", "close_reason": "cancelled", "pnl_pct": 0, "pnl_usd": 0},
        {"status": "open", "pnl_pct": 0, "pnl_usd": 0},
    ])

    assert stats == {"active": 1, "decided": 2, "wins": 1, "win_rate": 50, "total_pnl": 50.0}


def test_paper_trade_stats_exclude_not_triggered_from_win_rate():
    stats = history._paper_trade_stats([
        {"status": "target_hit", "result_pct": 8},
        {"status": "stop_hit", "result_pct": -4},
        {"status": "expired", "result_pct": 2},
        {"status": "not_triggered", "result_pct": 0},
        {"status": "open", "result_pct": 0},
    ])

    assert stats == {"decided": 3, "wins": 2, "win_rate": 67, "not_triggered": 1}


def test_record_status_filters_match_real_paper_and_virtual_states():
    paper = [
        {"status": "target_hit"}, {"status": "not_triggered"}, {"status": "open"},
    ]
    virtual = [
        {"status": "closed", "close_reason": "cancelled"},
        {"status": "closed", "close_reason": "target_hit"},
    ]

    assert history._status_filter(paper, "Target hit", "Paper trades") == [paper[0]]
    assert history._status_filter(paper, "Not triggered", "Paper trades") == [paper[1]]
    assert history._status_filter(virtual, "Cancelled", "Virtual trades") == [virtual[0]]


def test_pagination_bounds_rendered_history_records():
    rows = [{"id": index} for index in range(60)]

    page_rows, total_pages, page = history._paginate(rows, 3, page_size=25)

    assert [row["id"] for row in page_rows] == list(range(50, 60))
    assert total_pages == 3
    assert page == 3


def test_naive_database_timestamp_is_interpreted_as_utc(monkeypatch):
    if not hasattr(time_module, "tzset"):
        return
    previous = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/New_York")
    time_module.tzset()
    try:
        assert history._format_datetime("2026-07-20T12:00:00") == "Jul 20, 2026 · 08:00"
    finally:
        if previous is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", previous)
        time_module.tzset()


def test_history_dashboard_renders_analysis_as_metrics_and_tables(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'history-dashboard.db'}"
    engine = configure_engine(database_url)
    Base.metadata.create_all(engine)
    user = AuthService().create_user("history-viewer", "correct horse battery staple")
    now = datetime.now(timezone.utc)
    AnalysisHistoryRepository().save(
        user.id,
        ticker="AAPL",
        provider="yfinance",
        strategy="swing",
        verdict={
            "label": "Buy (lean)", "direction": "bullish", "score": 0.62,
            "confidence": 0.78, "explanation": ["Momentum and structure agree."],
        },
        timeframes=[{
            "timeframe": "1D", "bias_score": 0.42, "trend_direction": "bullish",
            "last_close": 214.35, "bar_count": 120,
        }],
        started_at=now,
        completed_at=now,
        idempotency_key="history-dashboard",
    )

    app = AppTest.from_string(
        f"from stockanalyzer.ui.history import render_history\nrender_history('{user.id}')"
    ).run(timeout=30)

    assert not app.exception
    assert any(metric.label == "Technical score" and metric.value == "+62%" for metric in app.metric)
    assert any("AAPL" in subheader.value for subheader in app.subheader)
    engine.dispose()

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stockanalyzer.charting import daychart as dc

TODAY = date(2026, 10, 21)  # a Wednesday


@pytest.mark.parametrize("spec,expected", [
    ("today", [date(2026, 10, 21)]),
    ("yesterday", [date(2026, 10, 20)]),
    ("last week", dc._weekdays_between(date(2026, 10, 12), date(2026, 10, 16))),
    ("previous week", dc._weekdays_between(date(2026, 10, 12), date(2026, 10, 16))),
    ("this week", [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]),
    ("last 3 days", [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]),
    ("last monday", [date(2026, 10, 19)]),
    ("Last Three Fridays", [date(2026, 10, 2), date(2026, 10, 9), date(2026, 10, 16)]),
    ("2026-10-13,2026-10-15", [date(2026, 10, 13), date(2026, 10, 15)]),
    ("2026-10-09..2026-10-13", [date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13)]),
])
def test_resolve_days(spec, expected):
    assert dc.resolve_days(spec, TODAY) == expected


def test_last_n_mondays_counts_back_and_includes_today_when_monday():
    days = dc.resolve_days("last 10 mondays", date(2026, 10, 19))
    assert len(days) == 10 and all(d.weekday() == 0 for d in days)
    assert days[-1] == date(2026, 10, 19)


def test_last_month_and_weeks():
    days = dc.resolve_days("last month", TODAY)
    assert days[0] == date(2026, 9, 1) and days[-1] == date(2026, 9, 30)
    assert dc.resolve_days("last 2 weeks", TODAY)[0] == date(2026, 10, 5)


def test_unknown_phrase_raises():
    with pytest.raises(ValueError):
        dc.resolve_days("whenever", TODAY)


def test_pick_interval():
    assert dc.pick_interval([date(2026, 10, 19)], False, TODAY) == "5m"
    assert dc.pick_interval([date(2026, 8, 10)], False, TODAY) == "1h"
    assert dc.pick_interval([date(2023, 1, 2)], False, TODAY) == "1d"
    assert dc.pick_interval([date(2026, 8, 3), date(2026, 10, 16)], True, TODAY) == "1d"


def _bars(days):
    idx = []
    for d in days:
        idx += list(pd.date_range(f"{d} 09:30", f"{d} 15:55", freq="5min"))
    n = len(idx)
    close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 0.2, n))
    return pd.DataFrame({"open": close - 0.05, "high": close + 0.3, "low": close - 0.3,
                         "close": close, "volume": np.full(n, 1000.0)},
                        index=pd.DatetimeIndex(idx))


def test_stats_and_html_without_network():
    df = _bars([date(2026, 10, 12), date(2026, 10, 13)])
    stats = dc.day_stats(df)
    assert [s.day for s in stats] == [date(2026, 10, 12), date(2026, 10, 13)]
    assert stats[0].volume == 78 * 1000
    text = dc.stats_text("TEST", "x", stats)
    assert "2 trading days" in text and "period:" in text

    html = dc.build_html(df, "TEST", "last 2 days", "5m", summary=False)
    assert html.count("plotly-graph-div") >= 2  # grid + overlay
    assert "cdn.plot.ly" in html
    html = dc.build_html(df, "TEST", "last 2 days", "5m", summary=True)
    assert "plotly-graph-div" in html

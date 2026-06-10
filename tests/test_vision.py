"""Phase 3 image-fallback tests.

Round-trip: render a known candlestick chart with mplfinance, then read it back
with the OpenCV reader and confirm we recover a sensible candle count and that the
analysis engine runs on the result. Also unit-tests classify + the OCR parser.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

cv2 = pytest.importorskip("cv2")
mpf = pytest.importorskip("mplfinance")

from stockanalyzer.analysis.engine import analyze_timeframe
from stockanalyzer.vision.classify import ImageKind, classify_image
from stockanalyzer.vision.chart_reader import read_candles
from stockanalyzer.vision.info_reader import parse_info


def _make_ohlcv(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    # Up then down so there is a clear structure to recover.
    base = np.concatenate([np.linspace(100, 140, n // 2), np.linspace(140, 110, n - n // 2)])
    rng = np.random.default_rng(0)
    opens = base + rng.normal(0, 0.5, n)
    closes = base + rng.normal(0, 0.5, n)
    highs = np.maximum(opens, closes) + rng.uniform(0.5, 2.0, n)
    lows = np.minimum(opens, closes) - rng.uniform(0.5, 2.0, n)
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes,
                         "Volume": rng.uniform(1e6, 2e6, n)}, index=idx)


def _render(df: pd.DataFrame, path: str) -> None:
    mc = mpf.make_marketcolors(up="#26a69a", down="#ef5350", edge="inherit", wick="inherit")
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc)
    mpf.plot(df, type="candle", style=style, volume=False,
             savefig=dict(fname=path, dpi=100, pad_inches=0.1), figsize=(10, 5),
             axisoff=True)


def test_chart_roundtrip(tmp_path):
    df = _make_ohlcv(30)
    img = tmp_path / "chart.png"
    _render(df, str(img))

    cls = classify_image(str(img))
    assert cls.kind == ImageKind.CHART

    result = read_candles(str(img))
    # We won't recover every candle perfectly, but should get most of them.
    assert result.n_candles >= 18
    # The analysis engine must run on the recovered (relative-price) frame.
    rep = analyze_timeframe(result.df)
    assert -1.0 <= rep.bias_score <= 1.0
    assert len(result.df) == result.n_candles


def test_info_panel_classified_as_info(tmp_path):
    # Plain dark image with white text — no red/green candles.
    img = np.full((300, 500, 3), 20, np.uint8)
    for i, txt in enumerate(["Previous Close 428.05", "PE Ratio (TTM) 24.82", "EPS 16.79"]):
        cv2.putText(img, txt, (20, 60 + i * 60), cv2.FONT_HERSHEY_SIMPLEX, 1,
                    (240, 240, 240), 2)
    path = tmp_path / "info.png"
    cv2.imwrite(str(path), img)
    cls = classify_image(str(path))
    assert cls.kind == ImageKind.INFO


def test_parse_info_extracts_fields():
    lines = [
        "Previous Close 428.05",
        "Open 428.34",
        "PE Ratio (TTM) 24.82",
        "EPS (TTM) 16.79",
        "Beta (5Y Monthly) 1.10",
        "Market Cap (intraday) 3.095T",
        "1y Target Est 560.95",
        "Microsoft is advancing its quantum computing efforts while facing scrutiny",
    ]
    fields, headlines = parse_info(lines)
    assert round(fields["pe"], 2) == 24.82
    assert round(fields["eps"], 2) == 16.79
    assert round(fields["beta"], 2) == 1.10
    assert fields["market_cap"] == pytest.approx(3.095e12, rel=1e-3)
    assert round(fields["target_1y"], 2) == 560.95
    assert any("quantum" in h for h in headlines)

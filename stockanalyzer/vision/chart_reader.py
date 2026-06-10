"""Extract approximate OHLCV from a candlestick chart image (OpenCV).

Pipeline: HSV color-segment red/green candles → connect wick-to-body with
morphology → contour each candle → per-candle body vs. wick by row-width →
map pixel-Y to price. Output feeds the SAME analysis engine as the API path.

Prices are RELATIVE unless a calibration is supplied: directional signals (trend,
S/R structure, candlestick & chart patterns, RSI/MACD crosses) are preserved under
a positive linear pixel→price mapping, which is all the engine needs. Absolute
price labels require axis OCR (see info_reader) — this is explicitly approximate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
import pandas as pd

from ..data.schema import validate_ohlcv
from .colors import candle_mask, green_mask, red_mask


@dataclass
class ChartReadResult:
    df: pd.DataFrame                       # OHLCV (relative price unless calibrated)
    n_candles: int
    confidence: float
    calibrated: bool = False
    notes: list[str] = field(default_factory=list)


def _load_bgr(image) -> np.ndarray:
    if isinstance(image, np.ndarray):
        return image
    bgr = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"could not read image: {image}")
    return bgr


def _px_to_price(px: float, lo_px: int, hi_px: int,
                 calib: tuple[int, float, int, float] | None) -> float:
    """Image Y grows downward, so smaller px = higher price."""
    if calib is not None:
        pa, va, pb, vb = calib
        if pb == pa:
            return va
        return va + (px - pa) * (vb - va) / (pb - pa)
    if hi_px == lo_px:
        return 100.0
    # Map top of chart (hi_px, smallest y) → 200, bottom (lo_px) → 100.
    frac = (lo_px - px) / (lo_px - hi_px)
    return 100.0 + 100.0 * frac


def read_candles(
    image,
    calibration: tuple[int, float, int, float] | None = None,
    min_candles: int = 5,
) -> ChartReadResult:
    """Read candles from a chart image.

    calibration: optional (pixel_y_a, price_a, pixel_y_b, price_b) to recover
    absolute prices (e.g. from two axis tick labels read via OCR).
    """
    bgr = _load_bgr(image)
    h, w = bgr.shape[:2]
    notes: list[str] = []

    gmask = green_mask(bgr)
    rmask = red_mask(bgr)
    cmask = candle_mask(bgr)

    # Connect each candle's wick to its body (vertical close), drop specks (open).
    cmask = cv2.morphologyEx(cmask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    cmask = cv2.morphologyEx(cmask, cv2.MORPH_CLOSE, np.ones((9, 1), np.uint8))

    contours, _ = cv2.findContours(cmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    raw = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if ch < max(3, 0.01 * h) or cw < 1:
            continue
        raw.append((x, y, cw, ch))

    if not raw:
        raise ValueError("no candle-colored shapes found — is this a candlestick chart?")

    # Keep shapes whose width is candle-like (filters dashed price lines, blobs).
    widths = np.array([r[2] for r in raw])
    med_w = float(np.median(widths))
    candles_px = [r for r in raw if 0.3 * med_w <= r[2] <= 3.0 * med_w]
    candles_px.sort(key=lambda r: r[0])  # left → right

    if len(candles_px) < min_candles:
        notes.append(f"only {len(candles_px)} candle-like shapes detected.")

    lo_px = max(y + ch for (x, y, cw, ch) in candles_px)   # bottom-most pixel
    hi_px = min(y for (x, y, cw, ch) in candles_px)        # top-most pixel

    rows = []
    for (x, y, cw, ch) in candles_px:
        box_g = int(gmask[y:y + ch, x:x + cw].sum())
        box_r = int(rmask[y:y + ch, x:x + cw].sum())
        is_up = box_g >= box_r

        sub = cmask[y:y + ch, x:x + cw] > 0
        row_width = sub.sum(axis=1)
        if row_width.max() == 0:
            continue
        body_rows = np.where(row_width >= 0.6 * row_width.max())[0]
        body_top_px = y + int(body_rows.min())
        body_bot_px = y + int(body_rows.max())
        high_px, low_px_c = y, y + ch - 1

        high = _px_to_price(high_px, lo_px, hi_px, calibration)
        low = _px_to_price(low_px_c, lo_px, hi_px, calibration)
        body_hi = _px_to_price(body_top_px, lo_px, hi_px, calibration)
        body_lo = _px_to_price(body_bot_px, lo_px, hi_px, calibration)

        if is_up:
            open_, close = body_lo, body_hi      # green: close above open
        else:
            open_, close = body_hi, body_lo      # red: close below open
        rows.append((open_, high, low, close))

    if not rows:
        raise ValueError("candle shapes found but none could be measured.")

    idx = pd.date_range("2000-01-01", periods=len(rows), freq="D")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 0.0  # volume panel not read; engine skips volume signals at 0
    df = validate_ohlcv(df)

    # Confidence: more candles + uniform widths ⇒ higher.
    uniformity = 1.0 - min(1.0, float(np.std(widths)) / (med_w + 1e-9))
    confidence = round(min(1.0, 0.3 + 0.4 * uniformity + 0.01 * len(rows)), 2)
    notes.append("Prices are RELATIVE (no calibration)." if calibration is None
                 else "Prices calibrated from supplied axis ticks.")

    return ChartReadResult(df, len(rows), confidence, calibration is not None, notes)

"""Decide whether a screenshot is a price CHART or an INFO/text panel.

Heuristic: a candlestick chart has many narrow, tall red/green colored columns
spread horizontally; an info panel is mostly text (little saturated red/green,
spread differently). Cheap, dependency-light, and good enough to route the image.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from .colors import candle_mask, green_mask, red_mask


class ImageKind(str, Enum):
    CHART = "chart"
    INFO = "info"


@dataclass
class ClassifyResult:
    kind: ImageKind
    confidence: float
    candle_pixel_ratio: float
    colored_columns: int
    reason: str


def _load_bgr(image) -> np.ndarray:
    if isinstance(image, np.ndarray):
        return image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    bgr = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"could not read image: {image}")
    return bgr


def classify_image(image) -> ClassifyResult:
    bgr = _load_bgr(image)
    h, w = bgr.shape[:2]
    mask = candle_mask(bgr)
    ratio = float(mask.sum() / 255) / (h * w)

    # Columns containing any candle color (low threshold — candles can be thin).
    col_count = mask.sum(axis=0) / 255
    active = col_count > max(3, 0.008 * h)
    colored_columns = int(active.sum())

    # Horizontal extent: a chart's candles span most of the image width; a small
    # colored badge/icon on an info panel does not.
    xs = np.where(active)[0]
    extent = float(xs.max() - xs.min()) / w if len(xs) else 0.0

    green = float(green_mask(bgr).sum() / 255) / (h * w)
    red = float(red_mask(bgr).sum() / 255) / (h * w)
    both_colors = green > 0.001 and red > 0.001

    # Chart: both colors present, spread across the width, not a solid color fill.
    is_chart = both_colors and extent > 0.4 and colored_columns >= 8 and ratio < 0.6
    if is_chart:
        conf = min(1.0, 0.4 + 0.5 * extent)
        reason = (f"red+green candles spanning {extent:.0%} of width "
                  f"({colored_columns} active columns, area {ratio:.1%}).")
        return ClassifyResult(ImageKind.CHART, round(conf, 2), round(ratio, 4),
                              colored_columns, reason)

    conf = min(1.0, 0.5 + 0.5 * (1 - min(extent, 1.0)))
    reason = (f"candle color not spread like a chart (extent {extent:.0%}, "
              f"both_colors={both_colors}); treating as info/text panel.")
    return ClassifyResult(ImageKind.INFO, round(conf, 2), round(ratio, 4),
                          colored_columns, reason)

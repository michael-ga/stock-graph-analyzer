"""Shared HSV masks for the red/green of candlesticks.

Works for the common dark-theme palette (teal-green up, red down) seen on Yahoo/
TradingView, and also ordinary bright green/red. Returned masks are uint8 0/255.
"""
from __future__ import annotations

import cv2
import numpy as np


def green_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # Green/teal spans roughly hue 70..170 in OpenCV's 0..179 scale.
    lower = np.array([60, 40, 40])
    upper = np.array([95, 255, 255])
    return cv2.inRange(hsv, lower, upper)


def red_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # Red wraps around 0/179, so two bands.
    m1 = cv2.inRange(hsv, np.array([0, 60, 40]), np.array([12, 255, 255]))
    m2 = cv2.inRange(hsv, np.array([165, 60, 40]), np.array([179, 255, 255]))
    return cv2.bitwise_or(m1, m2)


def candle_mask(bgr: np.ndarray) -> np.ndarray:
    return cv2.bitwise_or(green_mask(bgr), red_mask(bgr))

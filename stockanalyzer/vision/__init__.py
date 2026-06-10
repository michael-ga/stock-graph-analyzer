"""Image fallback (Phase 3): read OHLCV / info from a screenshot when no API/ticker
is available. Results are APPROXIMATE — the API path is always preferred.
"""
from .classify import ImageKind, classify_image
from .chart_reader import ChartReadResult, read_candles
from .info_reader import InfoReadResult, parse_info, read_info

__all__ = [
    "ImageKind", "classify_image",
    "ChartReadResult", "read_candles",
    "InfoReadResult", "parse_info", "read_info",
]

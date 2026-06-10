"""Core technical-analysis engine. Pure functions over a normalized OHLCV frame.

Public entry point: ``analyze_timeframe`` returns a TimeframeReport bundling every
detector's output plus an aggregated trend-change score.
"""
from .signals import Direction, Signal
from .engine import TimeframeReport, analyze_timeframe

__all__ = ["Direction", "Signal", "TimeframeReport", "analyze_timeframe"]

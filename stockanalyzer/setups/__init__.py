"""Intraday setups — one file per setup, all in this package.

- ``orb.py``        Gap-and-Go opening-range breakout (live bots ``bot-gap-*``).
- ``reversal.py``   reversal-at-support family (live bots ``rev-*``).
- ``silver_bullet.py``, ``double_bottom.py``, ``base_breakout.py``  backtest-only
  setups built on the shared contract in ``base.py``
  (``signal(SetupContext, cfg) -> SetupDecision``).
  ``harness.py`` steps them bar by bar over real history.

``orb`` and ``reversal`` keep their own signatures because the live radar, the
replay and their tests call them directly. New setups follow ``base.py`` and are
listed in ``SETUPS`` so the harness finds them; the
``.claude/skills/setup-lab`` skill walks an idea from text to a module here.
Wiring a setup into the live bots is an algorithm change — its own [ALGO] PR.
"""
from . import base_breakout, double_bottom, silver_bullet

SETUPS = {m.NAME: m for m in (silver_bullet, double_bottom, base_breakout)}

__all__ = ["SETUPS"]

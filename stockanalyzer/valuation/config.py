"""Every valuation tunable in one place (plan rule R6).

These are heuristics, not truths — they exist to be swept by the Phase 5
backtest. Engine modules read them through the ``cfg`` argument (this module by
default), never as bare numbers, so a sweep can pass a patched copy.
"""
from __future__ import annotations

import os

VALUATION_ENABLED = True

# SEC access — SEC asks for an app name + contact in every User-Agent.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "StockAnalyzer personal-research admin@stockanalyzer.local")
SEC_MAX_RPS = 8
CACHE_TTL_HOURS = 24
RISK_FREE_DEFAULT = 0.042          # used when FRED is unreachable

# discount rate (§6.1)
ERP = 0.05
SMALL_CAP_USD = 2e9
SMALL_CAP_PREMIUM = 0.01
NEG_FCF_PREMIUM = 0.015
HIGH_LEVERAGE_PREMIUM = 0.005
R_MIN, R_MAX = 0.08, 0.15
G_TERMINAL_MAX = 0.025
MIN_R_MINUS_G = 0.03

# DCF (§6.2)
HORIZON_YEARS = 10
MARGIN_RAMP_YEARS = 7
SBC_AS_EXPENSE = True
TV_SHARE_WARN = 0.85
TARGET_FCF_MARGIN = {"default": 0.15, "software": 0.25, "semis": 0.20, "retail": 0.05}
SECTOR_BY_SIC = {"7372": "software", "7371": "software", "7374": "software",
                 "3674": "semis", "5311": "retail", "5331": "retail", "5411": "retail"}

# growth (§6.3)
BASE_RATE_CAP = [(50e9, 0.10), (10e9, 0.15), (1e9, 0.25), (0, 0.40)]
GROWTH_FLOOR = -0.10
PLAUSIBLE_WEIGHTS = {"cagr_3y": 0.5, "yoy": 0.3, "guidance": 0.2}

# reverse DCF / expectations (§6.4)
REV_DCF_BOUNDS = (-0.20, 0.80)
REV_DCF_TOL = 0.005                # fraction of price
REV_DCF_MAX_ITER = 60
GAP_PESSIMISTIC, GAP_STRETCHED, GAP_PFP = -0.05, 0.05, 0.10

# archetypes (§5)
MIN_QUARTERS = 4
FULL_CONF_QUARTERS = 12
PRE_REVENUE_USD = 50e6
GROWTH_THRESHOLD = 0.15
FINANCIAL_SIC_RANGE = (6000, 6411)
REIT_SIC = {"6798"}
CYCLICAL_SIC = {"1311", "1381", "2911", "3312", "3674", "3711",
                "1000", "1040", "2800", "2821", "3714", "4911"}
CYCLICAL_MARGIN_STD = 0.08

# health (§6.6) — thresholds, then the points each finding removes (max 20)
LEVERAGE_WARN, LEVERAGE_SEVERE = 3.0, 5.0
COVERAGE_WARN, COVERAGE_SEVERE = 2.0, 1.0
RUNWAY_WARN_M, RUNWAY_SEVERE_M = 12, 6
DILUTION_WARN, DILUTION_SEVERE = 0.05, 0.10
HEALTH_PENALTY = {"warn": 4.0, "severe": 8.0, "piotroski_weak": 4.0}
PIOTROSKI_STRONG, PIOTROSKI_WEAK = 7, 3
SHORT_INTEREST_NOTE = 0.20

# multiples (§6.5)
MULTIPLE_HISTORY_YEARS = 5
MULTIPLE_MIN_POINTS = 12
FILING_LAG_DAYS = 45               # a quarter's numbers are public ~45d after period end

# scenarios (§6.7)
SCENARIO_PROBS = {"bear": 0.25, "base": 0.50, "bull": 0.25}
METHOD_WEIGHTS = {
    "MATURE": {"dcf": 0.6, "multiples": 0.4},
    "GROWTH_PROFITABLE": {"dcf": 0.6, "multiples": 0.4},
    "GROWTH_UNPROFITABLE": {"dcf": 0.5, "multiples": 0.5},
    "CYCLICAL": {"dcf": 0.5, "multiples": 0.5},
    "FINANCIAL": {"pb_roe": 0.7, "multiples": 0.3},
    "FALLBACK": {"multiples": 1.0},
}

# score (§6.8) — component maxima
SCORE_MAX = {"mos": 30, "gap": 25, "health": 20, "quality": 15, "multiple": 10}

# labels & confidence
MOS_UNDERVALUED, MOS_OVERVALUED = 0.20, -0.20
LOW_CONF = 0.5
FALLBACK_CONF_CAP = 0.45
CONF_HIGH = 0.75

# fusion (§7)
W_MAX = 0.25
# Gates are switched separately from the blend: W_MAX = 0 with every gate off is
# the byte-identical "information only" mode (R5).
GATES = {"VAL_HEALTH_VETO": True, "VAL_PFP_EARNINGS": True, "VAL_GAP_RISK": True}
# Default: the blended score is DISPLAYED; bots and the book keep deciding on the
# TA score until the Phase 5 backtest earns the blend a vote (LEARNINGS guardrail).
BLEND_DRIVES_PLAN_SCORE = False
EARNINGS_WINDOW_DAYS = 10
PFP_EARNINGS_MODE = "block"        # or "require_rr"
PFP_MIN_RR = 2.5
SIZE_MULTIPLIER = {"UNDERVALUED": 1.25, "FAIR": 1.0, "OVERVALUED": 0.5,
                   "PRICED_FOR_PERFECTION": 0.5, "CANT_VALUE": 1.0}
MAX_TRADE_COUNT_DROP = 0.25        # backtest acceptance

# events (§7.5)
EARNINGS_MOVE_EVENTS = 8

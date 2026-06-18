"""Emerging-mode demand score — for tickers with too little history to trust the
mature setup scorer (fresh IPOs, recent spin-offs, new listings).

The mature swing score leans on multi-frame moving averages, MACD, and a level
map built from months of daily bars. A name with ~3 trading days has none of
that — sma50/sma200/MACD are all `n/a`, and the level mapper has almost no
structure. Scoring it on that machinery either silently collapses to a near-zero
score (every MA row `n/a`) or, worse, rewards the one thing it *can* see — a big
green percentage — and turns the tool into a top-buyer.

This module scores the **only** thing that's actually knowable from a handful of
bars: *is the buying genuine, or is it a blowoff being distributed to latecomers?*

The lesson is SPCX: it printed +20% on the day, but it opened at $150, spiked
past 30%, and closed at $160.95 — well off its highs. A big green headline that
closes near the low of its range is distribution, not demand. So "eager buying"
is **never** measured by the percentage move. It's measured by *where price
closes in its range*, *whether volume confirms the up-days*, *whether it's making
higher lows and holding its anchor*, and *whether it's stretched into exhaustion*.

Components (weights). VWAP is dropped and the rest renormalized when no session
VWAP is supplied, so the score never silently assumes a neutral VWAP.

  - Accumulation trend   (25%) — higher lows + holding above the anchor.
  - Close strength       (20%) — where in the day's range price closes. Near the
                                 high = buyers in control; near the low = sold.
  - Volume confirmation  (20%) — share of volume on up-days; fading volume on a
                                 continued rise is penalized.
  - VWAP position        (15%) — above VWAP = buyers in control intraday.
  - Extension guard      (20%) — the anti-"buy the top" term: a moderate
                                 extension scores high, a parabolic move toward
                                 EXT_BLOWOFF above the anchor scores ~0.

Two hard guardrails sit on top of the weighted blend:

  1. The score is **capped** at `EMERGING_SCORE_CAP` (< the mature ceiling): with
     less information it must not be able to look as certain as a mature setup.
  2. **Confidence** scales with how many bars exist, so a one-day listing can't
     earn a "healthy demand" call no matter how the single bar looks — the label
     is gated by confidence, not just the raw score.

Eager buying raises the score; being *extended* raises the `extended` flag, which
the swing layer uses to refuse a GO. The two jobs — "is there demand" and "is it
safe to chase here" — are deliberately kept separate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Routing boundary: at or above this many daily bars, the mature setup scorer has
# enough history (sma50, a real level map) and should be used instead.
EMERGING_MAX_BARS = 60

# Guardrail 1 — a low-history name can never score as confidently as a mature
# setup. Tune against a backtest of how first-weeks IPOs actually resolve.
EMERGING_SCORE_CAP = 75

# Extension band (price vs the anchor). At/under HEALTHY the move is still
# orderly and the guard gives full credit; at/over BLOWOFF it's parabolic and the
# guard zeroes out. EXT_FLAG is the "stop chasing here" line the swing layer gates
# GO on — set inside the band so demand can be real (high score) yet too stretched
# to enter (extended=True). These should eventually scale with the name's own
# volatility rather than being fixed.
EXT_HEALTHY = 0.12
EXT_BLOWOFF = 0.40
EXT_FLAG = 0.20

# Guardrail 2 — confidence reaches 1.0 only as history approaches the mature
# threshold; a 3-bar name lands near 0.05.
CONFIDENCE_FULL_BARS = float(EMERGING_MAX_BARS)
# Below this confidence, even a strong score can't be called "healthy demand".
HEALTHY_MIN_CONFIDENCE = 0.30

# A big move off the anchor whose bars keep closing weak = sellers handing it off.
# This is the explicit SPCX detector: not real demand, so it also hard-caps the
# score (a weak-close blowoff must not score high just because volume/% are big).
_DISTRIBUTION_MIN_EXT = 0.08
_DISTRIBUTION_MAX_CLOSE = 0.35
_DISTRIBUTION_SCORE_CAP = 40

_W_VWAP = {"accumulation": 0.25, "close": 0.20, "volume": 0.20,
           "vwap": 0.15, "extension": 0.20}
_W_NO_VWAP = {"accumulation": 0.25 / 0.85, "close": 0.20 / 0.85,
              "volume": 0.20 / 0.85, "extension": 0.20 / 0.85}


@dataclass(frozen=True)
class EmergingScore:
    score: int                          # 0..EMERGING_SCORE_CAP — demand quality
    label: str                          # plain-English call, gated by confidence
    confidence: float                   # 0..1 — scales with bars of history
    extended: bool                      # True → too stretched to chase here
    bars: int                           # how many bars the read is based on
    extension_pct: float                # price vs anchor, % (signed)
    anchor: float                       # the reference price (offering / first open)
    components: dict[str, float] = field(default_factory=dict)  # 0..1 each
    flags: list[str] = field(default_factory=list)
    summary: str = ""                   # one-line expert sentence


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _accumulation(df, anchor: float) -> float:
    """Higher lows + holding above the anchor (offering / first open).

    Genuine accumulation steps its lows up and refuses to give back the anchor;
    a one-day pop that immediately undercuts its open is not accumulation.
    """
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    if len(lows) >= 2:
        higher = sum(1 for i in range(1, len(lows)) if lows[i] >= lows[i - 1])
        hl = higher / (len(lows) - 1)
    else:
        hl = 0.5                       # single bar — no trend to judge, stay neutral
    above = float((closes >= anchor).mean()) if anchor > 0 else 0.5
    return _clip(0.6 * hl + 0.4 * above)


def _close_strength(df) -> float:
    """Average close-in-range, weighted toward the most recent bars.

    (close − low) / (high − low): 1.0 closes on the high (buyers won the day),
    0.0 closes on the low (the move was sold into). This is the term that refuses
    to reward an SPCX-style big-green-but-closed-weak bar.
    """
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    n = len(closes)
    pos, weights = [], []
    for i in range(n):
        rng = highs[i] - lows[i]
        pos.append((closes[i] - lows[i]) / rng if rng > 1e-9 else 0.5)
        weights.append(i + 1)          # linear recency weight
    total = sum(weights)
    return _clip(sum(p * w for p, w in zip(pos, weights)) / total) if total else 0.5


def _volume_confirmation(df) -> tuple[float, bool]:
    """(score, fading) — share of volume on up-days, penalized if volume fades
    while price keeps rising (a rise on drying-up volume is suspect)."""
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    vols = df["volume"].to_numpy(dtype=float)
    tot = float(vols.sum())
    if tot <= 0:
        return 0.5, False
    up = closes > opens
    up_share = float(vols[up].sum()) / tot
    fading = False
    if len(closes) >= 4:
        mid = len(closes) // 2
        price_rising = closes[-1] > closes[0]
        vol_falling = vols[mid:].mean() < vols[:mid].mean() * 0.85
        fading = bool(price_rising and vol_falling)
    return _clip(up_share * (0.7 if fading else 1.0)), fading


def _vwap_position(last_close: float, vwap: float | None) -> float | None:
    """0.5 at VWAP, 1.0 at +4% above, 0.0 at −4% below. None → term dropped."""
    if vwap is None or vwap <= 0:
        return None
    return _clip(0.5 + (last_close / vwap - 1) / 0.08)


def _extension_guard(extension: float) -> float:
    """Full credit up to EXT_HEALTHY above the anchor, ramping to 0 at EXT_BLOWOFF.

    Below the anchor (negative extension) there's no exhaustion risk, so a name
    trading at/under its anchor keeps full credit on this term."""
    if extension <= EXT_HEALTHY:
        return 1.0
    if extension >= EXT_BLOWOFF:
        return 0.0
    return _clip(1.0 - (extension - EXT_HEALTHY) / (EXT_BLOWOFF - EXT_HEALTHY))


def score_emerging(df, offering_price: float | None = None,
                   vwap: float | None = None) -> EmergingScore:
    """Score genuine demand on a low-history daily OHLCV frame.

    `offering_price`: IPO offering / known anchor. Falls back to the first bar's
    open. `vwap`: latest session VWAP if available (the VWAP term is dropped and
    the others renormalized when it's absent).
    """
    closes = df["close"].to_numpy(dtype=float)
    bars = len(closes)
    last = float(closes[-1])
    anchor = float(offering_price) if offering_price and offering_price > 0 \
        else float(df["open"].iloc[0])
    extension = (last / anchor - 1) if anchor > 0 else 0.0

    acc = _accumulation(df, anchor)
    cs = _close_strength(df)
    vol, fading = _volume_confirmation(df)
    vw = _vwap_position(last, vwap)
    eg = _extension_guard(extension)

    comps = {"accumulation": acc, "close": cs, "volume": vol, "extension": eg}
    if vw is not None:
        comps["vwap"] = vw
        weights = _W_VWAP
    else:
        weights = _W_NO_VWAP
    raw = sum(weights[k] * comps[k] for k in weights)
    score = min(EMERGING_SCORE_CAP, round(raw * 100))

    confidence = round(min(1.0, bars / CONFIDENCE_FULL_BARS), 2)
    extended = extension > EXT_FLAG
    distribution = extension >= _DISTRIBUTION_MIN_EXT and cs < _DISTRIBUTION_MAX_CLOSE
    if distribution:
        # Closing weak on a big move is distribution, not demand — strong
        # accumulation/volume terms must not let it score high.
        score = min(score, _DISTRIBUTION_SCORE_CAP)

    flags: list[str] = []
    if extended:
        flags.append("extended")
    if distribution:
        flags.append("distribution_risk")
    if fading:
        flags.append("fading_volume")
    if confidence < HEALTHY_MIN_CONFIDENCE:
        flags.append("low_history")

    # Label — the call, gated so thin history can't shout "buy".
    if distribution:
        label = "Distribution risk — buyers not in control"
    elif extended:
        label = "Demand, but extended — wait for a hold"
    elif score >= 65 and confidence >= HEALTHY_MIN_CONFIDENCE:
        label = "Healthy demand"
    elif score >= 50:
        label = "Building — watch"
    elif score >= 35:
        label = "Early / unproven"
    else:
        label = "Weak — no demand edge"

    summary = (
        f"{bars}-bar read ({confidence*100:.0f}% confidence): demand score {score}"
        f"/{EMERGING_SCORE_CAP}. ")
    if distribution:
        summary += (f"Up {extension*100:+.0f}% off the ${anchor:.2f} anchor but bars "
                     "keep closing weak — looks like distribution, not demand.")
    elif extended:
        summary += (f"Buying looks genuine, but price is {extension*100:+.0f}% above the "
                     f"${anchor:.2f} anchor — stretched; wait for a hold, don't chase.")
    elif score >= 65:
        summary += "Higher lows, strong closes and confirming volume — genuine demand."
    else:
        summary += "Not enough one-directional buying yet to call it demand."

    return EmergingScore(
        score=int(score), label=label, confidence=confidence, extended=extended,
        bars=bars, extension_pct=round(extension * 100, 1), anchor=round(anchor, 2),
        components={k: round(v, 2) for k, v in comps.items()},
        flags=flags, summary=summary,
    )

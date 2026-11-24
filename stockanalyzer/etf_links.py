"""ETF -> underlying linkage, so the bots never trade the same exposure twice.

The book is long-only: a bearish view is expressed by buying an inverse ETF. That
makes ``SNDK`` (long), ``SNDU`` (2x long SNDK) and ``SNDQ`` (inverse SNDK) three
tickers for ONE underlying. A bot already holding any of them must not open another:
the same sign doubles the exposure (overlap), the opposite sign is a self-hedge that
can only pay the fund's fees (opposite).

Links come from two places. ``auto``: parsed from the fund's own name
("GraniteShares 2x Long SNDK Daily ETF" -> SNDK, +2). ``user``: typed in, for names
that carry no ticker; a user link is never overwritten by auto-detection. Nothing
here touches the network on the entry hot path — :func:`conflict` reads the database
only; :func:`ensure` is the one place that looks a fund name up, once per ticker per
process.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterable

from .db.repositories.etf_links import EtfLinkRepository, normalize
from .db.repositories.trades import TradeRepository

logger = logging.getLogger(__name__)
_LINKS = EtfLinkRepository()
_TRADES = TradeRepository()

_LEVERAGE = re.compile(r"^(\d+(?:\.\d+)?)[xX]$")
_INVERSE = re.compile(r"\b(short|bear|inverse|ultrashort)\b", re.I)
_DIRECTION = re.compile(r"^(long|short|bull|bear|inverse|ultrashort)$", re.I)
_TICKER = re.compile(r"^[A-Z]{1,5}$")
_NOT_TICKERS = frozenset({
    "ETF", "ETN", "ETP", "DAILY", "LONG", "SHORT", "BULL", "BEAR", "INVERSE", "SHARES",
    "TARGET", "NYSE", "USD", "TRUST", "FUND", "THE", "AND", "ULTRA", "X", "INC", "CORP",
    "LLC", "ADR", "NASDAQ", "INDEX", "LEVERAGED", "I", "II",
})


@dataclass(frozen=True)
class EtfLink:
    etf: str
    underlying: str
    leverage: float          # signed: +2 = 2x long, -1 = inverse
    source: str = "user"

    @property
    def sign(self) -> int:
        return -1 if self.leverage < 0 else 1


def parse_fund_name(name: str) -> tuple[str, float] | None:
    """(underlying ticker, signed leverage) from a leveraged/inverse single-stock
    fund name, or None when the name is not one (plain ETF, ticker not in the name)."""
    words = (name or "").split()
    levs = [i for i, w in enumerate(words) if _LEVERAGE.match(w)]
    dirs = [i for i, w in enumerate(words) if _DIRECTION.match(w)]
    if not (levs or dirs):
        return None                           # a plain ETF, or "Ultra" alone: too weak

    def is_ticker(i: int) -> bool:
        return 0 <= i < len(words) and bool(_TICKER.match(words[i])) \
            and words[i] not in _NOT_TICKERS

    # The underlying sits right next to the direction or leverage word:
    # "2X Long SNDK", "TSLA Bull 2X", "Corgi NBIS 2x Daily". Never a stray capital
    # elsewhere in the name ("T-REX", "Trust I").
    near = [j for i in dirs + levs for j in (i + 1, i - 1)]
    ticker = next((words[j] for j in near if is_ticker(j)), None)
    if ticker is None:
        return None
    lev = float(_LEVERAGE.match(words[levs[0]]).group(1)) if levs else 1.0
    return ticker, -lev if _INVERSE.search(name) else lev


def _yf_names(ticker: str) -> list[str]:
    import yfinance as yf
    info = yf.Ticker(ticker).info or {}
    if str(info.get("quoteType", "")).upper() != "ETF":
        return []
    # Both: longName sometimes title-cases the ticker ("2X Long Sndk"), shortName not.
    return [n for n in (info.get("longName"), info.get("shortName")) if n]


# Swappable: tests replace it so nothing ever reaches the network.
_NAME_LOOKUP: Callable[[str], str | list[str] | None] = _yf_names
_attempted: set[tuple[str, str]] = set()


def detect(ticker: str, name_lookup: Callable[[str], str | list[str] | None] | None = None
           ) -> EtfLink | None:
    """Auto-detect ``ticker``'s link from its fund name(s); None if it isn't one."""
    t = normalize(ticker)
    try:
        names = (name_lookup or _NAME_LOOKUP)(t) or []
    except Exception:
        logger.warning("etf link lookup failed for %s", ticker, exc_info=True)
        return None
    for name in [names] if isinstance(names, str) else names:
        parsed = parse_fund_name(name)
        if parsed and parsed[0] != t:
            return EtfLink(t, parsed[0], parsed[1], "auto")
    return None


def ensure(ticker: str, *, user_id: str) -> None:
    """Auto-link ``ticker`` once per process (no-op when a link already exists)."""
    key = (user_id, normalize(ticker))
    if key in _attempted:
        return
    _attempted.add(key)
    try:
        if _LINKS.get(user_id, ticker) is None:
            link = detect(ticker)
            if link:
                _LINKS.upsert(user_id, link.etf, link.underlying, link.leverage, "auto")
    except Exception:
        logger.warning("etf auto-link failed for %s", ticker, exc_info=True)


def load(*, user_id: str) -> list[EtfLink]:
    return [EtfLink(**row) for row in _LINKS.list(user_id)]


def set_link(etf: str, underlying: str, leverage: float, *, user_id: str) -> None:
    """Define (or override) a link by hand. Leverage is signed; 0 is rejected."""
    if not leverage:
        raise ValueError("leverage must be non-zero (negative for an inverse ETF)")
    if normalize(etf) == normalize(underlying):
        raise ValueError("an ETF cannot be linked to itself")
    _LINKS.upsert(user_id, etf, underlying, leverage, "user")


def remove_link(etf: str, *, user_id: str) -> None:
    _LINKS.remove(user_id, etf)
    _attempted.discard((user_id, normalize(etf)))


def _root(links: Iterable[EtfLink], ticker: str) -> tuple[str, int]:
    """(underlying, sign) a ticker gives exposure to; an unlinked ticker is its own."""
    t = normalize(ticker)
    for link in links:
        if link.etf == t:
            return link.underlying, link.sign
    return t, 1


def related_held(links: list[EtfLink], ticker: str, held: Iterable[str]
                 ) -> list[tuple[str, str]]:
    """Held tickers that share ``ticker``'s underlying, as (held ticker, 'overlap' |
    'opposite'). The ticker itself is not reported (same-ticker is has_open's job)."""
    root, sign = _root(links, ticker)
    out = []
    for h in held:
        if normalize(h) == normalize(ticker):
            continue
        h_root, h_sign = _root(links, h)
        if h_root == root:
            out.append((h, "overlap" if h_sign == sign else "opposite"))
    return out


def conflict(user_id: str, ticker: str, trader: str) -> tuple[str, str] | None:
    """First (held ticker, kind) that makes ``trader`` opening ``ticker`` a duplicate
    or self-hedged exposure, else None. Database-only; fails open on error."""
    try:
        held = _TRADES.open_tickers(user_id, trader)
        if not held:
            return None
        hits = related_held(load(user_id=user_id), ticker, held)
        return hits[0] if hits else None
    except Exception:
        logger.warning("etf conflict check failed for %s", ticker, exc_info=True)
        return None


def blocked_trades(trades: list[dict], links: list[EtfLink]) -> list[int]:
    """The linked-ticker rule of ``virtualbook.has_open`` over one session's trades
    from several tickers, for simulators that replay each ticker in its own book.

    ``trades``: dicts with ``ticker``, ``trader``, ``in`` and ``out`` ("HH:MM";
    ``out`` None = held to the close). Walked in entry order; a trade is refused when
    the same trader still holds another ticker on the same underlying, and a refused
    trade holds nothing. Returns the refused indices."""
    held: list[tuple[str, str, str]] = []           # (trader, ticker, out)
    refused = []
    for i in sorted(range(len(trades)), key=lambda k: (trades[k]["in"], k)):
        t = trades[i]
        mine = [tk for trader, tk, out in held if trader == t["trader"] and out > t["in"]]
        if related_held(links, t["ticker"], mine):
            refused.append(i)
            continue
        held.append((t["trader"], t["ticker"], t["out"] or "99:99"))
    return sorted(refused)


def instruments_for(links: list[EtfLink], underlying: str, bullish: bool
                    ) -> list[EtfLink]:
    """Known ETFs that give ``underlying`` the wanted direction, most leveraged
    first. A long view wants positive-leverage funds, a bearish view inverse ones."""
    u = normalize(underlying)
    pool = [l for l in links if l.underlying == u and (l.sign > 0) == bullish]
    return sorted(pool, key=lambda l: -abs(l.leverage))

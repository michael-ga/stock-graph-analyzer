"""Virtual paper-trading book.

Every position is virtual ($1,000 stake each) but tracked against real prices:
manual buys ("me") and strategy bots trade side by side, and every close is
stored with the full prediction snapshot (score, setup, kind, failed checks…)
so the algorithm can be judged and improved from evidence.

Position lifecycle:
    pending  — armed breakout order; activates when price crosses `trigger`
    open     — live position; auto-closes at stop (conservative) or target,
               or expires at market after ~1.5× the horizon in calendar days
    closed   — final; carries exit price, reason and realized P&L

All functions are file-backed (`.virtualbook.json`) and take an optional `now`
for deterministic tests.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / ".virtualbook.json"

STAKE_USD = 1000.0          # virtual dollars per position
_EXPIRY_FACTOR = 1.5        # horizon trading-days → calendar-days cushion


def load(path: Path = _PATH) -> list[dict]:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _write(path: Path, positions: list[dict]) -> None:
    try:
        path.write_text(json.dumps(positions, indent=1))
    except Exception:
        pass


def has_open(ticker: str, trader: str, path: Path = _PATH) -> bool:
    ticker = ticker.upper()
    return any(p["ticker"] == ticker and p["trader"] == trader
               and p["status"] in ("open", "pending") for p in load(path))


def open_position(*, ticker: str, trader: str, entry: float, stop: float,
                  target: float, kind: str = "immediate",
                  trigger: float | None = None, horizon_days: int = 3,
                  stake: float = STAKE_USD, snapshot: dict | None = None,
                  now: float | None = None, path: Path = _PATH) -> dict:
    """Open a virtual position (or a pending breakout order when kind says so).

    `stake` is the simulated dollar amount put into the trade — P&L $ scales with
    it, so you can size positions and later compute real ratios/trends."""
    now = now or time.time()
    status = "pending" if (kind == "breakout_wait" and trigger) else "open"
    stake = float(stake) if stake and stake > 0 else STAKE_USD
    pos = dict(
        id=uuid.uuid4().hex[:10], ticker=ticker.upper(), trader=trader,
        status=status, kind=kind,
        opened_ts=now, opened=time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        activated_ts=(None if status == "pending" else now),
        entry=round(entry, 4), stop=round(stop, 4), target=round(target, 4),
        trigger=(round(trigger, 4) if trigger else None),
        stake=round(stake, 2),
        shares=round(stake / entry, 4) if entry else 0.0,
        horizon_days=int(horizon_days),
        snapshot=snapshot or {},
        exit_price=None, close_reason=None, closed=None,
        pnl_pct=0.0, pnl_usd=0.0,
    )
    positions = load(path)
    positions.append(pos)
    _write(path, positions)
    return pos


def _close(p: dict, exit_price: float, reason: str, now: float) -> None:
    p["status"] = "closed"
    p["exit_price"] = round(exit_price, 4)
    p["close_reason"] = reason
    p["closed"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    p["pnl_pct"] = round((exit_price / p["entry"] - 1) * 100, 2)
    p["pnl_usd"] = round((exit_price - p["entry"]) * p["shares"], 2)


def close_position(pid: str, exit_price: float, reason: str = "manual",
                   now: float | None = None, path: Path = _PATH) -> dict | None:
    now = now or time.time()
    positions = load(path)
    for p in positions:
        if p["id"] == pid and p["status"] in ("open", "pending"):
            if p["status"] == "pending":
                p["status"] = "closed"
                p["close_reason"] = "cancelled"
                p["closed"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
                p["exit_price"] = None
            else:
                _close(p, exit_price, reason, now)
            _write(path, positions)
            return p
    return None


def mark(ticker: str, price: float, now: float | None = None,
         path: Path = _PATH) -> list[dict]:
    """Mark a ticker's positions to `price`: activate pending breakout orders,
    auto-close stop/target hits (stop wins on ambiguity), expire stale trades.
    Returns the positions whose status changed (for toasts)."""
    if not price or price <= 0:
        return []
    now = now or time.time()
    ticker = ticker.upper()
    positions = load(path)
    changed: list[dict] = []
    for p in positions:
        if p["ticker"] != ticker or p["status"] not in ("open", "pending"):
            continue
        expiry_s = p["horizon_days"] * _EXPIRY_FACTOR * 86400
        if p["status"] == "pending":
            if price >= p["trigger"]:
                p["status"] = "open"
                p["activated_ts"] = now
                p["entry"] = p["trigger"]            # filled at the trigger
                p["shares"] = round(STAKE_USD / p["entry"], 4)
                changed.append(p)
            elif now - p["opened_ts"] > expiry_s:
                p["status"] = "closed"
                p["close_reason"] = "cancelled"
                p["closed"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
                changed.append(p)
            continue
        # open longs: stop first (conservative), then target, then expiry.
        if price <= p["stop"]:
            _close(p, p["stop"], "stop_hit", now)
            changed.append(p)
        elif price >= p["target"]:
            _close(p, p["target"], "target_hit", now)
            changed.append(p)
        elif now - (p.get("activated_ts") or p["opened_ts"]) > expiry_s:
            _close(p, price, "expired", now)
            changed.append(p)
    if changed:
        _write(path, positions)
    return changed


# --------------------------------------------------------------------------- #
# Analytics — the evidence for improving the algorithm.
# --------------------------------------------------------------------------- #
def _band(score) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "?"
    return "80+" if s >= 80 else "70–79" if s >= 70 else "60–69" if s >= 60 else "<60"


def _agg(rows: list[dict]) -> dict:
    wins = [p for p in rows if p["pnl_pct"] > 0]
    losses = [p for p in rows if p["pnl_pct"] <= 0]
    n = len(rows)
    return dict(
        n=n, wins=len(wins), losses=len(losses),
        win_rate=(round(len(wins) / n * 100) if n else None),
        avg_pnl_pct=(round(sum(p["pnl_pct"] for p in rows) / n, 2) if n else None),
        total_pnl_usd=round(sum(p["pnl_usd"] for p in rows), 2),
    )


def stats(positions: list[dict]) -> dict:
    """Per-trader / per-setup / per-score-band aggregates over closed trades
    (cancelled pending orders excluded)."""
    closed = [p for p in positions
              if p["status"] == "closed" and p.get("close_reason") != "cancelled"]
    by_trader: dict = {}
    by_setup: dict = {}
    by_band: dict = {}
    for p in closed:
        by_trader.setdefault(p["trader"], []).append(p)
        by_setup.setdefault(p.get("snapshot", {}).get("setup", "?"), []).append(p)
        by_band.setdefault(_band(p.get("snapshot", {}).get("score")), []).append(p)
    return dict(
        totals=_agg(closed),
        traders={k: _agg(v) for k, v in sorted(by_trader.items())},
        setups={k: _agg(v) for k, v in sorted(by_setup.items())},
        bands={k: _agg(v) for k, v in sorted(by_band.items())},
    )

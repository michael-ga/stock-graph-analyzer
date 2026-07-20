"""PostgreSQL repository preserving the virtual-book behavior."""
from __future__ import annotations

import hashlib
import time
from collections import defaultdict

from sqlalchemy import select

from stockanalyzer.db.models import (
    Trade, TradeIndicator, TradeSignal, TradeSwingCheck, TradeVerdict,
)
from stockanalyzer.db.session import session_scope

_CONFIRM_MARGIN = 0.002


def _cohort_id(trade: dict, snapshot: dict) -> str:
    key = "|".join([
        trade.get("ticker", "").upper(), snapshot.get("setup", ""), trade.get("kind", ""),
        f"{float(trade.get('entry', 0)):.2f}", f"{float(trade.get('stop', 0)):.2f}",
        f"{float(trade.get('target', 0)):.2f}", trade.get("opened", "")[:10],
    ])
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _dict(row: Trade) -> dict:
    return {
        "id": row.id, "ticker": row.ticker, "trader": row.trader,
        "status": row.status, "kind": row.kind, "opened_ts": row.opened_ts,
        "opened": row.opened, "activated_ts": row.activated_ts,
        "broke_out_ts": row.broke_out_ts, "entry": row.entry, "stop": row.stop,
        "target": row.target, "trigger": row.trigger_price, "stake": row.stake,
        "shares": row.shares, "horizon_days": row.horizon_days,
        "exit_price": row.exit_price, "close_reason": row.close_reason,
        "closed_ts": row.closed_ts, "closed": row.closed, "pnl_pct": row.pnl_pct,
        "pnl_usd": row.pnl_usd, "snapshot": row.snapshot or {},
        "cohort_id": row.cohort_id,
    }


def _agg(rows: list[dict]) -> dict:
    wins = [p for p in rows if p["pnl_pct"] > 0]
    n = len(rows)
    return {
        "n": n, "wins": len(wins), "losses": n - len(wins),
        "win_rate": round(len(wins) / n * 100) if n else None,
        "avg_pnl_pct": round(sum(p["pnl_pct"] for p in rows) / n, 2) if n else None,
        "total_pnl_usd": round(sum(p["pnl_usd"] for p in rows), 2),
    }


def _band(score) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "?"
    return "80+" if value >= 80 else "70–79" if value >= 70 else "60–69" if value >= 60 else "<60"


class TradeRepository:
    def list(self, user_id: str) -> list[dict]:
        with session_scope() as session:
            rows = session.scalars(
                select(Trade).where(Trade.user_id == user_id).order_by(Trade.opened_ts.desc())
            ).all()
            return [_dict(row) for row in rows]

    def has_open(self, user_id: str, ticker: str, trader: str) -> bool:
        with session_scope() as session:
            return session.scalar(select(Trade.id).where(
                Trade.user_id == user_id, Trade.ticker == ticker.upper(),
                Trade.trader == trader, Trade.status.in_(("open", "pending")),
            ).limit(1)) is not None

    def insert(self, user_id: str, trade: dict, context: dict | None = None) -> dict:
        snapshot = context or trade.get("snapshot") or {}
        with session_scope() as session:
            row = Trade(
                id=trade["id"], user_id=user_id, ticker=trade["ticker"].upper(),
                trader=trade["trader"], status=trade["status"], kind=trade["kind"],
                opened_ts=trade["opened_ts"], opened=trade["opened"],
                activated_ts=trade.get("activated_ts"), broke_out_ts=trade.get("broke_out_ts"),
                entry=trade["entry"], stop=trade["stop"], target=trade["target"],
                trigger_price=trade.get("trigger"), stake=trade.get("stake", 1000),
                shares=trade.get("shares", 0), horizon_days=trade.get("horizon_days", 3),
                exit_price=trade.get("exit_price"), close_reason=trade.get("close_reason"),
                closed_ts=trade.get("closed_ts"), closed=trade.get("closed"),
                pnl_pct=trade.get("pnl_pct", 0.0) or 0.0,
                pnl_usd=trade.get("pnl_usd", 0.0) or 0.0,
                snapshot=snapshot, cohort_id=trade.get("cohort_id") or _cohort_id(trade, snapshot),
            )
            session.add(row)
            for timeframe, info in (snapshot.get("timeframes") or {}).items():
                for signal in info.get("signals") or []:
                    session.add(TradeSignal(
                        trade_id=row.id, timeframe=timeframe, name=signal["name"],
                        direction=signal["direction"], strength=signal["strength"],
                        category=signal["category"], evidence=signal.get("evidence"),
                    ))
                if info.get("indicators"):
                    session.add(TradeIndicator(
                        trade_id=row.id, timeframe=timeframe,
                        payload={**info["indicators"], "bias_score": info.get("bias_score"),
                                 "trend_dir": info.get("trend_dir")},
                    ))
            if snapshot.get("verdict"):
                session.add(TradeVerdict(trade_id=row.id, payload=snapshot["verdict"]))
            for check in snapshot.get("checks") or []:
                session.add(TradeSwingCheck(
                    trade_id=row.id, name=check["name"], ok=bool(check["ok"]),
                    na=bool(check.get("na", False)), detail=check.get("detail"),
                    weight=int(check.get("weight", 0)),
                ))
        return trade

    def close(self, user_id: str, trade_id: str, exit_price: float | None,
              reason: str, now: float | None = None) -> dict | None:
        now = now or time.time()
        with session_scope() as session:
            row = session.scalar(select(Trade).where(
                Trade.id == trade_id, Trade.user_id == user_id,
                Trade.status.in_(("open", "pending")),
            ).with_for_update())
            if row is None:
                return None
            if row.status == "pending":
                self._finish(row, None, "cancelled", now)
            elif exit_price is None:
                return None
            else:
                self._finish(row, exit_price, reason, now)
            session.flush()
            return _dict(row)

    @staticmethod
    def _finish(row: Trade, exit_price: float | None, reason: str, now: float) -> None:
        row.status = "closed"; row.close_reason = reason; row.closed_ts = now
        row.closed = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        if exit_price is not None:
            row.exit_price = round(float(exit_price), 4)
            row.pnl_pct = round((exit_price / row.entry - 1) * 100, 2)
            row.pnl_usd = round((exit_price - row.entry) * row.shares, 2)

    def mark(self, user_id: str, ticker: str, price: float,
             now: float | None = None) -> list[dict]:
        if not price or price <= 0:
            return []
        now = now or time.time(); changed = []
        with session_scope() as session:
            rows = session.scalars(select(Trade).where(
                Trade.user_id == user_id, Trade.ticker == ticker.upper(),
                Trade.status.in_(("open", "pending")),
            ).with_for_update()).all()
            for row in rows:
                stale = now - row.opened_ts > row.horizon_days * 1.5 * 86400
                if row.status == "pending":
                    trigger = row.trigger_price or row.entry
                    if row.broke_out_ts is None:
                        if price >= trigger * (1 + _CONFIRM_MARGIN):
                            row.broke_out_ts = now
                        elif stale:
                            self._finish(row, None, "cancelled", now); changed.append(_dict(row))
                        continue
                    if price <= row.stop:
                        self._finish(row, None, "cancelled", now); changed.append(_dict(row))
                    elif price <= row.entry:
                        row.status = "open"; row.activated_ts = now
                        row.shares = round(row.stake / row.entry, 4); changed.append(_dict(row))
                    elif stale:
                        self._finish(row, None, "cancelled", now); changed.append(_dict(row))
                    continue
                reference = row.activated_ts or row.opened_ts
                if price <= row.stop:
                    self._finish(row, row.stop, "stop_hit", now); changed.append(_dict(row))
                elif price >= row.target:
                    self._finish(row, row.target, "target_hit", now); changed.append(_dict(row))
                elif now - reference > row.horizon_days * 1.5 * 86400:
                    self._finish(row, price, "expired", now); changed.append(_dict(row))
        return changed

    def stats(self, user_id: str) -> dict:
        closed = [p for p in self.list(user_id)
                  if p["status"] == "closed" and p.get("close_reason") != "cancelled"]
        groups = {"traders": defaultdict(list), "setups": defaultdict(list), "bands": defaultdict(list)}
        for trade in closed:
            snapshot = trade.get("snapshot") or {}
            groups["traders"][trade["trader"]].append(trade)
            groups["setups"][snapshot.get("setup", "?")].append(trade)
            groups["bands"][_band(snapshot.get("score"))].append(trade)
        return {"totals": _agg(closed), **{
            name: {key: _agg(value) for key, value in sorted(group.items())}
            for name, group in groups.items()
        }}

    def algorithm_correctness(self, user_id: str) -> dict:
        closed = [p for p in self.list(user_id)
                  if p["status"] == "closed" and p.get("close_reason") != "cancelled"]
        cohorts: dict[str, list[dict]] = defaultdict(list)
        for trade in closed:
            cohorts[trade.get("cohort_id") or trade["id"]].append(trade)
        ideas = []
        for cohort_id, trades in cohorts.items():
            pnls = [t["pnl_pct"] for t in trades]; snapshot = trades[0].get("snapshot") or {}
            mean = round(sum(pnls) / len(pnls), 2)
            ideas.append({"cohort_id": cohort_id, "ticker": trades[0]["ticker"],
                          "setup": snapshot.get("setup", "?"), "score": snapshot.get("score"),
                          "band": _band(snapshot.get("score")), "n_trades": len(trades),
                          "traders": sorted({t["trader"] for t in trades}),
                          "idea_pnl_pct": mean, "win": mean > 0,
                          "best_pnl_pct": max(pnls), "worst_pnl_pct": min(pnls)})
        def aggregate(items):
            n = len(items); wins = sum(i["win"] for i in items)
            return {"n_ideas": n, "n_trades": sum(i["n_trades"] for i in items),
                    "wins": wins, "losses": n - wins,
                    "win_rate": round(wins / n * 100) if n else None,
                    "avg_pnl_pct": round(sum(i["idea_pnl_pct"] for i in items) / n, 2) if n else None}
        setups = defaultdict(list); bands = defaultdict(list)
        for idea in ideas:
            setups[idea["setup"]].append(idea); bands[idea["band"]].append(idea)
        return {"totals": aggregate(ideas),
                "setups": {k: aggregate(v) for k, v in sorted(setups.items())},
                "bands": {k: aggregate(v) for k, v in sorted(bands.items())},
                "ideas": sorted(ideas, key=lambda item: item["idea_pnl_pct"])}

"""Controlled sign-in benchmark: 14 tracked stocks and simulated slow market calls.

Uses an isolated temporary SQLite database and never connects to production data.
Pass --baseline /path/to/old-app.py to compare the prior rendering path.
"""
from __future__ import annotations

import argparse
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def benchmark(source: Path, tickers: int, delay: float) -> dict:
    from sqlalchemy import insert
    from streamlit.testing.v1 import AppTest
    from stockanalyzer.auth import AuthService
    from stockanalyzer.db.models import Base, Trade
    from stockanalyzer.db.repositories.swingwatch import SwingWatchRepository
    from stockanalyzer.db.session import configure_engine, session_scope
    import streamlit as st

    calls = []

    def slow_market(ticker, *args, **kwargs):
        calls.append(ticker)
        time.sleep(delay)
        return SimpleNamespace(reports={}, quote=None)

    with tempfile.TemporaryDirectory(prefix="stock-login-benchmark-") as directory:
        url = f"sqlite:///{directory}/benchmark.db"
        with patch.dict(os.environ, {"DATABASE_URL": url, "APP_ENV": "development",
                                    "FINNHUB_KEY": "", "TWELVEDATA_KEY": ""}):
            engine = configure_engine(url)
            Base.metadata.create_all(engine)
            owner = AuthService().create_user("benchmark", "benchmark password long enough")
            for index in range(tickers):
                SwingWatchRepository().add(owner.id, f"TEST{index}")
            with session_scope() as database:
                database.execute(insert(Trade), [{
                    "id": f"history-{index}", "user_id": owner.id, "ticker": "TEST0", "trader": "me",
                    "status": "closed", "kind": "immediate", "opened_ts": 1.0,
                    "opened": "1970-01-01", "entry": 100, "stop": 95, "target": 110,
                    "init_stop": 95, "exit_price": 110, "close_reason": "target_hit",
                    "closed_ts": 2.0, "closed": "1970-01-01", "pnl_pct": 10, "pnl_usd": 100,
                    "snapshot": {"score": 75, "setup": "benchmark"},
                } for index in range(1000)])
            st.cache_data.clear()
            with patch("stockanalyzer.pipeline.analyze_ticker", slow_market):
                app = AppTest.from_file(str(source)).run(timeout=60)
                app.text_input[0].set_value("benchmark")
                app.text_input[1].set_value("benchmark password long enough")
                started = time.perf_counter()
                next(button for button in app.button if button.label == "Sign in").click().run(timeout=120)
                elapsed = (time.perf_counter() - started) * 1000
                if app.exception:
                    raise RuntimeError(str(app.exception[0].message))
                result = {"source": str(source), "login_ms": round(elapsed),
                          "market_calls_during_login": len(calls), "tracked_tickers": tickers,
                          "simulated_market_delay_ms": delay * 1000, "closed_history_rows": 1000}
            engine.dispose()
            st.cache_data.clear()
            return result


def main() -> int:
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--tickers", type=int, default=14)
    parser.add_argument("--market-delay", type=float, default=0.2)
    args = parser.parse_args()
    if args.baseline:
        print(json.dumps(benchmark(args.baseline, args.tickers, args.market_delay)))
    result = benchmark(Path(__file__).resolve().parents[1] / "app.py", args.tickers, args.market_delay)
    print(json.dumps(result))
    return int(result["market_calls_during_login"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())

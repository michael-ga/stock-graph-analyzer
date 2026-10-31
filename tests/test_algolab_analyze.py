"""Smoke test: the algolab post-mortem runs on the repositories, scoped to one user."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from stockanalyzer import virtualbook as vb
from stockanalyzer.auth import AuthService

_SCRIPT = Path(__file__).resolve().parent.parent / "algolab" / "analyze.py"


def _analyze():
    spec = importlib.util.spec_from_file_location("algolab_analyze", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_post_mortem_reports_every_view_for_the_only_user(book_db, owner, capsys):
    snap = {"score": 72, "setup": "Pullback to 20-EMA",
            "checks": [{"name": "Engine bias agrees", "ok": False, "weight": 2}]}
    for trader in ("bot-GO", "me"):                       # one idea, two bots
        pos = vb.open_position(ticker="NOK", trader=trader, entry=14.0, stop=13.3,
                               target=14.8, snapshot=snap, now=1_000_000.0,
                               user_id=owner)
        vb.close_position(pos["id"], 13.5, now=1_000_100.0, user_id=owner)

    _analyze().main(["--database-url", str(book_db.url)])
    out = capsys.readouterr().out
    assert "book owner: book-owner" in out
    assert "1 distinct ideas from 2 bot-trades" in out
    assert "Engine bias agrees" in out
    assert "over 2 closed trades" in out


def test_post_mortem_requires_user_when_ambiguous(book_db, owner):
    AuthService().create_user("second-owner", "another long password")
    analyze = _analyze()
    with pytest.raises(SystemExit, match="--user"):
        analyze.main(["--database-url", str(book_db.url)])
    analyze.main(["--database-url", str(book_db.url), "--user", "second-owner"])


def test_post_mortem_requires_a_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="DATABASE_URL"):
        _analyze().main([])

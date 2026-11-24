"""Trade export: read-only dump of the journal and the virtual book."""
from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest

from scripts import export_trades as ex
from stockanalyzer import papertrade
from stockanalyzer import virtualbook as vb


@pytest.fixture()
def filled(owner):
    papertrade.record(dict(ts=1_000_000.0, date="2026-11-02T04:00:00", ticker="NOK",
                           level=60, score=65, kind="immediate", setup="Support test",
                           entry=13.8, stop=13.2, target=14.5, rr=1.2,
                           status="open", result_pct=0.0), user_id=owner)
    snap = {"score": 70, "setup": "Pullback to 20-EMA"}
    a = vb.open_position(ticker="NOK", trader="bot-GO", entry=14.0, stop=13.3,
                         target=14.8, snapshot=snap, now=1_000_000.0, user_id=owner)
    vb.open_position(ticker="MU", trader="bot-BRK", entry=100.0, stop=95.0,
                     target=110.0, snapshot=snap, now=1_000_000.0, user_id=owner)
    vb.close_position(a["id"], 14.8, reason="target_hit", now=1_000_100.0, user_id=owner)
    return owner


def _no_reconfigure(monkeypatch):
    """main() must reuse the test database, not DATABASE_URL."""
    monkeypatch.setattr(ex, "configure_engine", lambda url=None: None)
    monkeypatch.setattr(ex.Settings, "from_env", classmethod(lambda cls: type(
        "S", (), {"database_url": None})()))


def test_collect_splits_tables(filled):
    data = ex.collect(filled)
    assert len(data["swing_journal"]) == 1
    assert [p["ticker"] for p in data["bot_positions"]] == ["MU"]
    hist = data["bot_history"]
    assert len(hist) == 1 and hist[0]["close_reason"] == "target_hit"
    assert hist[0]["score"] == 70 and hist[0]["setup"] == "Pullback to 20-EMA"


def test_csv_puts_app_columns_first(filled):
    files = ex.build_files(ex.collect(filled), "book-owner")
    header = next(csv.reader(io.StringIO(files["bot_history.csv"])))
    assert header[:4] == ["closed_ts", "ticker", "trader", "close_reason"]
    header = next(csv.reader(io.StringIO(files["swing_journal.csv"])))
    assert header[:3] == ["date", "ticker", "level"]
    assert json.loads(files["all.json"])["counts"] == {
        "swing_journal": 1, "bot_positions": 1, "bot_history": 1}


def test_main_out_folder(filled, tmp_path, monkeypatch):
    _no_reconfigure(monkeypatch)
    ex.main(["--username", "book-owner", "--out", str(tmp_path / "x")])
    names = sorted(p.name for p in (tmp_path / "x").iterdir())
    assert names == ["all.json", "bot_history.csv", "bot_history.json",
                     "bot_positions.csv", "bot_positions.json",
                     "swing_journal.csv", "swing_journal.json"]


def test_main_zip_to_stdout(filled, monkeypatch, capsysbinary):
    _no_reconfigure(monkeypatch)
    ex.main(["--username", "book-owner"])
    zf = zipfile.ZipFile(io.BytesIO(capsysbinary.readouterr().out))
    assert "all.json" in zf.namelist()


def test_unknown_user(filled, monkeypatch):
    _no_reconfigure(monkeypatch)
    with pytest.raises(SystemExit, match="No user"):
        ex.main(["--username", "nobody"])


def _raw_rows(owner: str) -> dict:
    """What fetch_trades.sh's psql query returns: whole table rows as JSON."""
    from sqlalchemy import select
    from stockanalyzer.db.models import PaperTrade, Trade
    from stockanalyzer.db.session import session_scope

    def cols(model, obj):
        return {c.name: getattr(obj, model.__mapper__.get_property_by_column(c).key)
                for c in model.__table__.columns}
    with session_scope() as s:
        journal = [cols(PaperTrade, o) for o in s.scalars(
            select(PaperTrade).where(PaperTrade.user_id == owner))]
        book = [cols(Trade, o) for o in s.scalars(
            select(Trade).where(Trade.user_id == owner))]
    return json.loads(json.dumps({"journal": journal, "book": book}, default=str))


def test_raw_sql_rows_export_like_the_app(filled, tmp_path):
    """sql mode (raw psql rows) and app mode (repositories) give the same files."""
    assert ex.from_raw(_raw_rows(filled)) == ex.collect(filled)


def test_main_from_raw(filled, tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "configure_engine", lambda url=None: pytest.fail("no DB"))
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(_raw_rows(filled)), encoding="utf-8")
    ex.main(["--username", "book-owner", "--from-raw", str(raw), "--out", str(tmp_path / "o")])
    hist = json.loads((tmp_path / "o" / "bot_history.json").read_text(encoding="utf-8"))
    assert hist[0]["close_reason"] == "target_hit" and "user_id" not in hist[0]


def test_from_raw_empty_means_unknown_user(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="No user"):
        ex.main(["--username", "ghost", "--from-raw", str(raw), "--out", str(tmp_path)])


def test_export_writes_nothing(filled):
    before = (len(papertrade.load(user_id=filled)), len(vb.load(user_id=filled)))
    ex.build_files(ex.collect(filled), "book-owner")
    assert (len(papertrade.load(user_id=filled)), len(vb.load(user_id=filled))) == before

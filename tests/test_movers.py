"""Most-active screener parsing (pure — no network)."""
from stockanalyzer.data.movers import Mover, parse_movers


def _quote(sym="AAPL", **over):
    q = dict(symbol=sym, shortName="Apple Inc.", regularMarketPrice=201.5,
             regularMarketChangePercent=1.25, regularMarketVolume=55_000_000,
             marketCap=3.1e12)
    q.update(over)
    return q


def test_parse_basic_fields():
    movers = parse_movers({"quotes": [_quote()]})
    assert movers == [Mover("AAPL", "Apple Inc.", 201.5, 1.25, 55_000_000, 3.1e12)]


def test_parse_uppercases_and_strips_symbol():
    movers = parse_movers({"quotes": [_quote(sym=" smci ")]})
    assert movers[0].symbol == "SMCI"


def test_parse_skips_blank_symbols_and_non_dicts():
    payload = {"quotes": [_quote(sym=""), "garbage", None, _quote(sym="INTC")]}
    movers = parse_movers(payload)
    assert [m.symbol for m in movers] == ["INTC"]


def test_parse_missing_numbers_become_none():
    q = _quote(regularMarketPrice=None, regularMarketChangePercent="n/a",
               regularMarketVolume=None, marketCap=None)
    m = parse_movers({"quotes": [q]})[0]
    assert m.price is None and m.change_pct is None
    assert m.volume is None and m.market_cap is None


def test_parse_falls_back_to_long_name():
    q = _quote(shortName=None, longName="American Airlines Group")
    assert parse_movers({"quotes": [q]})[0].name == "American Airlines Group"


def test_parse_respects_count():
    payload = {"quotes": [_quote(sym=f"T{i}") for i in range(25)]}
    assert len(parse_movers(payload, count=10)) == 10


def test_parse_handles_empty_payloads():
    assert parse_movers(None) == []
    assert parse_movers({}) == []
    assert parse_movers({"quotes": []}) == []

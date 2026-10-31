"""Pin the bot's trading execution to the reviewed baseline, commit 8337e8d.

Why this exists: commit 74d23d0 on ``dev`` rewired *when, where and on what* the
bots ran, while presenting itself as a view/performance change. No scoring formula
changed, so every indicator and strategy test still passed, yet the recorded
trades changed. It was rolled back in 9493702; see docs/bot-execution-rollback.md.
This file is the guard that rollback added: any edit to these execution entry
points must fail CI until it is reviewed as a bot-behavior change.

This branch never contained 74d23d0, and its execution already equals the
baseline. Ten functions are pinned whole. ``_portfolio_panel`` is pinned by its
trading structure instead (see that test), because this branch reworked its
*reporting* (per-bot report card, ``virtualbook.bot_report``), while every
statement that touches a position stayed byte-identical to the baseline.
"""
import ast
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _functions(path: str) -> dict[str, ast.FunctionDef]:
    # Explicit UTF-8: app.py holds emoji, and Windows defaults to cp1252.
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _fingerprint(node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


@pytest.mark.parametrize("path,name,expected", [
    ('app.py', '_quiet_price', '8fe34cfb7a223205c8973e5137ce3f08bef25269db8be953b16c0d4e043a0f7d'),
    ('app.py', '_radar_panel', '88b031dc0e4afd862bd67f85596d9869fdd71a71889c6fec060d44d265f89503'),
    ('app.py', '_radar_body', 'c35c1322445da2468d38562d12a9690fc660f9b0d332b8f03e1c599196b5477c'),
    ('app.py', '_run_bots', 'cadd9e7c774794fae2ea0effcc83841a3c68ac02d739e0f231b610306a7f74a5'),
    ('app.py', '_run_reversal_bots', 'babb608f42296ec7d11a52781c1e80a1e92ee051077cb0a4d597abaa2ad7706c'),
    ('app.py', '_radar_plan', '75dbd52c4a7fd38e22fa5d4bcb4b406eac827f837e2afa7904ecbf0e901a9038'),
    ('app.py', '_sidebar', '04ff1e461014efc651e4b78f8f3044afc41cfe9dce9b5de6a55fd117052d6a40'),
    ('stockanalyzer/radar_worker.py', 'run_forever', '1d78ca2bf5681f7321e7fd492d6a594cb894c919431004cc32103d117252d1af'),
    ('stockanalyzer/radar_worker.py', 'process_analysis', '5fd77ed36f903a34387accfbbd8edf3171489d2f0ee4b31ddd59c5121848b2b2'),
    ('stockanalyzer/radar_worker.py', 'build_radar_plan', '29e2b2a05fef6ae8509d9cb4d6cdd7eb4201c363d34a3faabdb98b4bee8cf321'),
])
def test_original_trading_execution_is_preserved(path, name, expected):
    assert _fingerprint(_functions(path)[name]) == expected, f"Trading execution changed: {path}:{name}"


# --------------------------------------------------------------------------- #
# _portfolio_panel: the reporting may change, the trading may not.
# --------------------------------------------------------------------------- #
# Calls into trading modules the panel may make, and why each is allowed. A call
# to anything else in these modules (manage, open_position, a bot runner, …)
# fails: it would make a page render trade.
_PORTFOLIO_READS = {
    "virtualbook.load", "virtualbook.stats", "virtualbook.bot_report",
    "virtualbook.algorithm_correctness", "virtualbook.managed_vs_fixed",
    "virtualbook.TRADER_RULES.items",
}
# Calls that change a position, in order, exactly as in 8337e8d: the 30-second
# stop/target marking, and the user's own "close" button.
_PORTFOLIO_WRITES = [
    "_quiet_price(tk)",
    "virtualbook.mark(tk, px, user_id=st.session_state.auth_user_id)",
    "virtualbook.close_position(pid, px or (pos or {}).get('entry', 0), "
    "user_id=st.session_state.auth_user_id)",
]
_TRADING_PREFIXES = ("virtualbook.", "papertrade.", "swingwatch.", "_run_", "_quiet_")
# The marking loop (which positions, which price source, the mark call), hashed
# from 8337e8d. Byte-identical on this branch.
_MARK_LOOP = "653ebf41650e36760040a42a3ed5cdea87e4539d3fe53f05700bc0e3e522dc92"


def _portfolio_panel() -> ast.FunctionDef:
    return _functions("app.py")["_portfolio_panel"]


def test_portfolio_panel_keeps_its_30s_marking_cadence():
    fn = _portfolio_panel()
    decorators = [ast.unparse(d) for n in ast.walk(fn)
                  if isinstance(n, ast.FunctionDef) and n is not fn for d in n.decorator_list]
    assert decorators == ["st.fragment(run_every='30s')"]


def test_portfolio_panel_marks_the_same_positions_the_same_way():
    loops = [n for n in ast.walk(_portfolio_panel())
             if isinstance(n, ast.For) and "_quiet_price" in ast.unparse(n)]
    assert len(loops) == 1, "expected exactly one price-and-mark loop"
    assert _fingerprint(loops[0]) == _MARK_LOOP, "the portfolio's stop/target marking changed"
    live = {ast.unparse(n) for n in ast.walk(_portfolio_panel()) if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "live" for t in n.targets)}
    assert live == {"live = [p for p in book if p['status'] in ('open', 'pending')]"}


def test_portfolio_panel_trades_only_through_the_reviewed_calls():
    calls = [n for n in ast.walk(_portfolio_panel()) if isinstance(n, ast.Call)]
    trading = [n for n in calls if ast.unparse(n.func).startswith(_TRADING_PREFIXES)]
    writes = [ast.unparse(n) for n in trading if ast.unparse(n.func) not in _PORTFOLIO_READS]
    assert writes == _PORTFOLIO_WRITES, f"new or changed trading call in _portfolio_panel: {writes}"

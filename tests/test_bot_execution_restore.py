"""Pin the restored trading execution to the pre-view-change commit 8337e8d."""
import ast
import copy
import hashlib
from pathlib import Path

import pytest


def _expand_portfolio_presentation(node, functions):
    """Undo only the approved UI extraction before checking the ORIGINAL hash.

    No trading statement is excluded. Inlining the report's exact body also
    catches additions there. Pin the new wrapper so it cannot conceal trading
    calls or change the parent fragment's timer.
    """
    expected = ast.parse('''
@st.fragment
def _portfolio_reports(book, s, tot, wr) -> None:
    """Opening reports reruns only this view, without repeating trading checks."""
    if st.toggle("Show closed-trade analytics and history", key="portfolio_reports_open"):
        _render_portfolio_reports(book, s, tot, wr)
''').body[0]
    assert ast.dump(functions['_portfolio_reports']) == ast.dump(expected)
    report = functions['_render_portfolio_reports']
    assert ast.dump(report.args) == ast.dump(expected.args)
    assert not report.decorator_list

    class InlineReport(ast.NodeTransformer):
        calls = 0
        keys = 0

        def visit_Expr(self, expression):
            call = expression.value
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == '_portfolio_reports':
                assert ast.dump(call) == ast.dump(ast.parse('_portfolio_reports(book, s, tot, wr)').body[0].value)
                self.calls += 1
                return copy.deepcopy(report.body)
            return self.generic_visit(expression)

        def visit_Call(self, call):
            if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id == 'st' and call.func.attr == 'expander':
                for keyword in list(call.keywords):
                    if keyword.arg == 'key' and isinstance(keyword.value, ast.Constant) and keyword.value.value == 'virtual-portfolio':
                        call.keywords.remove(keyword)
                        self.keys += 1
            return self.generic_visit(call)

    inline = InlineReport()
    restored = inline.visit(copy.deepcopy(node))
    assert (inline.calls, inline.keys) == (1, 1)
    return restored


@pytest.mark.parametrize("path,name,expected", [
    ('app.py', '_quiet_price', '8fe34cfb7a223205c8973e5137ce3f08bef25269db8be953b16c0d4e043a0f7d'),
    ('app.py', '_radar_panel', '88b031dc0e4afd862bd67f85596d9869fdd71a71889c6fec060d44d265f89503'),
    ('app.py', '_radar_body', 'c35c1322445da2468d38562d12a9690fc660f9b0d332b8f03e1c599196b5477c'),
    ('app.py', '_portfolio_panel', '75ef231d3c9f31ea7bad5eaabe42b48f152d1a75c0b5c2778f0d00f7c4de3f67'),
    ('app.py', '_run_bots', 'cadd9e7c774794fae2ea0effcc83841a3c68ac02d739e0f231b610306a7f74a5'),
    ('app.py', '_run_reversal_bots', 'babb608f42296ec7d11a52781c1e80a1e92ee051077cb0a4d597abaa2ad7706c'),
    ('app.py', '_radar_plan', '75dbd52c4a7fd38e22fa5d4bcb4b406eac827f837e2afa7904ecbf0e901a9038'),
    ('app.py', '_sidebar', '04ff1e461014efc651e4b78f8f3044afc41cfe9dce9b5de6a55fd117052d6a40'),
    ('stockanalyzer/radar_worker.py', 'run_forever', '1d78ca2bf5681f7321e7fd492d6a594cb894c919431004cc32103d117252d1af'),
    ('stockanalyzer/radar_worker.py', 'process_analysis', '5fd77ed36f903a34387accfbbd8edf3171489d2f0ee4b31ddd59c5121848b2b2'),
    ('stockanalyzer/radar_worker.py', 'build_radar_plan', '29e2b2a05fef6ae8509d9cb4d6cdd7eb4201c363d34a3faabdb98b4bee8cf321'),
])
def test_original_trading_execution_is_preserved(path, name, expected):
    functions = {node.name: node for node in ast.parse(Path(path).read_text()).body
                 if isinstance(node, ast.FunctionDef)}
    node = functions[name]
    if name == '_portfolio_panel':
        node = _expand_portfolio_presentation(node, functions)
    fingerprint = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
    assert fingerprint == expected, f"Trading execution changed: {path}:{name}"

"""R5 regression: the TA plan output must stay byte-identical to the Phase 0
goldens. Today this pins the unmodified engine; once fusion lands it runs with
the valuation layer disabled / ``W_MAX = 0`` and must still match exactly."""
from __future__ import annotations

import json

import pytest

from tests.valuation import golden_ta as g

pytestmark = pytest.mark.skipif(not g.GOLDEN_PATH.exists(),
                                reason="TA goldens not captured")


def test_ta_output_matches_goldens():
    expected = json.loads(g.GOLDEN_PATH.read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(g.compute_goldens(), sort_keys=True,
                                   ensure_ascii=False))
    assert actual.keys() == expected.keys()
    diffs = [k for k in expected if actual[k] != expected[k]]
    assert not diffs, f"{len(diffs)} TA plans drifted, e.g. {diffs[:5]}"

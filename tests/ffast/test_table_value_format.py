"""Desktop table cells never print a non-zero value as zero (ADR 0055 rule 4).

`precision` stays decimal places; a value that would round to zero falls back
to three significant digits. The cases are shared with the browser's test
(tests/ffast/renderers/web/test_web_pure_helpers.py).
"""
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from UI.panels import format_table_value  # noqa: E402

CASES = json.loads((Path(__file__).parent / "table_value_cases.json").read_text())


@pytest.mark.parametrize("value, precision, want", CASES)
def test_table_value_format(value, precision, want):
    assert format_table_value(value, precision) == want

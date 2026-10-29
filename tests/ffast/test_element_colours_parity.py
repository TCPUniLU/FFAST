"""The web client's element colours are a copy of the 3D table; keep them in step."""
import re
from pathlib import Path

from ffast.chemistry import atomColors

JS = Path(__file__).resolve().parents[2] / "ffast" / "renderers" / "web" / "static" / "element_colours.js"


def test_the_web_element_colours_match_the_python_table():
    hexes = re.findall(r"'(#[0-9a-f]{6})'", JS.read_text())
    expected = ["#%02x%02x%02x" % tuple(int(v) for v in c) for c in atomColors]
    assert hexes == expected

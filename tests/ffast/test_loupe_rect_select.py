"""Rubber-band select obeys the armed-tool rule (GUI bug, 2026-08-07).

Reported: "atom selection without activating any of Pick option just makes atom
yellow and I am not able to deselect them".

Yellow is a server-owned Scientific Selection overlay, not the Loupe's own pick
highlight (that one is green) — so an unarmed gesture was committing a real
`SET_SELECTION`. The gesture was a Ctrl+drag: `on_mouse_release` sent the
rectangle to `Loupe.onAdapterPickRect` whenever a select tool was *not* armed,
which contradicted the rule the plain-click path states two lines earlier — atom
selection requires an intentionally-activated tool. And `onAdapterPickRect` only
ever appended, so nothing the mouse could do would clear the highlight. The
toggling sibling `onAdapterPick` had no caller at all.

The canvas is built with ``__new__`` so no GL context or QApplication is needed,
matching the Loupe harness in test_loupe_view_commands.py.
"""

import pytest


class _FakeAdapter:
    def __init__(self):
        self.rect_calls = 0

    def pick_in_rect(self, pos0, pos1):
        self.rect_calls += 1
        return [0, 1, 2]

    def pick_at(self, pos, radius=None):
        return 0                 # a hit on the first displayed atom

    def displayed_to_atom_id(self, i):
        return int(i) + 100      # a distinct id space, as under a filter


class _FakeLoupe:
    """Records anything the canvas tries to commit server-side."""
    def __init__(self):
        self.committed = []

    def _sendViewCommand(self, **fields):
        self.committed.append(fields)


class _FakeWidget:
    def __init__(self):
        self.sceneAdapter = _FakeAdapter()
        self.loupe = _FakeLoupe()
        self.selected = []
        self.rectangleHidden = 0
        self.colorbarDeselects = 0

    def addSelectedAtoms(self, atom_ids, refresh=False):
        self.selected.extend(atom_ids)

    def addSelectedAtom(self, atom_id, refresh=False):
        self.selected.append(atom_id)

    def hideSelectionRectangle(self):
        self.rectangleHidden += 1

    def _pickRadius(self):
        return 12

    def clearColorbarSelection(self):
        self.colorbarDeselects += 1


class _Event:
    button = 1
    pos = (60.0, 60.0)


def _canvas(*, tool_armed: bool):
    from UI.loupe.canvas import SceneCanvas
    c = SceneCanvas.__new__(SceneCanvas)
    c.widget = _FakeWidget()
    c.isCtrlDragging = True             # mid Ctrl+drag
    c.draggingStart = (10.0, 10.0)      # far enough to pass the 3px threshold
    c.mouseClickActive = tool_armed
    c.rectangleSelectActive = tool_armed
    return c


# ── no tool armed ───────────────────────────────────────────────────────────

def test_unarmed_rectangle_commits_no_selection():
    """The bug: this used to send SET_SELECTION and paint the atoms yellow."""
    c = _canvas(tool_armed=False)
    c.on_mouse_release(_Event())
    assert c.widget.loupe.committed == []


def test_unarmed_rectangle_selects_nothing_locally_either():
    c = _canvas(tool_armed=False)
    c.on_mouse_release(_Event())
    assert c.widget.selected == []


def test_unarmed_rectangle_does_not_even_ray_cast():
    """No selection means no hit-test — the picked set was the only consumer."""
    c = _canvas(tool_armed=False)
    c.on_mouse_release(_Event())
    assert c.widget.sceneAdapter.rect_calls == 0


def test_unarmed_rectangle_still_hides_the_rubber_band():
    """The band is drawn during the drag; not clearing it would leave it stuck."""
    c = _canvas(tool_armed=False)
    c.on_mouse_release(_Event())
    assert c.widget.rectangleHidden == 1


# ── tool armed: unchanged behaviour ─────────────────────────────────────────

def test_armed_rectangle_still_selects_into_the_tool():
    c = _canvas(tool_armed=True)
    c.on_mouse_release(_Event())
    assert c.widget.selected == [100, 101, 102]


def test_armed_rectangle_does_not_commit_a_server_selection():
    """Tool picks are client-side; only the select-indices field commits one."""
    c = _canvas(tool_armed=True)
    c.on_mouse_release(_Event())
    assert c.widget.loupe.committed == []


# ── a drag too short to be a rectangle ──────────────────────────────────────

def test_a_tiny_ctrl_drag_is_not_a_rectangle():
    c = _canvas(tool_armed=True)
    c.draggingStart = (59.0, 59.0)      # ~1.4px, under the threshold
    c.on_mouse_release(_Event())
    assert c.widget.selected == []
    assert c.widget.sceneAdapter.rect_calls == 0


# ── the removed handlers stay removed ───────────────────────────────────────

@pytest.mark.parametrize("name", ["onAdapterPick", "onAdapterPickRect"])
def test_the_adapter_pick_handlers_are_gone(name):
    """`onAdapterPick` never had a caller; `onAdapterPickRect` only had the
    unarmed branch above, and appended without ever removing. Re-adding either
    without a clearing gesture brings the stuck-yellow bug back.
    """
    from UI.loupe.window import Loupe
    assert not hasattr(Loupe, name)


def test_the_select_indices_field_can_still_clear_the_selection():
    """The remaining route in, and the one that unsticks a highlight: an empty
    list commits an empty selection, which the adapter draws as nothing.
    """
    from UI.loupe.window import Loupe
    obj = Loupe.__new__(Loupe)
    obj._pickedSet = [1, 2, 3]
    sent = []
    obj._sendViewCommand = lambda **fields: sent.append(fields)
    obj._parseIndexList = lambda text: []
    obj.settings = {"sceneSelectIndices": ""}

    Loupe.onApplySceneSelection(obj)

    assert obj._pickedSet == []
    assert sent == [{
        "type": "SET_SELECTION",
        "name": "picked",
        "scope": "current_structure",
        "indices": [],
    }]


# ── the rubber band only starts when it can select ──────────────────────────

class _PressEvent:
    button = 1
    pos = (40.0, 40.0)

    def __init__(self, ctrl=True):
        from vispy.util import keys
        self.modifiers = (keys.CONTROL,) if ctrl else ()


def _pressed(*, tool_armed, rect_capable=True, ctrl=True):
    from UI.loupe.canvas import SceneCanvas
    c = SceneCanvas.__new__(SceneCanvas)
    c.widget = _FakeWidget()
    c.isCtrlDragging = False
    c.draggingStart = (0.0, 0.0)
    c.mouseClickActive = tool_armed
    c.rectangleSelectActive = tool_armed and rect_capable
    c.on_mouse_press(_PressEvent(ctrl=ctrl))
    return c


def test_unarmed_ctrl_drag_does_not_start_a_rubber_band():
    """Drawing a box that selects nothing promises a selection it cannot make."""
    assert _pressed(tool_armed=False).isCtrlDragging is False


@pytest.mark.parametrize("tool_armed,ctrl", [(False, False), (True, False), (True, True)])
def test_any_press_on_the_canvas_deselects_the_colorbar(tool_armed, ctrl):
    """The colorbar's floating pieces are siblings of the canvas, so a click
    that misses them lands here — the only place that can clear their dashed
    highlight, whatever the click was otherwise doing."""
    assert _pressed(tool_armed=tool_armed, ctrl=ctrl).widget.colorbarDeselects == 1


def test_armed_rect_tool_starts_a_rubber_band():
    c = _pressed(tool_armed=True)
    assert c.isCtrlDragging is True
    assert c.draggingStart == (40.0, 40.0)


def test_a_tool_without_rectangle_select_starts_no_band():
    """`rectangleSelectActive` mirrors the tool's own `rectangleSelect` flag."""
    assert _pressed(tool_armed=True, rect_capable=False).isCtrlDragging is False


def test_ctrl_never_picks_a_single_atom():
    """Ctrl is the rubber-band modifier; it must not fall through to a pick."""
    c = _pressed(tool_armed=True, rect_capable=False)
    assert c.widget.selected == []


def test_a_plain_click_with_a_tool_armed_still_picks():
    """The unmodified click path is untouched."""
    c = _pressed(tool_armed=True, ctrl=False)
    assert c.isCtrlDragging is False
    assert c.widget.selected == [100]     # displayed 0 → atom id 100

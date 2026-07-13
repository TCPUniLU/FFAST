"""Sidebar regroup contract (ADR 0040).

The Loupe sidebar panes are grouped by concern (Playback / Appearance /
Analysis / View), VIEW SETTINGS is dissolved into DISPLAY + ALIGNMENT, and the
two "filter" concepts are renamed (Hide atoms vs Extract subset). These are
Qt-free checks pinning that the group map, the pick-tool paneNames, and the
dissolved schemas stay in sync — a rename in one place must not silently
orphan a pane or a tool handshake.
"""

# Every pane a loupe module registers via addSidebarPane, post-ADR-0040.
EXPECTED_PANES = {
    "INDEX / VIDEO",
    "DISPLAY", "BONDS", "UNIT CELL",
    "COLOR BY", "FORCE VECTORS", "EXTRACT SUBSET", "ALIGNMENT",
    "CAMERA", "EXPORT",
}


def _grouped_panes():
    from UI.loupe.window import Loupe
    return [n for _, names in Loupe.SIDEBAR_GROUPS for n in names]


def test_group_map_covers_every_pane_exactly_once():
    grouped = _grouped_panes()
    assert sorted(grouped) == sorted(EXPECTED_PANES)
    assert len(grouped) == len(set(grouped)), "a pane is listed in two groups"


def test_group_order_is_playback_appearance_analysis_view():
    from UI.loupe.window import Loupe
    assert [g for g, _ in Loupe.SIDEBAR_GROUPS] == [
        "PLAYBACK", "APPEARANCE", "ANALYSIS", "VIEW",
    ]


def test_coloring_and_force_vectors_are_analysis():
    """Coloring-by-metric and force-arrow overlays are analysis, not appearance."""
    from UI.loupe.window import Loupe
    groups = {g: names for g, names in Loupe.SIDEBAR_GROUPS}
    assert "COLOR BY" in groups["ANALYSIS"]
    assert "FORCE VECTORS" in groups["ANALYSIS"]
    assert "COLOR BY" not in groups["APPEARANCE"]
    assert "FORCE VECTORS" not in groups["APPEARANCE"]


def test_pick_tool_panenames_point_at_real_groups():
    """The ADR 0039 tool→pane handshake must target panes that still exist."""
    from modules.loupe.loupeAtomFilter import AtomFilterSelect
    from modules.loupe.loupeAtomAlign import AtomAlignSelect
    grouped = set(_grouped_panes())

    assert AtomFilterSelect.paneName == "EXTRACT SUBSET"
    assert AtomAlignSelect.paneName == "ALIGNMENT"
    assert AtomFilterSelect.paneName in grouped
    assert AtomAlignSelect.paneName in grouped


def test_view_settings_dissolved_with_renamed_filters():
    from modules.loupe.loupeViewSettings import SCHEMA_DISPLAY, SCHEMA_ALIGN

    # The two "filter" concepts are disambiguated by label.
    assert SCHEMA_DISPLAY["sceneFilterIndices"].label == "Hide atoms"
    assert SCHEMA_DISPLAY["sceneSelectIndices"].label == "Highlight atoms"

    # Alignment owns both strategies end to end.
    assert set(SCHEMA_ALIGN) == {
        "alignKabsch", "alignKabschHeavyOnly", "alignAtoms", "alignAtomsIndices",
    }

"""Tests for ffast.renderers.web.serve static file server and JS API contract."""
import os
import socket
import time
import urllib.request

import pytest

from ffast.renderers.web.serve import STATIC_DIR, start_static_server


class TestStaticDir:
    def test_static_dir_exists(self):
        assert os.path.isdir(STATIC_DIR)

    def test_index_html_exists(self):
        assert os.path.isfile(os.path.join(STATIC_DIR, "index.html"))

    def test_ffast_viewer_js_exists(self):
        assert os.path.isfile(os.path.join(STATIC_DIR, "ffast-viewer.js"))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestStartStaticServer:
    def test_serves_index_html(self):
        port = _free_port()
        httpd = start_static_server(port)
        try:
            time.sleep(0.05)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=3) as resp:
                assert resp.status == 200
                content = resp.read().decode()
                assert "FFAST" in content
        finally:
            httpd.shutdown()

    def test_returns_httpserver_instance(self):
        import http.server
        port = _free_port()
        httpd = start_static_server(port)
        try:
            assert isinstance(httpd, http.server.HTTPServer)
        finally:
            httpd.shutdown()

    def test_serves_js_file(self):
        port = _free_port()
        httpd = start_static_server(port)
        try:
            time.sleep(0.05)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/ffast-viewer.js", timeout=3) as resp:
                assert resp.status == 200
        finally:
            httpd.shutdown()


@pytest.fixture(scope="module")
def viewer_js_source():
    """Read ffast-viewer.js once for all contract tests."""
    path = os.path.join(STATIC_DIR, "ffast-viewer.js")
    with open(path) as f:
        return f.read()


class TestRendererAPIContract:
    """Verify ffast-viewer.js implements the same scene-adapter API as VispySceneAdapter.

    These are static contract tests — they parse the JS source for required identifiers
    rather than running it (no browser required).
    """

    # ── protocol ──────────────────────────────────────────────────────────────

    def test_hello_advertises_webgl_renderer(self, viewer_js_source):
        assert "renderer: 'webgl'" in viewer_js_source

    # ── public API surface ────────────────────────────────────────────────────

    def test_apply_scene_defined(self, viewer_js_source):
        assert "applyScene(" in viewer_js_source

    def test_apply_patch_defined(self, viewer_js_source):
        assert "applyPatch(" in viewer_js_source

    def test_clear_method_defined(self, viewer_js_source):
        assert "clear()" in viewer_js_source

    # ── per-component update methods ─────────────────────────────────────────

    def test_update_atoms_defined(self, viewer_js_source):
        assert "_updateAtoms(" in viewer_js_source

    def test_update_bonds_defined(self, viewer_js_source):
        assert "_updateBonds(" in viewer_js_source

    def test_update_forces_defined(self, viewer_js_source):
        assert "_updateForces(" in viewer_js_source

    def test_update_unit_cell_defined(self, viewer_js_source):
        assert "_updateUnitCell(" in viewer_js_source

    def test_update_labels_defined(self, viewer_js_source):
        assert "_updateLabels(" in viewer_js_source

    def test_update_selections_defined(self, viewer_js_source):
        assert "_updateSelections(" in viewer_js_source

    # ── applyScene handles all scene components ───────────────────────────────

    def test_apply_scene_handles_labels(self, viewer_js_source):
        # applyScene must call _updateLabels
        assert "_updateLabels(" in viewer_js_source
        # The call site is inside applyScene
        apply_scene_start = viewer_js_source.index("applyScene(scene)")
        apply_scene_end = viewer_js_source.index("applyPatch(patch,")
        apply_scene_body = viewer_js_source[apply_scene_start:apply_scene_end]
        assert "_updateLabels" in apply_scene_body

    def test_apply_scene_handles_selections(self, viewer_js_source):
        apply_scene_start = viewer_js_source.index("applyScene(scene)")
        apply_scene_end = viewer_js_source.index("applyPatch(patch,")
        apply_scene_body = viewer_js_source[apply_scene_start:apply_scene_end]
        assert "_updateSelections" in apply_scene_body

    # ── applyPatch handles all scene components ───────────────────────────────

    def test_apply_patch_handles_labels(self, viewer_js_source):
        patch_start = viewer_js_source.index("applyPatch(patch,")
        patch_end = viewer_js_source.index("_updateAtoms(atoms)")
        patch_body = viewer_js_source[patch_start:patch_end]
        assert "'labels'" in patch_body

    def test_apply_patch_handles_selections(self, viewer_js_source):
        patch_start = viewer_js_source.index("applyPatch(patch,")
        patch_end = viewer_js_source.index("_updateAtoms(atoms)")
        patch_body = viewer_js_source[patch_start:patch_end]
        assert "'selections'" in patch_body

    # ── state caching for selection overlays ─────────────────────────────────

    def test_atom_position_cache_field(self, viewer_js_source):
        assert "_cachedAtomPositions" in viewer_js_source

    def test_atom_size_cache_field(self, viewer_js_source):
        assert "_cachedAtomSizes" in viewer_js_source

    def test_selection_meshes_map_field(self, viewer_js_source):
        assert "_selectionMeshes" in viewer_js_source

    def test_label_sprites_list_field(self, viewer_js_source):
        assert "_labelSprites" in viewer_js_source

    # ── clear methods ─────────────────────────────────────────────────────────

    def test_clear_labels_method(self, viewer_js_source):
        assert "_clearLabels()" in viewer_js_source

    def test_clear_selections_method(self, viewer_js_source):
        assert "_clearSelections()" in viewer_js_source

    def test_clear_calls_all_components(self, viewer_js_source):
        clear_start = viewer_js_source.index("clear() {")
        # Find the closing brace (next method after clear)
        clear_end = viewer_js_source.index("_updateLabels(labels)", clear_start)
        clear_body = viewer_js_source[clear_start:clear_end]
        assert "_clearAtoms()" in clear_body
        assert "_clearBonds()" in clear_body
        assert "_clearForces()" in clear_body
        assert "_clearUnitCell()" in clear_body
        assert "_clearLabels()" in clear_body
        assert "_clearSelections()" in clear_body

    # ── _updateAtoms caches atom positions ────────────────────────────────────

    def test_update_atoms_caches_positions(self, viewer_js_source):
        update_atoms_start = viewer_js_source.index("_updateAtoms(atoms)")
        update_atoms_end = viewer_js_source.index("_updateBonds(bonds)")
        update_atoms_body = viewer_js_source[update_atoms_start:update_atoms_end]
        assert "_cachedAtomPositions" in update_atoms_body
        assert "_cachedAtomSizes" in update_atoms_body

    # ── _updateSelections skips without positions ─────────────────────────────

    def test_update_selections_guards_on_cached_positions(self, viewer_js_source):
        # The guard must appear near the start of the _updateSelections method body.
        sel_def_start = viewer_js_source.index("_updateSelections(selections) {")
        sel_preamble = viewer_js_source[sel_def_start:sel_def_start + 300]
        assert "_cachedAtomPositions" in sel_preamble

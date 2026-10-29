"""Arrow tessellation, now owned by its only consumer (ADR 0052).

These tests moved from ``tests/ffast/visualization/test_builtin_force_stages.py``.
ADR 0049 deleted the ``ffast.force_arrows`` stage but left ``_arrow_mesh`` behind
in the stage package, where the Vispy adapter reached in for it by private
import — so a package the review called "renderer-neutral" was the home of a
private function only one renderer could use. The mesh builder now lives beside
that renderer and is public.

The ``arrow_index`` return is new: it maps each vertex back to the *original*
arrow it belongs to, which is what lets the adapter colour arrows from
``ForceScene.colors`` instead of re-hardcoding the core's RGBA literal.
"""

import numpy as np

from ffast.renderers.vispy.arrow_mesh import arrow_mesh


# ── mesh geometry ───────────────────────────────────────────────────────────

def test_arrow_mesh_returns_arrays():
    verts, faces, _ = arrow_mesh(np.array([[0.0, 0, 0]]), np.array([[1.0, 0, 0]]))
    assert verts is not None
    assert faces is not None
    assert verts.ndim == 2 and verts.shape[1] == 3
    assert faces.ndim == 2 and faces.shape[1] == 3


def test_arrow_mesh_zero_length_returns_none():
    starts = np.array([[0.0, 0, 0], [1.0, 0, 0]])
    verts, faces, index = arrow_mesh(starts, starts.copy())
    assert verts is None
    assert faces is None
    assert index is None


def test_arrow_mesh_partial_zero_skipped():
    starts = np.array([[0.0, 0, 0], [0.0, 0, 0]])
    ends = np.array([[1.0, 0, 0], [0.0, 0, 0]])   # only the first is non-zero
    verts, _, _ = arrow_mesh(starts, ends)
    assert verts is not None   # one arrow survives


def test_arrow_mesh_vertex_count_scales_with_n():
    v1, f1, _ = arrow_mesh(np.array([[0.0, 0, 0]]), np.array([[1.0, 0, 0]]))
    v2, f2, _ = arrow_mesh(
        np.array([[0.0, 0, 0], [2.0, 0, 0]]),
        np.array([[1.0, 0, 0], [3.0, 0, 0]]),
    )
    assert v2.shape[0] == 2 * v1.shape[0]
    assert f2.shape[0] == 2 * f1.shape[0]


def test_arrow_mesh_faces_are_valid_indices():
    verts, faces, _ = arrow_mesh(
        np.array([[0.0, 0, 0], [0.0, 1, 0]]),
        np.array([[1.0, 0, 0], [0.0, 2, 0]]),
    )
    assert np.all(faces >= 0)
    assert np.all(faces < len(verts))


def test_arrow_mesh_along_all_axes():
    for axis in range(3):
        ends = np.zeros((1, 3))
        ends[0, axis] = 1.0
        verts, _, _ = arrow_mesh(np.zeros((1, 3)), ends)
        assert verts is not None


def test_arrow_mesh_antiparallel_z():
    verts, _, _ = arrow_mesh(np.array([[0.0, 0, 0]]), np.array([[0.0, 0, -1.0]]))
    assert verts is not None


# ── arrow_index (vertex → original arrow) ───────────────────────────────────

def test_arrow_index_covers_every_vertex():
    verts, _, index = arrow_mesh(
        np.array([[0.0, 0, 0], [2.0, 0, 0]]),
        np.array([[1.0, 0, 0], [3.0, 0, 0]]),
    )
    assert index.shape == (len(verts),)


def test_arrow_index_names_both_arrows_evenly():
    _, _, index = arrow_mesh(
        np.array([[0.0, 0, 0], [2.0, 0, 0]]),
        np.array([[1.0, 0, 0], [3.0, 0, 0]]),
    )
    counts = np.bincount(index, minlength=2)
    assert counts[0] == counts[1]   # identical tessellation per arrow


def test_arrow_index_uses_original_indices_when_an_arrow_is_dropped():
    """A zero-length arrow is not tessellated, but the colour array still has
    an entry for it — so the index must be the caller's index, not the
    post-filter one, or every arrow after a dropped one is mis-coloured.
    """
    starts = np.array([[0.0, 0, 0], [0.0, 0, 0], [0.0, 0, 0]])
    ends = np.array([[0.0, 0, 0], [1.0, 0, 0], [0.0, 2.0, 0]])   # arrow 0 dropped
    _, _, index = arrow_mesh(starts, ends)
    assert set(index.tolist()) == {1, 2}


def test_arrow_index_is_integer_typed():
    """It is used as a fancy index into the colour array."""
    _, _, index = arrow_mesh(np.array([[0.0, 0, 0]]), np.array([[1.0, 0, 0]]))
    assert np.issubdtype(index.dtype, np.integer)

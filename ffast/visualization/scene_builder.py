"""Build a renderer-neutral RenderScene from VisualizationState + dataset access.

Scene construction runs through the Stage Catalog: ``build_scene`` populates an
external-namespace context from the dataset/view state and drives the registered
pipeline stages via ``pipeline.execute`` (which resolves dependency order through
``StageRegistry.resolve_order``). View parameters flow into the stages, so a
``SET_PARAMETER`` command changes the produced scene.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np

from ffast.visualization.models import VisualizationState
from ffast.visualization.scene import (
    AtomColorBy,
    AtomScene,
    BondScene,
    ForceScene,
    LabelScene,
    RenderScene,
    ScenePatch,
    SelectionOverlay,
    UnitCellScene,
)

logger = logging.getLogger(__name__)

_ATOM_POSITIONS = "stage.ffast.atom_positions.positions"
_ATOM_SIZES = "stage.ffast.atom_sizes.sizes"
_ATOM_COLORS = "stage.ffast.atom_colors.colors"


def build_scene(
    state: VisualizationState,
    get_dataset: Callable,
    get_prediction: Callable | None = None,
    get_forces: Callable | None = None,
    executor: Any | None = None,
    _legacy_forces: bool = False,
) -> RenderScene:
    """Derive a RenderScene from VisualizationState.

    ``executor`` is the injected ``MetricExecutor`` (ADR 0046) threaded to
    value-driven atom coloring — the caller's ``DataService.metricExecutor``,
    so coloring and ``REQUEST_METRIC`` share the same server-side executor
    instance instead of each maintaining its own.

    Returns a scene with only camera populated when no dataset is loaded or
    when any data access fails.
    """
    # Ensure the built-in stages are registered before resolving the pipeline.
    import ffast.visualization.stages.builtin  # noqa: F401
    from ffast.visualization.pipeline import execute
    from ffast.visualization.stages.registry import _default_registry

    scene = RenderScene(
        view_id=state.view_id,
        version=state.version,
        camera=state.camera,
    )

    if state.dataset_ref is None:
        return scene

    ds = get_dataset(state.dataset_ref)
    if ds is None:
        return scene

    n = ds.getN()
    idx = min(state.structure_index, n - 1)

    # Raw (untransformed) frame positions — atom_positions applies view transforms.
    try:
        raw_positions = np.asarray(ds.getCoordinates(idx), dtype=np.float64)
    except Exception as exc:
        logger.warning("scene_builder: getCoordinates(%d) failed: %s", idx, exc)
        return scene

    # Elements
    try:
        is_variable = getattr(ds, "isVariable", False)
        if is_variable:
            z = np.asarray(ds.getElements(idx), dtype=np.int64)
        else:
            z = np.asarray(ds.getElements(), dtype=np.int64)
    except Exception as exc:
        logger.warning("scene_builder: getElements failed: %s", exc)
        return scene

    # View transforms (ADR 0014, gate item 2): transforms are computed AND
    # applied server-side. Explicit state.transforms plus any feature-driven
    # transform stages (e.g. Kabsch alignment) compose here; the client sends
    # only feature toggles, never transform matrices.
    transforms = list(state.transforms)
    if "kabsch_align" in state.enabled_features:
        transforms = _kabsch_to_frame0(ds, raw_positions, z, state) + transforms
    if "atom_align" in state.enabled_features:
        transforms = transforms + _atom_align_to_reference(ds, raw_positions, state)

    # External-namespace inputs the pipeline stages consume.
    context = {
        "frame.positions": raw_positions,
        "frame.elements": z,
        "view.transforms": transforms,
    }

    atom_targets = ["ffast.atom_positions", "ffast.atom_sizes", "ffast.atom_colors"]
    if "labels" in state.enabled_features:
        atom_targets.append("ffast.atom_labels")

    try:
        results = execute(_default_registry, atom_targets, context, parameters=state.parameters)
        positions = np.asarray(results[_ATOM_POSITIONS], dtype=np.float64)
        sizes = np.asarray(results[_ATOM_SIZES]).tolist()
        colors = np.asarray(results[_ATOM_COLORS]).tolist()
    except Exception as exc:
        # config/atoms (element radii/colors) may be unavailable; positions still
        # render with neutral styling and downstream stages are skipped.
        logger.warning("scene_builder: atom pipeline failed: %s", exc)
        results = {}
        positions = _apply_transforms(raw_positions, transforms)
        n_atoms = len(positions)
        sizes = [0.5] * n_atoms
        colors = [[0.7, 0.7, 0.7, 1.0]] * n_atoms

    # Value-driven atom coloring (ADR 0016): compute per-atom values on the FULL
    # set and resolve the range here, so the colorbar/scale stay stable across
    # frames and filtering. The client maps these values to colors.
    color_values = None
    color_meta = None
    from ffast.visualization.color_values import resolve_atom_color_values
    _color_src = resolve_atom_color_values(state, ds, idx, raw_positions, z, get_prediction, executor=executor)
    if _color_src is not None:
        color_values, _clabel, _cunit = _color_src
        cparams = state.parameters.get("ffast.atom_color", {})
        finite = color_values[np.isfinite(color_values)]
        auto_lo = float(np.min(finite)) if finite.size else 0.0
        auto_hi = float(np.max(finite)) if finite.size else 1.0
        color_meta = {
            "colormap": cparams.get("colormap", "viridis"),
            "vmin": float(cparams.get("vmin", auto_lo)),
            "vmax": float(cparams.get("vmax", auto_hi)),
            "label": _clabel,
            "unit": _cunit,
        }

    # Atom filter (ADR 0014, gate item 3): the ffast.atom_filter stage builds a
    # keep-mask from explicit indices; scene assembly applies it across atoms,
    # bonds, labels, and forces. ``keep`` stays None (no filtering) for an empty
    # index list. ``positions_all`` retains every atom for the unit-cell origin.
    positions_all = positions
    keep = None
    old_to_new = None
    filter_params = state.parameters.get("ffast.atom_filter", {})
    filter_indices = _resolve_filter_indices(filter_params.get("indices") or [], z)
    if filter_indices:
        try:
            from ffast.visualization.stages.builtin.selection_stages import atom_filter
            keep = np.asarray(
                atom_filter(
                    positions_all,
                    filter_indices,
                    invert=bool(filter_params.get("invert", False)),
                ),
                dtype=bool,
            )
            old_to_new = -np.ones(len(keep), dtype=int)
            old_to_new[keep] = np.arange(int(keep.sum()))
            positions = positions_all[keep]
            sizes = [s for s, k in zip(sizes, keep) if k]
            colors = [c for c, k in zip(colors, keep) if k]
            if color_values is not None:
                color_values = color_values[keep]
        except Exception as exc:
            logger.warning("scene_builder: atom filter failed: %s", exc)
            keep = None

    # atom_ids (ADR 0015): map each displayed atom to its scientific index so a
    # picked atom resolves to the right server-side index under filtering.
    atom_ids = np.where(keep)[0].tolist() if keep is not None else None

    color_by = None
    if color_values is not None and color_meta is not None:
        color_by = AtomColorBy(values=color_values.tolist(), **color_meta)

    scene.atoms = AtomScene(
        positions=positions.tolist(),
        sizes=sizes,
        colors=colors,
        atom_ids=atom_ids,
        color_by=color_by,
    )

    # Labels (pipeline output; depends on atom_positions via resolve_order)
    if _label_outputs_present(results):
        label_positions = np.asarray(results["stage.ffast.atom_labels.positions"], dtype=np.float64)
        texts = list(results["stage.ffast.atom_labels.texts"])
        if keep is not None:
            # Keep original-index text ("3","5",…) at the surviving atoms.
            label_positions = label_positions[keep]
            texts = [t for t, k in zip(texts, keep) if k]
        # Black for parity with the legacy loupeIndices text on the light
        # loupe background. Label color/size should become a presentation
        # parameter (see ADR 0014 labels parity follow-up).
        scene.labels = LabelScene(
            positions=label_positions.tolist(),
            texts=texts,
            colors=[[0.0, 0.0, 0.0, 1.0]] * len(texts),
        )

    # Bonds. Topology is dynamic (distance-based) unless the view selects an
    # explicit Fixed bond set via the ffast.bonds parameter (loupeBonds "Fixed"
    # mode). An empty or absent Fixed set falls back to dynamic bonds so the
    # default view is never left without bonds.
    try:
        bond_params = state.parameters.get("ffast.bonds", {})
        fixed_idx = (
            bond_params.get("fixed_indices")
            if bond_params.get("bond_type") == "Fixed"
            else None
        )
        if fixed_idx:
            bond_idx = np.asarray(fixed_idx, dtype=int).reshape(-1, 2)
        else:
            bond_idx = ds.getBondIndices(idx)
        if bond_idx is not None and len(bond_idx) > 0:
            bond_idx = np.asarray(bond_idx)
            if keep is not None:
                # Drop bonds touching a filtered-out atom; remap the rest into
                # the compact (post-filter) index space of ``positions``.
                both = keep[bond_idx[:, 0]] & keep[bond_idx[:, 1]]
                bond_idx = old_to_new[bond_idx[both]]
            if len(bond_idx) > 0:
                # Interleave endpoints: (2M, 3) array
                starts = positions[bond_idx[:, 0]]
                ends = positions[bond_idx[:, 1]]
                segments = np.empty((2 * len(bond_idx), 3), dtype=np.float64)
                segments[0::2] = starts
                segments[1::2] = ends
                scene.bonds = BondScene(segments=segments.tolist())
    except Exception as exc:
        logger.debug("scene_builder: bonds failed: %s", exc)

    # Force vectors are an explicit feature. ``prediction_ref`` is also used by
    # metric coloring, so it must not create arrows by itself.
    forces_enabled = "forces" in state.enabled_features or _legacy_forces
    if forces_enabled:
        try:
            raw_forces = None
            force_params = state.parameters.get("ffast.force_arrows", {})
            # Per-stage prediction_ref overrides the global state.prediction_ref so
            # force arrows can show a different model than atom coloring (Option B).
            # Key absent → fall back to global ref. Key present but None → ground truth.
            if "prediction_ref" in force_params:
                force_pred_ref = force_params["prediction_ref"]
            else:
                force_pred_ref = state.prediction_ref

            if force_pred_ref is None:
                if get_forces is not None:
                    raw_forces = get_forces(state.dataset_ref, idx)
            elif get_prediction is not None:
                pred = get_prediction(state.dataset_ref, force_pred_ref)
                if pred is not None:
                    raw_forces = pred.forces[idx]

            if raw_forces is not None:
                scene.forces = _build_force_scene(
                    positions=positions,
                    forces=np.asarray(raw_forces, dtype=np.float64),
                    keep=keep,
                    old_to_new=old_to_new,
                    transforms=transforms,
                    params=force_params,
                )
        except Exception as exc:
            logger.debug("scene_builder: force vectors failed: %s", exc)

    # Unit cell (pipeline stage; origin derived from transformed positions).
    # Shown by default when lattice data is available; the client opts out by
    # adding "no_unit_cell" to enabled_features (TOGGLE_FEATURE from Loupe).
    if "no_unit_cell" not in state.enabled_features:
        try:
            if hasattr(ds, "getLattice"):
                lattice_raw = ds.getLattice(idx)
                if lattice_raw is not None:
                    latt_arr = (
                        lattice_raw.array
                        if hasattr(lattice_raw, "array")
                        else np.asarray(lattice_raw, dtype=np.float64)
                    )
                    # Full (unfiltered) centroid so an atom filter doesn't shift the cell.
                    origin = np.mean(positions_all, axis=0) - np.sum(latt_arr, axis=0) / 2
                    cell_ctx = {"frame.lattice": latt_arr, "view.cell_origin": origin}
                    cell_results = execute(_default_registry, ["ffast.unit_cell_edges"], cell_ctx)
                    edges = np.asarray(cell_results["stage.ffast.unit_cell_edges.segments"])
                    scene.unit_cell = UnitCellScene(segments=edges.tolist())
        except Exception as exc:
            logger.debug("scene_builder: unit_cell failed: %s", exc)

    # Selection overlays (ADR 0014): one SelectionOverlay per named scientific
    # selection. Indices are remapped into the filtered atom space when a filter
    # is active (atoms filtered out are dropped from the overlay).
    overlays = []
    for sel in state.selections.values():
        idxs = list(sel.indices)
        if keep is not None:
            idxs = [
                int(old_to_new[i])
                for i in idxs
                if 0 <= i < len(keep) and keep[i]
            ]
        overlays.append(
            SelectionOverlay(
                name=sel.name,
                atom_indices=idxs,
                color=[1.0, 1.0, 0.0, 1.0],
            )
        )
    scene.selections = overlays

    return scene


def _label_outputs_present(results: dict) -> bool:
    return (
        "stage.ffast.atom_labels.positions" in results
        and "stage.ffast.atom_labels.texts" in results
    )


def fill_patch_from_scene(patch: ScenePatch, scene: RenderScene) -> None:
    """Populate changed scene fields in-place from a freshly built scene."""
    if "atoms" in patch.changed:
        patch.atoms = scene.atoms
    if "bonds" in patch.changed:
        patch.bonds = scene.bonds
    if "forces" in patch.changed:
        patch.forces = scene.forces
    if "labels" in patch.changed:
        patch.labels = scene.labels
    if "unit_cell" in patch.changed:
        patch.unit_cell = scene.unit_cell
    if "selections" in patch.changed:
        patch.selections = scene.selections
    if "camera" in patch.changed:
        patch.camera = scene.camera


def _build_force_scene(
    *,
    positions: np.ndarray,
    forces: np.ndarray,
    keep: np.ndarray | None,
    old_to_new: np.ndarray | None,
    transforms: list,
    params: dict,
) -> ForceScene | None:
    if keep is not None:
        forces = forces[keep]

    force_positions = positions
    if params.get("filter_enabled"):
        scientific_indices = [int(i) for i in params.get("atom_indices", [])]
        if not scientific_indices:
            return None
        # atom_indices are scientific (original frame) indices; positions is
        # post-filter compact. Remap via old_to_new when a filter is active.
        if old_to_new is not None:
            compact = [
                int(old_to_new[i])
                for i in scientific_indices
                if 0 <= i < len(old_to_new) and old_to_new[i] >= 0
            ]
        else:
            compact = [i for i in scientific_indices if 0 <= i < len(force_positions)]
        if not compact:
            return None
        idx_arr = np.asarray(compact, dtype=int)
        force_positions = force_positions[idx_arr]
        forces = forces[idx_arr]

    if len(forces) == 0:
        return None

    # Forces are free vectors: apply rotations, not translations.
    for transform in transforms:
        transform = np.asarray(transform)
        if transform.ndim == 2:
            forces = forces @ transform

    if params:
        length = float(params.get("length_factor", 10))
        normalised = bool(params.get("normalised", True))
        norms = np.linalg.norm(forces, axis=1)
        max_norm = float(norms.max()) if len(norms) else 0.0
        if normalised and max_norm > 1e-10:
            forces = forces / max_norm * length / 5
        else:
            forces = forces * length / 500

    colors = [[0.9, 0.4, 0.1, 0.8]] * len(force_positions)
    return ForceScene(
        starts=force_positions.tolist(),
        vectors=forces.tolist(),
        colors=colors,
    )


def _kabsch_to_frame0(ds, raw_positions: np.ndarray, z: np.ndarray, state) -> list:
    """Server-side Kabsch alignment of the current frame onto frame 0.

    ADR 0014 / gate item 2. Computes the rigid transform via the
    ``ffast.kabsch_alignment`` stage and returns it as a list to prepend to
    ``view.transforms``. The ``heavy_only`` compute parameter is read from the
    view's stored ``ffast.kabsch_alignment`` parameters (set by the client's
    "Kabsch: heavy atoms only" checkbox); it defaults to the stage default when
    absent. Returns ``[]`` on atom-count mismatch (variable datasets) or
    failure, so an unalignable frame renders untransformed.
    """
    try:
        from ffast.visualization.stages.builtin.transform_stages import (
            kabsch_alignment,
        )
        params = state.parameters.get("ffast.kabsch_alignment", {})
        heavy_only = bool(params.get("heavy_only", True))
        ref = np.asarray(ds.getCoordinates(0), dtype=np.float64)
        if ref.shape != raw_positions.shape:
            return []
        return list(kabsch_alignment(raw_positions, ref, z, heavy_only=heavy_only))
    except Exception as exc:
        logger.warning("scene_builder: kabsch align failed: %s", exc)
        return []


def _atom_align_to_reference(ds, raw_positions: np.ndarray, state) -> list:
    """Server-side 3-atom frame alignment (ADR 0014 gate item 2, ffast.atom_align).

    Computes translate+rotate transforms via the ``atom_align`` stage so the
    three selected atoms overlap a reference frame, and returns them to append
    to ``view.transforms``. Returns ``[]`` on an invalid selection (not exactly
    three indices), atom-count mismatch (variable datasets), or failure.
    """
    try:
        from ffast.visualization.stages.builtin.transform_stages import atom_align
        params = state.parameters.get("ffast.atom_align", {})
        atom_indices = params.get("atom_indices", [])
        ref_frame = int(params.get("reference_frame", 0))
        if len(atom_indices) != 3:
            return []
        ref = np.asarray(ds.getCoordinates(ref_frame), dtype=np.float64)
        if ref.shape != raw_positions.shape:
            return []
        return list(
            atom_align(
                raw_positions, ref,
                atom_indices=atom_indices, reference_frame=ref_frame,
            )
        )
    except Exception as exc:
        logger.warning("scene_builder: atom_align failed: %s", exc)
        return []


def _resolve_filter_indices(raw, z) -> list:
    """Resolve a mixed atom-filter spec to concrete indices (ADR 0014 gate 3).

    Tokens may be integer indices or element symbols ("C"); a "-" prefix
    ("-H", "-3") excludes. Includes minus excludes; when only excludes are
    given, every atom except the excluded ones is kept. Element symbols are
    matched against ``z`` (the *current frame's* atomic numbers), so filtering
    is correct for variable datasets whose composition changes per frame.
    """
    if not raw:
        return []
    z = np.asarray(z).ravel()
    n = len(z)
    include: list = []
    exclude: list = []
    names = None

    def _element_indices(symbol):
        nonlocal names
        if names is None:
            try:
                from ffast.chemistry import zIntToZStr  # type: ignore[import]
                names = [zIntToZStr.get(int(zi), str(int(zi))) for zi in z]
            except Exception:
                names = [str(int(zi)) for zi in z]
        return [i for i, nm in enumerate(names) if nm == symbol]

    for tok in raw:
        if isinstance(tok, bool):
            continue
        if isinstance(tok, (int, np.integer)):
            include.append(int(tok))
            continue
        s = str(tok).strip()
        if not s:
            continue
        neg = s.startswith("-")
        if neg:
            s = s[1:].strip()
        try:
            idx = int(s)
            (exclude if neg else include).append(idx)
            continue
        except ValueError:
            pass
        (exclude if neg else include).extend(_element_indices(s))

    if not include and exclude:
        return sorted(set(range(n)) - set(exclude))
    return sorted(set(include) - set(exclude))


def _apply_transforms(positions: np.ndarray, transforms: list) -> np.ndarray:
    R = positions.copy()
    for t in (transforms or []):
        t = np.asarray(t)
        if t.ndim == 1:
            R = R + t
        elif t.ndim == 2:
            R = R @ t
    return R

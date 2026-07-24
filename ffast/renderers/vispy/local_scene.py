from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np

from ffast.visualization.models import ScientificSelection, SelectionScope, VisualizationState
from ffast.visualization.scene import SceneSnapshot
from ffast.visualization.scene_builder import build_scene

logger = logging.getLogger(__name__)


class _PredictionView:
    __slots__ = ("forces",)

    def __init__(self, forces) -> None:
        self.forces = forces


def _entry_get(entry: Any, key: str, default: Any = None) -> Any:
    if entry is None:
        return default
    getter = getattr(entry, "get", None)
    if callable(getter):
        # Cache entries are DataEntity, whose ``get(key=None)`` takes a single
        # argument and returns None for a missing key — NOT the dict
        # ``get(key, default)`` two-arg form. Call with one arg (works for dicts
        # too) and apply the default ourselves. (Previously this passed two args
        # and raised TypeError, so available_prediction_refs found no
        # predictions and the Prediction selector stayed empty.)
        try:
            value = getter(key)
        except TypeError:
            value = getter(key, default)
        return default if value is None else value
    return getattr(entry, key, default)


def _data_service(env: Any) -> Any:
    """The DataService that owns the cache and its key helpers.

    After the Environment decomposition the cache lives on ``env.data``
    (DataService), not on ``env`` itself, and the old facade methods on ``env``
    were deleted. Resolve to ``env.data`` when present, falling back to ``env``
    for a flat/legacy object that exposes the helpers directly. (The previous
    ``hasattr(env, "getCacheByKey")`` guards checked the deleted facade names, so
    they were always False — every prediction lookup silently returned None and
    the Loupe Prediction selector stayed empty.)
    """
    return getattr(env, "data", None) or env


def _cache_entry(env: Any, key: str | None) -> Any:
    if not key:
        return None
    svc = _data_service(env)
    if hasattr(svc, "getCacheByKey"):
        return svc.getCacheByKey(key, subChecks=False)
    return getattr(svc, "cache", {}).get(key)


def _direct_forces_cache(env: Any, dataset_ref: str, model_ref: str, dataset=None, model=None):
    keys: list[str] = []
    svc = _data_service(env)
    if hasattr(svc, "getCacheKey"):
        try:
            key = svc.getCacheKey(
                "forces",
                model=model if model is not None else model_ref,
                dataset=dataset if dataset is not None else dataset_ref,
            )
            if key:
                keys.append(key)
        except Exception as exc:
            logger.debug("local_scene: direct forces getCacheKey failed: %s", exc)
    keys.append(f"forces__{model_ref}__{dataset_ref}")

    seen = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        forces = _entry_get(_cache_entry(env, key), "forces")
        if forces is not None:
            return forces
    return None


def _force_difference_cache(env: Any, dataset_ref: str, model_ref: str, dataset=None, model=None):
    keys: list[str] = []
    svc = _data_service(env)
    if hasattr(svc, "make_metric_cache_key") and dataset is not None and model is not None:
        try:
            key = svc.make_metric_cache_key("ffast.force_difference", {}, model, dataset)
            if key:
                keys.append(key)
        except Exception as exc:
            logger.debug("local_scene: metric force-difference key failed: %s", exc)
    keys.extend([
        f"ffast.force_difference__{model_ref}__{dataset_ref}",
        f"ffast.force_difference__nil__{model_ref}__{dataset_ref}",
    ])

    seen = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        entry = _cache_entry(env, key)
        if entry is not None:
            return entry
    return None


def _prediction_forces_from_difference(dataset: Any, diff_entry: Any):
    diff = _entry_get(diff_entry, "values")
    if diff is None:
        return None
    diff = np.asarray(diff, dtype=np.float64)
    ref_forces = dataset.getForces()
    if getattr(dataset, "isVariable", False) and isinstance(ref_forces, list):
        ref_flat = np.concatenate(ref_forces, axis=0)
        pred_flat = ref_flat + diff
        offsets = np.array([0] + [f.shape[0] for f in ref_forces]).cumsum()
        return [pred_flat[offsets[i]:offsets[i + 1]] for i in range(len(ref_forces))]

    ref = np.asarray(ref_forces, dtype=np.float64)
    if diff.shape == ref.shape:
        return ref + diff
    n_frames, n_atoms, _ = ref.shape
    pred = ref.reshape(n_frames * n_atoms, 3) + diff
    return pred.reshape(n_frames, n_atoms, 3)


def _force_frame(forces: Any, idx: int):
    if isinstance(forces, list):
        return forces[idx]
    return np.asarray(forces, dtype=np.float64)[idx]


def build_loupe_scene_snapshot(
    *,
    view_id: str,
    dataset_ref: str | None,
    structure_index: int,
    get_dataset: Callable[[str], Any],
    settings: Any = None,
    prediction_ref: str | None = None,
    get_prediction: Callable[[str | None, str | None], Any] | None = None,
    get_forces: Callable[[str, int], Any] | None = None,
    picked_indices: list[int] | None = None,
    version: int = 0,
    executor: Any | None = None,
) -> SceneSnapshot:
    """Build the scene-adapter snapshot Loupe needs when no server is attached."""
    state = VisualizationState(
        view_id=view_id,
        version=version,
        dataset_ref=dataset_ref,
        prediction_ref=prediction_ref,
        structure_index=int(structure_index or 0),
    )

    if _setting(settings, "alignKabsch", False):
        state.enabled_features.append("kabsch_align")
    if _setting(settings, "showSceneLabels", False):
        state.enabled_features.append("labels")

    if _setting(settings, "alignAtoms", False):
        raw_indices = _setting(settings, "alignAtomsIndices", "")
        if isinstance(raw_indices, str):
            align_indices = parse_index_list(raw_indices)
        else:
            align_indices = list(raw_indices) if raw_indices else []
        align_ref = int(_setting(settings, "alignAtomsConfIndex", 0) or 0)
        if len(align_indices) == 3:
            state.enabled_features.append("atom_align")
            state.parameters.setdefault("ffast.atom_align", {})["atom_indices"] = align_indices
            state.parameters.setdefault("ffast.atom_align", {})["reference_frame"] = align_ref

    if _setting(settings, "showForceVectors", False):
        state.enabled_features.append("forces")
        # Same stage key and field names the server sends (Loupe.onApplyForceVectors),
        # so build_scene's force branch honors length/normalise/filter identically.
        # prediction_ref is pinned to None: the local path bakes the chosen model
        # into get_forces, so arrows always resolve through that ground-truth route.
        state.parameters["ffast.force_arrows"] = {
            "prediction_ref": None,
            "length_factor": int(_setting(settings, "forceVectorsLength", 10) or 10),
            "normalised": bool(_setting(settings, "forceVectorsNormalised", True)),
            "filter_enabled": bool(_setting(settings, "forceVectorsFilterEnabled", False)),
            "atom_indices": list(_setting(settings, "forceVectorsAtomIndices", []) or []),
        }

    if not _setting(settings, "showUnitCell", True):
        state.enabled_features.append("no_unit_cell")

    if _setting(settings, "bondType", "Dynamic") == "Fixed":
        raw = _setting(settings, "fixedBondIndices", None)
        if raw is not None:
            state.parameters["ffast.bonds"] = {
                "bond_type": "Fixed",
                "fixed_indices": [list(p) for p in raw],
            }

    filter_indices = parse_filter_tokens(_setting(settings, "sceneFilterIndices", ""))
    if filter_indices:
        state.parameters.setdefault("ffast.atom_filter", {})["indices"] = filter_indices

    color_source = _setting(settings, "atomColorSource", "element")
    color_map = _setting(settings, "atomColorMap", "viridis")
    state.parameters["ffast.atom_color"] = {
        "source": color_source or "element",
        "colormap": color_map or "viridis",
    }

    highlight = parse_index_list(_setting(settings, "sceneSelectIndices", ""))
    if highlight:
        state.selections["highlight"] = ScientificSelection(
            name="highlight",
            scope=SelectionScope.CURRENT_STRUCTURE,
            indices=highlight,
        )
    if picked_indices:
        state.selections["picked"] = ScientificSelection(
            name="picked",
            scope=SelectionScope.CURRENT_STRUCTURE,
            indices=list(picked_indices),
        )

    scene = build_scene(
        state, get_dataset, get_prediction, get_forces=get_forces,
        executor=executor, _legacy_forces=False,
    )
    return SceneSnapshot(scene=scene)


def make_cache_prediction_resolver(env: Any):
    """Resolve predicted forces from the DataType cache for Loupe scene building.

    Returns a _PredictionView whose .forces supports [frame_idx] indexing:
    - uniform datasets: shape (N_frames, N_atoms, 3)
    - variable datasets: list of per-frame (N_atoms_i, 3) arrays
    """
    def get_prediction(dataset_fp, model_fp):
        if not dataset_fp or not model_fp:
            return None
        try:
            dataset = env.datasets.get(dataset_fp) if hasattr(env, "datasets") else None
            model = env.models.get(model_fp) if hasattr(env, "models") else None
            forces = _direct_forces_cache(env, dataset_fp, model_fp, dataset, model)
            if forces is None and dataset is not None:
                forces = _prediction_forces_from_difference(
                    dataset,
                    _force_difference_cache(env, dataset_fp, model_fp, dataset, model),
                )
            if forces is None:
                return None
            return _PredictionView(forces)
        except Exception as exc:
            logger.warning("local_scene: prediction lookup failed: %s", exc)
            return None

    return get_prediction


def make_force_resolver(env: Any, model_key: str | None = None):
    """Return a get_forces(dataset_ref, idx) callable for local force arrow rendering.

    model_key=None → ground-truth forces from dataset.getForces().
    model_key=<key> → forces from the cached model prediction.
    """
    def get_forces(dataset_ref: str | None, idx: int):
        if not dataset_ref:
            return None
        try:
            dataset = env.datasets.get(dataset_ref)
        except Exception as exc:
            logger.warning("local_scene: force resolver — getDataset failed: %s", exc)
            return None
        if dataset is None:
            return None
        if model_key is None:
            try:
                return dataset.getForces(indices=idx)
            except Exception as exc:
                logger.warning("local_scene: force resolver — getForces failed: %s", exc)
                return None
        else:
            try:
                model = env.models.get(model_key)
                if model is None:
                    return None
                direct_forces = _direct_forces_cache(env, dataset_ref, model_key, dataset, model)
                if direct_forces is not None:
                    return _force_frame(direct_forces, idx)
                result = _force_difference_cache(env, dataset_ref, model_key, dataset, model)
                if result is None:
                    return None
                forces = _prediction_forces_from_difference(dataset, result)
                return None if forces is None else _force_frame(forces, idx)
            except Exception as exc:
                logger.warning("local_scene: force resolver — model prediction failed: %s", exc)
                return None
    return get_forces


def available_prediction_refs(env: Any, dataset_ref: str | None) -> list[str]:
    """Return model refs with cached force predictions for the dataset."""
    if not dataset_ref:
        return []
    try:
        dataset = env.datasets.get(dataset_ref)
    except Exception as exc:
        logger.warning("local_scene: available_prediction_refs — getDataset failed: %s", exc)
        dataset = None
    if dataset is None:
        return []

    try:
        model_keys = list(env.models.all_keys())
    except Exception as exc:
        logger.warning("local_scene: available_prediction_refs — getAllModelKeys failed: %s", exc)
        model_keys = []

    refs = []
    for model_ref in model_keys:
        try:
            model = env.models.get(model_ref)
            if model is None:
                continue
            if _direct_forces_cache(env, dataset_ref, model_ref, dataset, model) is not None:
                refs.append(model_ref)
                continue
            if _force_difference_cache(env, dataset_ref, model_ref, dataset, model) is not None:
                refs.append(model_ref)
        except Exception as exc:
            logger.warning(
                "local_scene: prediction availability failed for %r: %s",
                model_ref,
                exc,
            )
    return refs


def parse_index_list(text: Any) -> list[int]:
    out = []
    for token in str(text or "").replace(",", " ").split():
        try:
            out.append(int(token))
        except ValueError:
            pass
    return out


def parse_filter_tokens(text: Any) -> list[int | str]:
    out = []
    for token in str(text or "").replace(",", " ").split():
        try:
            out.append(int(token))
        except ValueError:
            out.append(token)
    return out


def _setting(settings: Any, key: str, default: Any = None) -> Any:
    if settings is None:
        return default
    try:
        return settings.get(key, default)
    except TypeError:
        try:
            value = settings.get(key)
        except Exception:
            return default
        return default if value is None else value
    except Exception:
        return default

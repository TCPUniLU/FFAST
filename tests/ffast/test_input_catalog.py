"""The inputs list of the Metrics pane (ADR 0057 rule 10)."""
import asyncio
from types import SimpleNamespace

import numpy as np

import ffast.metrics.builtin  # noqa: F401
from ffast.metrics import dims
from ffast.metrics.dims import shape_to_str
from ffast.metrics.input_catalog import build_input_catalog
from ffast.metrics.registry import MetricRegistry, default_registry
from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.session.server_session import ServerSession


class _Dataset:
    fingerprint = "ds1"

    def availableFieldKeys(self):
        return ["REF_energy", "total charge"], ["charges"]


def _registry():
    r = MetricRegistry()

    @r.metric(id="lab.e", inputs={"x": "reference.energies"}, shape=(dims.N_frames,),
              unit="energy", label="E")
    def e(x):
        return x

    @r.metric(id="lab.e__kde__p1", inputs={"x": "lab.e"}, shape=(dims.curve_xy, dims.grid),
              unit="energy")
    def derived(x):
        return x

    return r


def _by_group(entries):
    out = {}
    for e in entries:
        out.setdefault(e["group"], []).append(e)
    return out


def test_without_a_prediction_only_reference_inputs_and_file_keys_are_offered():
    groups = _by_group(build_input_catalog(_registry(), _Dataset()))
    assert set(groups) == {"Reference", "File keys", "Metrics"}
    assert [e["ref"] for e in groups["Reference"]] == [
        "reference.energies", "reference.forces", "reference.positions", "reference.masses"]


def test_a_prediction_adds_its_energies_and_forces_but_not_positions():
    groups = _by_group(build_input_catalog(_registry(), _Dataset(), model=SimpleNamespace(fingerprint="m1")))
    assert [e["ref"] for e in groups["Prediction"]] == ["prediction.energies", "prediction.forces"]


def test_inputs_on_one_side_only_are_marked():
    groups = _by_group(build_input_catalog(_registry(), _Dataset(), model=SimpleNamespace(fingerprint="m1")))
    only = {e["ref"]: e["only"] for e in groups["Reference"]}
    assert only == {"reference.energies": None, "reference.forces": None,
                    "reference.positions": "reference", "reference.masses": "reference"}


def test_file_keys_come_from_both_files_and_say_which_side_has_them():
    model = SimpleNamespace(fingerprint="m1")
    fields = {("m1", "ds1"): {"info": {"REF_energy": np.zeros(2), "pred_only": np.zeros(2)}, "atoms": {}}}
    keys = _by_group(build_input_catalog(_registry(), _Dataset(), model, fields))["File keys"]
    got = {e["ref"]: e["only"] for e in keys}
    assert got == {
        "reference.info.REF_energy": None, "prediction.info.REF_energy": None,
        "reference.info.total charge": "reference", "reference.atoms.charges": "reference",
        "prediction.info.pred_only": "prediction",
    }
    shapes = {e["ref"]: e["shape"] for e in keys}
    assert shapes["reference.atoms.charges"] == "N_atoms" and shapes["reference.info.REF_energy"] == "N_frames"


def test_prediction_file_keys_of_a_sub_dataset_are_found_on_its_parent():
    sub = SimpleNamespace(fingerprint="sub", parent=_Dataset(), availableFieldKeys=lambda: ([], []))
    fields = {("m1", "ds1"): {"info": {"k": np.zeros(1)}, "atoms": {}}}
    keys = _by_group(build_input_catalog(_registry(), sub, SimpleNamespace(fingerprint="m1"), fields))["File keys"]
    assert [e["ref"] for e in keys] == ["prediction.info.k"]


def test_suggested_names_are_short_and_valid_variable_names():
    entries = {e["ref"]: e["name"] for e in build_input_catalog(
        _registry(), _Dataset(), model=SimpleNamespace(fingerprint="m1"))}
    assert entries["reference.energies"] == "ref_E"
    assert entries["prediction.forces"] == "pred_F"
    assert entries["reference.info.total charge"] == "ref_total_charge"
    assert entries["lab.e"] == "e"
    assert all(n.isidentifier() for n in entries.values())


def test_formula_raw_input_definitions_match_catalog_and_expression_shapes():
    from ffast.metrics.expr import _RAW_REF_SHAPE
    from ffast.metrics.inputs import (
        ALL_VALID_REFS, RAW_INPUTS, parse_field_ref,
    )

    definitions = {entry.ref: entry for entry in RAW_INPUTS}
    assert set(definitions) == set(_RAW_REF_SHAPE)
    catalog = {entry["ref"]: entry for entry in build_input_catalog(
        _registry(), _Dataset(), model=SimpleNamespace(fingerprint="m1"))}
    for ref, definition in definitions.items():
        assert catalog[ref]["label"] == definition.label
        assert catalog[ref]["shape"] == shape_to_str(definition.shape)
    assert {"reference.stress", "reference.elements",
            "selection.indices"} <= ALL_VALID_REFS
    assert parse_field_ref("reference.atoms.extra") == (
        "reference", "atoms", "extra",
    )


def test_per_atom_inputs_agree_across_whole_dataset_and_frame_adapters():
    from ffast.metrics.input_resolver import InputResolver
    from ffast.metrics.inputs import RAW_INPUTS
    from ffast.visualization.color_values import _resolve_symbolic_ref

    class Dataset:
        fingerprint = "ds1"
        positions = np.arange(12, dtype=float).reshape(2, 2, 3)
        forces = positions + 1
        charges = np.array([[1., 2.], [3., 4.]])
        elements = np.array([1, 8])

        def getN(self):
            return 2

        def getOffsets(self):
            return np.array([0, 2, 4])

        def getCoordinates(self, idx):
            return self.positions[idx]

        def getAllCoordinates(self):
            return self.positions.reshape(-1, 3)

        def getForces(self, idx):
            return self.forces[idx]

        def getAllForces(self):
            return self.forces.reshape(-1, 3)

        def getElements(self, idx):
            return self.elements

        def getAllElements(self):
            return np.tile(self.elements, 2)

        def getAtomField(self, key, indices=None):
            if key != "q":
                return None
            return self.charges if indices is None else self.charges[indices]

    dataset = Dataset()
    model = SimpleNamespace(fingerprint="m1")
    predicted_forces = dataset.forces + 5
    fields = {("m1", "ds1"): {
        "atoms": {"q": np.array([[10., 20.], [30., 40.]])},
        "info": {},
    }}
    entity = SimpleNamespace(get=lambda _field: predicted_forces)
    env = SimpleNamespace(
        predictionFields=fields,
        getData=lambda _key, **_kwargs: entity,
    )
    resolver = InputResolver(env)
    prediction = lambda *_args: SimpleNamespace(forces=predicted_forces)
    state = SimpleNamespace(dataset_ref="ds1")
    raw_per_atom = [item.ref for item in RAW_INPUTS
                    if item.shape[0] == dims.N_atoms]
    refs = raw_per_atom + ["reference.atoms.q", "prediction.atoms.q"]

    for ref in refs:
        whole = resolver.resolve(ref, model=model, dataset=dataset)
        frame = _resolve_symbolic_ref(
            ref, dataset, 1, dataset.getCoordinates(1), dataset.getElements(1),
            prediction, state, model.fingerprint, fields,
        )
        np.testing.assert_allclose(frame, np.asarray(whole)[2:4], err_msg=ref)


def test_metrics_are_listed_without_derived_transforms():
    metrics = _by_group(build_input_catalog(_registry(), None))["Metrics"]
    assert [(m["ref"], m["shape"], m["label"]) for m in metrics] == [("lab.e", "N_frames", "E")]


def test_a_box_setting_is_listed_only_once_saved():
    # A tab or a colouring registers a keep-box setting nobody named (ADR 0065).
    r = _registry()
    r.ensure("lab.e[dataset;mean]")
    r.ensure("lab.e[dataset;max]")
    saved = SimpleNamespace(origin=lambda m: "user" if m == "lab.e[dataset;mean]" else "built-in")
    metrics = _by_group(build_input_catalog(r, None, user_metrics=saved))["Metrics"]
    assert [m["ref"] for m in metrics] == ["lab.e", "lab.e[dataset;mean]"]


def test_the_server_answers_the_window_that_asked():
    env = SimpleNamespace(datasets={"ds1": _Dataset()}, models={}, data=SimpleNamespace(predictionFields={}))

    async def scenario():
        s = ServerSession(env, asyncio.Queue())
        await s.dispatch(control.REQUEST_METRIC_INPUTS, [], {"dataset_fp": "ds1"})
        return unpack(s.outbound.get_nowait())

    event, _, kwargs = asyncio.run(scenario())
    assert event == control.METRIC_INPUTS and kwargs["dataset_fp"] == "ds1"
    refs = {e["ref"] for e in kwargs["inputs"]}
    assert {"reference.energies", "reference.info.REF_energy", "ffast.force_difference"} <= refs
    assert control.REQUEST_METRIC_INPUTS not in control.MUTATING_CLIENT_EVENTS

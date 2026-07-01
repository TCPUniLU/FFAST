"""Dataset Fields: arbitrary xyz keys as metric inputs (ADR 0023).

Covers the ref pattern, the freeze-time validation predicate, the passthrough
compiler, the strict loader readers, prediction-side declared-key discovery, the
InputResolver branches, and the TOML config model. Isolated registries keep the
global default_registry clean (mirrors test_transform_compiler.py).
"""
import numpy as np
import pytest
from ase import Atoms

from ffast.metrics import dims
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.registry import MetricRegistry
from ffast.metrics.inputs import is_field_ref, is_valid_ref, parse_field_ref
from ffast.metrics.fields import (
    compile_field_metric,
    declared_field_keys,
    field_passthrough,
)
from modules.loaders.aseDataset import (
    available_field_keys,
    read_atom_field,
    read_frame_field,
)


# --- ref pattern ------------------------------------------------------------ #
def test_parse_field_ref():
    assert parse_field_ref("reference.atoms.charges") == ("reference", "atoms", "charges")
    assert parse_field_ref("prediction.info.dipole") == ("prediction", "info", "dipole")
    assert parse_field_ref("reference.energies") is None
    assert parse_field_ref("foo.bar") is None
    assert parse_field_ref(None) is None


def test_is_valid_ref_closed_set_plus_pattern():
    assert is_valid_ref("reference.forces")          # closed set
    assert is_valid_ref("reference.atoms.q")         # field pattern
    assert is_valid_ref("prediction.info.t")
    assert not is_valid_ref("reference.atoms")       # no key
    assert not is_valid_ref("bogus.ref")
    assert is_field_ref("prediction.atoms.x") and not is_field_ref("reference.forces")


# --- freeze-time validation ------------------------------------------------- #
def test_field_ref_metric_freezes_clean():
    r = MetricRegistry()

    @r.metric(id="t.q", inputs={"value": "reference.atoms.charges"},
              shape=(dims.N_atoms,), unit="dimensionless")
    def q(value):
        return np.asarray(value)

    assert r.freeze() == []


def test_truly_unknown_ref_still_errors():
    r = MetricRegistry()

    @r.metric(id="t.bad", inputs={"value": "reference.atoms"},  # missing key → not a field
              shape=(dims.N_atoms,), unit="dimensionless")
    def bad(value):
        return np.asarray(value)

    errors = r.freeze()
    assert errors and "Unknown symbolic ref" in errors[0][1]


# --- passthrough compiler --------------------------------------------------- #
def test_compile_field_metric_shape_inference():
    r = MetricRegistry()
    compile_field_metric("lab.q", "reference.atoms.charges", registry=r)
    compile_field_metric("lab.t", "reference.info.temperature", registry=r)
    assert r.get("lab.q")[0].shape == (dims.N_atoms,)
    assert r.get("lab.t")[0].shape == (dims.N_frames,)
    assert r.freeze() == []


def test_compile_field_metric_idempotent():
    r = MetricRegistry()
    a = compile_field_metric("lab.q", "reference.atoms.charges", registry=r)
    b = compile_field_metric("lab.q", "reference.atoms.charges", registry=r)
    assert a == b == "lab.q"
    assert len(r.list_metrics()) == 1


def test_compile_rejects_non_field_ref():
    r = MetricRegistry()
    with pytest.raises(ValueError):
        compile_field_metric("lab.bad", "reference.forces", registry=r)


def test_field_metric_runs_passthrough():
    r = MetricRegistry()
    compile_field_metric("lab.q", "reference.atoms.charges", registry=r)
    r.freeze()
    result = InProcessExecutor(r).run("lab.q", {"value": [0.1, -0.2, 0.3]}, {})
    np.testing.assert_allclose(result.values, [0.1, -0.2, 0.3])


def test_field_passthrough_picklable():
    import pickle
    assert pickle.loads(pickle.dumps(field_passthrough))([1, 2]).tolist() == [1, 2]


# --- declared-key discovery (prediction eager-extract set) ------------------ #
def test_declared_field_keys():
    r = MetricRegistry()
    compile_field_metric("lab.q", "prediction.atoms.charges", registry=r)
    compile_field_metric("lab.d", "prediction.info.dipole", registry=r)
    compile_field_metric("lab.ref", "reference.atoms.charges", registry=r)
    pred = declared_field_keys("prediction", registry=r)
    assert pred == {"info": {"dipole"}, "atoms": {"charges"}}
    ref = declared_field_keys("reference", registry=r)
    assert ref == {"info": set(), "atoms": {"charges"}}


# --- strict loader readers -------------------------------------------------- #
def _atoms_list(n_frames=3, charges=True, vector=False, nonnumeric=False):
    al = []
    for i in range(n_frames):
        a = Atoms("H2", positions=np.zeros((2, 3)))
        a.info["temperature"] = 100.0 + i
        if nonnumeric:
            a.info["label"] = "rotamer"
        if charges:
            a.arrays["charges"] = np.array([0.1 * i, -0.1 * i])
        if vector:
            a.arrays["dipvec"] = np.zeros((2, 3))
        al.append(a)
    return al


def test_read_frame_field_uniform():
    al = _atoms_list()
    np.testing.assert_allclose(read_frame_field(al, "temperature"), [100.0, 101.0, 102.0])


def test_read_atom_field_uniform_shape():
    al = _atoms_list()
    assert np.asarray(read_atom_field(al, "charges", variable=False)).shape == (3, 2)


def test_read_atom_field_variable_list():
    al = _atoms_list()
    out = read_atom_field(al, "charges", variable=True)
    assert isinstance(out, list) and [x.shape for x in out] == [(2,), (2,), (2,)]


def test_read_atom_field_scalar_index():
    al = _atoms_list()
    np.testing.assert_allclose(read_atom_field(al, "charges", variable=False, indices=1), [0.1, -0.1])


def test_strict_missing_key_is_none():
    al = _atoms_list(charges=False)
    assert read_atom_field(al, "charges", variable=False) is None


def test_strict_nonnumeric_is_none():
    al = _atoms_list(nonnumeric=True)
    assert read_frame_field(al, "label") is None


def test_strict_vector_is_none():
    al = _atoms_list(vector=True)
    assert read_atom_field(al, "dipvec", variable=False) is None


def test_strict_partial_presence_is_none():
    al = _atoms_list()
    del al[1].info["temperature"]
    assert read_frame_field(al, "temperature") is None


def test_available_field_keys():
    al = _atoms_list(vector=True, nonnumeric=True)
    frame, atom = available_field_keys(al)
    assert frame == ["temperature"]          # numeric scalar only (label excluded)
    assert atom == ["charges"]               # per-atom scalar only (dipvec excluded)


# --- InputResolver branches ------------------------------------------------- #
class _FakeDS:
    isVariable = False
    parent = None
    fingerprint = "ds1"

    def getAtomField(self, key, indices=None):
        return np.array([[0.1, -0.1], [0.2, -0.2]]) if key == "charges" else None

    def getFrameField(self, key, indices=None):
        return np.array([300.0, 310.0]) if key == "temperature" else None


class _FakeModel:
    fingerprint = "m1"


class _FakeEnv:
    def __init__(self, pred=None):
        self.predictionFields = pred or {}

    def getData(self, *a, **k):
        return None


def test_resolver_reference_fields():
    from ffast.metrics.input_resolver import InputResolver
    r = InputResolver(_FakeEnv())
    ds, m = _FakeDS(), _FakeModel()
    assert r.resolve("reference.atoms.charges", model=m, dataset=ds).shape == (2, 2)
    np.testing.assert_allclose(r.resolve("reference.info.temperature", model=m, dataset=ds), [300.0, 310.0])
    assert r.resolve("reference.atoms.nope", model=m, dataset=ds) is None


def test_resolver_prediction_field():
    from ffast.metrics.input_resolver import InputResolver
    env = _FakeEnv({("m1", "ds1"): {"atoms": {"q": np.array([[1.0, 2.0], [3.0, 4.0]])}, "info": {}}})
    r = InputResolver(env)
    ds, m = _FakeDS(), _FakeModel()
    assert r.resolve("prediction.atoms.q", model=m, dataset=ds).shape == (2, 2)
    assert r.resolve("prediction.atoms.absent", model=m, dataset=ds) is None
    assert r.resolve("prediction.info.t", model=m, dataset=ds) is None  # empty store kind


def test_resolver_prediction_field_walks_to_parent():
    from ffast.metrics.input_resolver import InputResolver
    env = _FakeEnv({("m1", "ds1"): {"atoms": {"q": np.array([[5.0], [6.0]])}, "info": {}}})

    class _Sub:
        isVariable = False
        fingerprint = "sub1"
        parent = _FakeDS()

    r = InputResolver(env)
    out = r.resolve("prediction.atoms.q", model=_FakeModel(), dataset=_Sub())
    np.testing.assert_allclose(out, [[5.0], [6.0]])


def test_metric_needs_prediction_counts_prediction_fields():
    from ffast.metrics.input_resolver import metric_needs_prediction
    r = MetricRegistry()
    compile_field_metric("lab.pf", "prediction.atoms.q", registry=r)
    compile_field_metric("lab.rf", "reference.atoms.q", registry=r)
    r.freeze()
    assert metric_needs_prediction("lab.pf", registry=r)
    assert not metric_needs_prediction("lab.rf", registry=r)


# --- TOML config model ------------------------------------------------------ #
def test_field_metric_config_valid():
    from ffast.config.models import FieldMetricConfig
    c = FieldMetricConfig(id="lab.q", ref="reference.atoms.charges", label="Q", unit="e")
    assert c.id == "lab.q"


def test_field_metric_config_rejects_bad_id_and_ref():
    from ffast.config.models import FieldMetricConfig
    with pytest.raises(ValueError):
        FieldMetricConfig(id="noDot", ref="reference.atoms.q")
    with pytest.raises(ValueError):
        FieldMetricConfig(id="lab.x", ref="reference.forces")

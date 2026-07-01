import pytest
from pydantic import ValidationError

from ffast.protocol import (
    DatasetKeysResponse,
    DatasetLengthResponse,
    DatasetMeta,
    DirListing,
    MetricCatalog,
    MetricResultMessage,
    ModelMeta,
)


# Representative payloads exactly as DatasetLoader.toMetaDict (uniform) and
# VariableDatasetLoader.toMetaDict (variable) emit them — see
# datasetLoaders/loader.py:208 and :332.
UNIFORM = {
    "name": "aspirin", "n": 100, "has_forces": True, "is_sub": False,
    "variable": False, "elements": [6, 1, 1, 8], "offsets": None,
    "path": "/data/aspirin.xyz", "source_type": "ASE",
}
VARIABLE = {
    "name": "mixed", "n": 50, "has_forces": False, "is_sub": True,
    "variable": True, "elements": [6, 1, 8], "offsets": [0, 2, 3],
    "path": "/data/mixed.xyz", "source_type": "Variable ASE",
}


@pytest.mark.parametrize("payload", [UNIFORM, VARIABLE])
def test_roundtrip_preserves_wire_shape(payload):
    # model_dump() must reproduce the exact dict toMetaDict() produced, so
    # routing REMOTE_DATASET_META through DatasetMeta is parity-preserving.
    assert DatasetMeta.model_validate(payload).model_dump() == payload


def test_model_fields_match_tometadict_keys():
    # Drift guard: model fields are exactly the toMetaDict key set (minus
    # fingerprint, which travels as the event arg, not the payload). If a
    # producer adds a key, this test and the server-side extra="forbid"
    # validation both flag it.
    assert set(DatasetMeta.model_fields) == {
        "name", "n", "has_forces", "is_sub", "variable",
        "elements", "offsets", "path", "source_type",
    }


def test_extra_key_rejected():
    with pytest.raises(ValidationError):
        DatasetMeta.model_validate({**UNIFORM, "unexpected": 1})


def test_defaults_allow_minimal_payload():
    m = DatasetMeta()
    assert m.name is None and m.n is None
    assert m.has_forces is True and m.variable is False and m.is_sub is False


# ── ModelMeta (REMOTE_MODEL_META) ────────────────────────────────────────────
# Payload exactly as server.py emits it (server.py:152 and :1214).
MODEL = {"name": "MACE-foo", "dataset_fingerprints": ["abc123", "def456"]}


def test_model_roundtrip_preserves_wire_shape():
    assert ModelMeta.model_validate(MODEL).model_dump() == MODEL


def test_model_fields_match_payload_keys():
    assert set(ModelMeta.model_fields) == {"name", "dataset_fingerprints"}


def test_model_extra_key_rejected():
    with pytest.raises(ValidationError):
        ModelMeta.model_validate({**MODEL, "unexpected": 1})


def test_model_defaults_allow_minimal_payload():
    m = ModelMeta()
    assert m.name is None and m.dataset_fingerprints is None


# ── MetricCatalog (METRIC_CATALOG) ───────────────────────────────────────────
def test_catalog_roundtrip_matches_real_build_output():
    # Strongest parity guard: validate the catalog the server actually builds and
    # confirm model_dump() reproduces it bit-for-bit (catches unit-type or
    # int/float drift over every registered metric).
    import ffast.metrics.builtin  # noqa: F401 — register built-ins
    from ffast.metrics.catalog import build_metric_catalog
    from ffast.metrics.registry import _default_registry

    real = build_metric_catalog(_default_registry)
    dumped = MetricCatalog(metrics=real).model_dump()["metrics"]
    assert dumped == real
    assert len(real) > 0


def test_catalog_entry_extra_key_rejected():
    bad = {"metrics": [{"id": "x", "label": "X", "shape": "scalar",
                        "unit": None, "parameters": {}, "surprise": 1}]}
    with pytest.raises(ValidationError):
        MetricCatalog.model_validate(bad)


def test_catalog_empty_default():
    assert MetricCatalog().metrics == []


# ── probe responses (DATASET_KEYS_RESPONSE / DATASET_LENGTH_RESPONSE / DIR_LISTING) ──
# Payloads exactly as server.py emits them (server.py:537, :569, :616).
KEYS_OK = {"energy_keys": ["energy"], "force_keys": ["forces"],
           "has_calculator_energy": True, "has_calculator_forces": True, "error": None}
KEYS_ERR = {"energy_keys": [], "force_keys": [],
            "has_calculator_energy": False, "has_calculator_forces": False,
            "error": "bad file"}
LENGTH_OK = {"n": 1000, "error": None}
LENGTH_ERR = {"n": None, "error": "not found"}
DIR_OK = {
    "path": "/data", "parent": "/", "home": "/home/u",
    "entries": [{"name": "sub", "is_dir": True, "size": 0},
                {"name": "a.xyz", "is_dir": False, "size": 1234}],
    "error": None,
}
DIR_ROOT = {"path": "/", "parent": None, "home": "/home/u", "entries": [], "error": None}


@pytest.mark.parametrize("payload", [KEYS_OK, KEYS_ERR])
def test_dataset_keys_roundtrip(payload):
    assert DatasetKeysResponse.model_validate(payload).model_dump() == payload


@pytest.mark.parametrize("payload", [LENGTH_OK, LENGTH_ERR])
def test_dataset_length_roundtrip(payload):
    assert DatasetLengthResponse.model_validate(payload).model_dump() == payload


@pytest.mark.parametrize("payload", [DIR_OK, DIR_ROOT])
def test_dir_listing_roundtrip(payload):
    # nested DirEntry list must round-trip bit-for-bit too
    assert DirListing.model_validate(payload).model_dump() == payload


# ── broadcast-event catalogue (SERVER_TO_CLIENT signals) ─────────────────────
def test_broadcast_catalogue_matches_server_to_client():
    # The broadcast events are catalogued (not typed); this drift test keeps the
    # catalogue's event-name set in lockstep with the real SERVER_TO_CLIENT set,
    # so adding/removing a broadcast event without updating the catalogue fails.
    from ffast.protocol.notifications import BROADCAST_EVENTS
    from ffast.protocol.rpc import SERVER_TO_CLIENT

    assert set(BROADCAST_EVENTS) == set(SERVER_TO_CLIENT)


def test_probe_responses_reject_extra_keys():
    with pytest.raises(ValidationError):
        DatasetKeysResponse.model_validate({**KEYS_OK, "x": 1})
    with pytest.raises(ValidationError):
        DatasetLengthResponse.model_validate({**LENGTH_OK, "x": 1})
    with pytest.raises(ValidationError):
        DirListing.model_validate({**DIR_OK, "x": 1})
    with pytest.raises(ValidationError):
        # extra key inside a nested DirEntry
        DirListing.model_validate(
            {**DIR_OK, "entries": [{"name": "a", "is_dir": False, "size": 0, "x": 1}]}
        )


# ── MetricResultMessage (METRIC_RESULT, hybrid metadata + numpy array) ───────
# Exercises the real pack/unpack helpers in ffast.protocol.rpc, not just the model, so
# the wire shape (incl. the encoded-array field) is asserted bit-for-bit.
def _sample_metric_result():
    import numpy as np
    from ffast.metrics.models import MetricResult

    return MetricResult(
        metric_id="ffast.force_rmse", shape="per_structure", dtype="float64",
        unit="eV/Angstrom", compute_parameters={"order": 1},
        implementation_hash="impl-abc", checksum="chk-def",
        values=np.array([1.0, 2.0, 3.0]),
    )


def test_metric_result_ok_wire_shape_and_roundtrip():
    import numpy as np
    from ffast.protocol.rpc import pack_metric_result, unpack, unpack_metric_result

    r = _sample_metric_result()
    event, args, kw = unpack(pack_metric_result("k1", r.metric_id, True, r))

    assert event == "METRIC_RESULT"
    assert args == ["k1", "ffast.force_rmse"]
    # exact key set — metadata + encoded array, no extra/None keys (parity with
    # the pre-typing hand-rolled payload)
    assert set(kw) == {
        "ok", "metric_id", "shape", "dtype", "unit", "compute_parameters",
        "implementation_hash", "checksum", "values",
    }
    assert kw["ok"] is True
    assert kw["values"]["__ndarray__"] is True  # array stays binary-encoded

    back = unpack_metric_result(kw)  # validates, then rebuilds MetricResult
    assert back.metric_id == r.metric_id
    assert back.shape == r.shape
    assert back.dtype == r.dtype
    assert back.unit == r.unit
    assert back.compute_parameters == r.compute_parameters
    assert back.implementation_hash == r.implementation_hash
    assert back.checksum == r.checksum
    assert np.array_equal(np.asarray(back.values), np.asarray(r.values))


def test_metric_result_not_ok_wire_shape():
    from ffast.protocol.rpc import pack_metric_result, unpack, unpack_metric_result

    event, args, kw = unpack(pack_metric_result("k2", "ffast.x", False, None))
    assert event == "METRIC_RESULT"
    # not-ok payload is exactly {"ok": False} — metadata fields excluded
    assert kw == {"ok": False}
    assert unpack_metric_result(kw) is None


def test_metric_result_extra_key_rejected():
    with pytest.raises(ValidationError):
        MetricResultMessage.model_validate({"ok": True, "surprise": 1})


def test_metric_result_fields_match_metricresult():
    # Drift guard: the message's metadata fields are exactly MetricResult's
    # fields plus the transport-only `ok` flag.
    from ffast.metrics.models import MetricResult

    assert set(MetricResultMessage.model_fields) == set(
        MetricResult.model_fields
    ) | {"ok"}

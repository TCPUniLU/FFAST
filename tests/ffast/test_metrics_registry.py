import pytest
from pydantic import ValidationError
from ffast.metrics.registry import MetricRegistry

@pytest.fixture
def registry():
    return MetricRegistry()

def test_register_and_retrieve_metric(registry):
    @registry.metric(
        id="ffast.test_metric",
        inputs={"reference": "reference.forces"},
        shape="per_structure_per_atom",
        unit="energy",
    )
    def test_metric(reference):
        pass
    
    decl, func = registry.get("ffast.test_metric")
    assert decl.id == "ffast.test_metric"
    assert decl.inputs == {"reference": "reference.forces"}
    assert decl.shape == "per_structure_per_atom"
    assert decl.unit == "energy"
    assert func is test_metric

def test_duplicate_registration_rejected(registry):
    @registry.metric(
        id="ffast.test_metric",
        inputs={"reference": "reference.forces"},
        shape="per_structure_per_atom",
        unit="energy",
    )
    def test_metric(reference):
        pass
    
    with pytest.raises(ValueError):
        @registry.metric(
            id="ffast.test_metric",
            inputs={"reference": "reference.forces"},
            shape="per_structure_per_atom",
            unit="energy",
        )
        def duplicate_metric(reference):
            pass

def test_function_still_callable(registry):
    @registry.metric(
        id="ffast.test_metric",
        inputs={},
        shape="scalar",
        unit="energy",
    )
    def passthrough(x):
        return x * 2

    assert passthrough(3) == 6

def test_unnamespaced_id_rejected(registry):
    with pytest.raises(ValueError):
        @registry.metric(
            id="test_metric_without_namespace",
            inputs={},
            shape="scalar",
            unit="energy",
        )
        def invalid_metric(x):
            return x
    
def test_list_metrics(registry):
    @registry.metric(
        id="ffast.metric_one",
        inputs={},
        shape="scalar",
        unit="energy",
    )
    def metric_one(x):
        return x
    
    @registry.metric(
        id="ffast.metric_two",
        inputs={},
        shape="scalar",
        unit="energy",
    )
    def metric_two(x):
        return x

    assert set(registry.list_metrics()) == {"ffast.metric_one", "ffast.metric_two"}

def test_get_unknown_raises(registry):
    with pytest.raises(KeyError):
        registry.get("ffast.non_existent_metric")
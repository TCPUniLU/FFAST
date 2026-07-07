import pytest 
from pydantic import ValidationError
from ffast.metrics.models import ChoiceParameter, FloatParameter, BoolParameter, MetricSchema, ParameterSchema

def valid_declaration(**overrides):
    base = {
        "id": "ffast.test_metric",
        "inputs": {"reference": "reference.forces"},
        "shape": "per_structure_per_atom",
        "unit": "kcal/mol",
        "parameters": {
            "norm": {"type": "choice", 
                     "choices": ["l1", "l2"], 
                     "default": "l2", 
                     "role": "compute"},
        },
    }
    base.update(overrides)
    return MetricSchema.model_validate(base)

def test_discriminator_routes_choice():
    metric = valid_declaration()
    assert isinstance(metric.parameters["norm"], ChoiceParameter)

def test_discriminator_routes_float():
    metric = valid_declaration(parameters={
        "tolerance": {"type": "float", 
                      "default": 0.1, 
                      "role": "compute", 
                      "min": 0.0, 
                      "max": 1.0},
    })
    assert isinstance(metric.parameters["tolerance"], FloatParameter)

def test_discriminator_routes_bool():
    metric = valid_declaration(parameters={
        "use_weights": {"type": "bool", 
                        "default": True, 
                        "role": "present"},
    })
    assert isinstance(metric.parameters["use_weights"], BoolParameter)

def test_unknown_key_on_declaration_rejected():
    with pytest.raises(ValidationError):
        valid_declaration(unknown_key="ups")
    
def test_unknown_key_on_parameter_rejected():
    with pytest.raises(ValidationError):
        valid_declaration(parameters={
            "norm": {"type": "choice", 
                     "choices": ["l1", "l2"], 
                     "default": "l2", 
                     "role": "compute",
                     "wrong_key": 123},
        })

def test_bad_discriminator_rejected():
    with pytest.raises(ValidationError):
        valid_declaration(parameters={
            "norm": {"type": "unknown", 
                     "choices": ["l1", "l2"], 
                     "default": "l2", 
                     "role": "compute"},
        })


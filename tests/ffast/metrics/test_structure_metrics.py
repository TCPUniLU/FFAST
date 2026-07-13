"""Tests for the relocated geometry metrics (gyradius + measurements)."""
import numpy as np
import pytest

from ffast.metrics.builtin.structure_metrics import (
    angle,
    dihedral,
    distance,
    gyradius,
)


class TestGyradius:
    def test_equal_weight_pair(self):
        # ±1 along x, equal Z → Rg = 1
        assert gyradius([[1.0, 0, 0], [-1.0, 0, 0]], [1, 1]) == pytest.approx(1.0)

    def test_z_weighting(self):
        # heavy atom at origin dominates the COM → smaller Rg than unweighted
        rg = gyradius([[0.0, 0, 0], [2.0, 0, 0]], [8, 1])
        # COM = (8*0 + 1*2)/9 = 0.2222; Rg^2 = (8*0.2222^2 + 1*1.7778^2)/9
        assert rg == pytest.approx(0.6285, abs=1e-3)

    def test_registered(self):
        from ffast.metrics.registry import _default_registry as reg
        assert reg.has("ffast.gyradius")

    def test_single_atom_is_zero(self):
        # A single atom is its own centre of mass -> radius of gyration 0.
        assert gyradius([[0.0, 0.0, 0.0]], [1]) == pytest.approx(0.0)

    def test_zero_total_weight_raises(self):
        # gyradius normalises by the total atomic-number weight (sum of
        # ``elements``); an all-zero-element structure would make the
        # centre-of-mass division 0/0. The metric guards this and raises a clear
        # ValueError rather than silently returning NaN.
        with pytest.raises(ValueError, match="total atomic-number weight is zero"):
            gyradius([[1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]], [0, 0])

    def test_nan_position_propagates(self):
        # A NaN coordinate taints the whole radius-of-gyration reduction.
        rg = gyradius([[np.nan, 0.0, 0.0], [-1.0, 0.0, 0.0]], [1, 1])
        assert np.isnan(rg)


class TestDistance:
    def test_3_4_5(self):
        assert distance([[0.0, 0, 0], [3.0, 4.0, 0]], [0, 1]) == pytest.approx(5.0)

    def test_selection_order_independent(self):
        assert distance([[0.0, 0, 0], [3.0, 4.0, 0]], [1, 0]) == pytest.approx(5.0)


class TestAngle:
    def test_right_angle(self):
        # vertex at index 1; (i-j)=x, (k-j)=y → 90°
        a = angle([[1.0, 0, 0], [0.0, 0, 0], [0.0, 1.0, 0]], [0, 1, 2])
        assert a == pytest.approx(90.0)

    def test_straight_angle(self):
        a = angle([[1.0, 0, 0], [0.0, 0, 0], [-1.0, 0, 0]], [0, 1, 2])
        assert a == pytest.approx(180.0)


class TestDihedral:
    def test_ninety_degrees(self):
        d = dihedral(
            [[1.0, 0, 0], [0.0, 0, 0], [0.0, 0, 1.0], [0.0, 1.0, 1.0]],
            [0, 1, 2, 3],
        )
        assert d == pytest.approx(90.0)

    def test_planar_is_zero(self):
        # four coplanar points (cis) → 0°
        d = dihedral(
            [[0.0, 1.0, 0], [0.0, 0, 0], [1.0, 0, 0], [1.0, 1.0, 0]],
            [0, 1, 2, 3],
        )
        assert d == pytest.approx(0.0, abs=1e-9)


class TestRegistration:
    def test_all_registered_with_scalar_shape(self):
        from ffast.metrics.registry import _default_registry as reg
        from ffast.metrics.dims import scalar
        for mid in ("ffast.distance", "ffast.angle", "ffast.dihedral"):
            assert reg.has(mid)
            schema, _ = reg.get(mid)
            assert schema.shape == (scalar,)

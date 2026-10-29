"""Create metric saves a keep-box setting as a user metric under its derived
id (ADR 0065 section 4, decided point 1), with an editable name."""
import numpy as np
import pytest

from ffast.config.user_metrics import MetricError, UserMetricStore, UserMetrics
from ffast.metrics import dims
from ffast.metrics.registry import MetricRegistry

BOX = "base.fd[structure;length;rmse]"


@pytest.fixture
def registry():
    r = MetricRegistry()

    @r.metric(id="base.fd", label="Force Difference", unit="force",
              inputs={"p": "prediction.forces", "r": "reference.forces"},
              shape=(dims.N_atoms, dims.xyz))
    def fd(p, r):
        return np.asarray(p) - np.asarray(r)

    r.freeze()
    return r


@pytest.fixture
def um(tmp_path, registry):
    m = UserMetrics(UserMetricStore(tmp_path / "metrics"), registry, builtin_ids={"base.fd"})
    m.load()
    return m


def box(**kw):
    return {"id": BOX, "expr": "rms(norm(D), by=structure)", "vars": {"D": "base.fd"},
            "label": "Force Difference · RMSE · per frame · length", "unit": "force", **kw}


def test_a_box_setting_is_saved_under_its_id_with_the_name_given(um, registry, tmp_path):
    um.save(box(label="My force RMSE"))
    assert registry.get(BOX)[0].label == "My force RMSE"
    assert um.origin(BOX) == "user"
    assert (tmp_path / "metrics" / f"{BOX}.toml").exists()


def test_it_comes_back_after_a_restart_with_its_name(um, tmp_path):
    um.save(box(label="My force RMSE"))
    fresh = MetricRegistry()
    fresh._metrics.update({"base.fd": um.registry.get("base.fd")})
    again = UserMetrics(UserMetricStore(tmp_path / "metrics"), fresh)
    assert again.load() == []
    assert fresh.get(BOX)[0].label == "My force RMSE"


def test_its_name_can_change_but_not_its_formula(um, registry):
    um.save(box())
    um.save(box(label="Renamed"), previous_id=BOX)
    assert registry.get(BOX)[0].label == "Renamed"
    with pytest.raises(MetricError, match="its formula is set by the boxes"):
        um.save(box(expr="max(norm(D), by=structure)"), previous_id=BOX)


def test_a_box_id_with_another_formula_is_refused(um):
    with pytest.raises(MetricError, match="its formula is set by the boxes"):
        um.save(box(expr="2 * rms(norm(D), by=structure)"))


def test_a_setting_the_boxes_grey_is_refused(um):
    with pytest.raises(MetricError, match="Statistic"):
        um.save(box(id="base.fd[structure;length]", expr="norm(D)"))


def test_a_setting_compiled_on_the_fly_can_still_be_saved(um, registry):
    # Colouring by a box setting registers it; Save as metric keeps it, named.
    registry.ensure(BOX)
    assert um.origin(BOX) == "built-in"           # its base's origin, until saved
    um.save(box(label="Kept"))
    assert um.origin(BOX) == "user"
    assert registry.get(BOX)[0].label == "Kept"


def test_a_setting_a_tab_had_at_start_can_be_saved(tmp_path, registry):
    # A built-in tab uses the setting, so it is registered before user metrics
    # load; it is still nobody's until Create metric names it.
    registry.ensure(BOX)
    m = UserMetrics(UserMetricStore(tmp_path / "metrics"), registry, builtin_ids={"base.fd"})
    m.load()
    m.save(box(label="Kept"))
    assert m.origin(BOX) == "user"
    assert registry.get(BOX)[0].label == "Kept"


def test_a_saved_setting_a_tab_uses_loads_after_a_restart(um, tmp_path):
    um.save(box(label="Kept"))
    fresh = MetricRegistry()
    fresh._metrics.update({"base.fd": um.registry.get("base.fd")})
    fresh.ensure(BOX)                              # the tab registered it first
    again = UserMetrics(UserMetricStore(tmp_path / "metrics"), fresh)
    assert again.load() == []
    assert again.origin(BOX) == "user"
    assert fresh.get(BOX)[0].label == "Kept"


def test_deleting_a_saved_setting_forgets_the_name_but_keeps_the_setting(um, registry):
    um.save(box(label="Kept"))
    um.usage_providers.append(lambda id: ["tab Basic Errors"] if id == BOX else [])
    um.delete(BOX)
    assert um.origin(BOX) == "built-in"
    assert registry.has(BOX)
    assert registry.get(BOX)[0].label == "Force Difference · RMSE · per frame · length"

"""Panel Display Override / Colorbar Display Override persistence (ADR 0029)."""
import client.display_overrides as do


def _redirect(tmp_path, monkeypatch):
    monkeypatch.setattr(do, "OVERRIDES_FILE", str(tmp_path / "display_overrides.json"))


def test_panel_key_is_content_based_not_positional():
    # Same tab/kind/metrics -> same key regardless of any row/col the caller
    # might otherwise have been tempted to fold in (ADR 0029).
    k1 = do.panel_key("Basic Errors", "density", ["ffast.energy_difference_density"])
    k2 = do.panel_key("Basic Errors", "density", ["ffast.energy_difference_density"])
    assert k1 == k2


def test_panel_key_flattens_list_roles():
    k = do.panel_key("Overlay", "overlay_timeline", [["ffast.a", "ffast.b"]])
    assert "ffast.a" in k and "ffast.b" in k


def test_set_and_get_panel_override_round_trip(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    do.set_panel_override(
        "Basic Errors", "density", ["ffast.energy_difference_density"],
        ("x_label", "text"), "Custom Label",
    )
    override = do.get_panel_override(
        "Basic Errors", "density", ["ffast.energy_difference_density"]
    )
    assert override == {"x_label": {"text": "Custom Label"}}


def test_get_panel_override_missing_returns_empty_dict(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    assert do.get_panel_override("Nope", "density", ["ffast.x"]) == {}


def test_clearing_a_field_prunes_empty_branches(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    key_args = ("Basic Errors", "density", ["ffast.energy_difference_density"])
    do.set_panel_override(*key_args, ("x_label", "text"), "Custom Label")
    do.set_panel_override(*key_args, ("x_label", "text"), "")  # clear -> revert to default
    assert do.get_panel_override(*key_args) == {}


def test_clearing_one_field_keeps_its_siblings(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    key_args = ("Basic Errors", "density", ["ffast.energy_difference_density"])
    do.set_panel_override(*key_args, ("x_label", "text"), "Custom Label")
    do.set_panel_override(*key_args, ("x_label", "font_size"), 24)
    do.set_panel_override(*key_args, ("x_label", "text"), None)
    assert do.get_panel_override(*key_args) == {"x_label": {"font_size": 24}}


def test_legend_entry_override_keyed_by_dataset_model(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    key_args = ("Basic Errors", "timeline", ["ffast.energy_error_smoothed"])
    do.set_panel_override(
        *key_args, ("legend", "entries", "aspirin|mace-small"), "My Model"
    )
    override = do.get_panel_override(*key_args)
    assert override["legend"]["entries"]["aspirin|mace-small"] == "My Model"


def test_panel_and_colorbar_overrides_do_not_collide(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    do.set_panel_override(
        "Basic Errors", "density", ["ffast.force_error"], ("x_label", "text"), "P"
    )
    do.set_colorbar_override("ffast.force_error", ("label", "text"), "C")
    assert do.get_panel_override(
        "Basic Errors", "density", ["ffast.force_error"]
    ) == {"x_label": {"text": "P"}}
    assert do.get_colorbar_override("ffast.force_error") == {"label": {"text": "C"}}


def test_colorbar_override_round_trip_and_clear(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    do.set_colorbar_override("ffast.force_error", ("position", "x"), 120)
    do.set_colorbar_override("ffast.force_error", ("position", "y"), 40)
    assert do.get_colorbar_override("ffast.force_error") == {
        "position": {"x": 120, "y": 40}
    }
    do.set_colorbar_override("ffast.force_error", ("position", "x"), None)
    assert do.get_colorbar_override("ffast.force_error") == {"position": {"y": 40}}


def test_overrides_persist_across_reload(tmp_path, monkeypatch):
    _redirect(tmp_path, monkeypatch)
    do.set_panel_override(
        "Basic Errors", "density", ["ffast.energy_difference_density"],
        ("legend", "font_size"), 16,
    )
    # Simulate a fresh process: _load() re-reads from disk each call, so a
    # second get_panel_override call with no cached state must still see it.
    override = do.get_panel_override(
        "Basic Errors", "density", ["ffast.energy_difference_density"]
    )
    assert override == {"legend": {"font_size": 16}}

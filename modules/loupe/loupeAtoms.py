from functools import partial

from UI.clientFeatures import ClientFeature

DEPENDENCIES = []

# Metric shapes that can color atoms: per-atom directly, per-element broadcast
# onto atoms. Scalar / per-frame shapes describe whole structures and can't.
_COLORABLE_SHAPES = ("N_atoms", "N_elements")


def _atom_coloring_metric_ids(registry):
    """Metric ids that can color atoms: per-atom (``N_atoms``) values directly,
    or per-element (``N_elements``) values broadcast onto atoms by element.

    A metric Shape is a tuple of ``Dim`` objects, so it resolves to ``"N_atoms"``
    / ``"N_elements"``. (The original filter compared the Shape to the literal
    ``"per_structure_per_atom"``, which never matched, so the metric list was
    always empty.) Scalar/per-frame metrics describe whole structures and cannot
    color individual atoms, so they are excluded.
    """
    from ffast.metrics.dims import shape_to_str
    ids = []
    for mid in sorted(registry.list_metrics()):
        try:
            schema, _ = registry.get(mid)
        except KeyError:
            continue
        if shape_to_str(schema.shape) in _COLORABLE_SHAPES:
            ids.append(mid)
    return ids


def _resolve_color_source(label, source_map):
    """Map a "Coloring" combo label → the server-side atom color source string.

    Single source of truth for value-driven coloring (ADR 0016). Each label maps
    to exactly one source, so selectors can never clobber one another. Metric
    colorings appear by their display name (``schema.label``) and map directly to
    ``metric:<id>``; there is no separate generic "Metric" entry or raw-id list.
    """
    return source_map.get(label, "element")


def _ensure_prediction(loupe):
    """Attach an available prediction to the view if none is set.

    Metric coloring sources (force error, etc.) need ``prediction.forces``; with
    no prediction on the view the server silently falls back to element colors.
    Pick the first model that has a cached force prediction for the current
    dataset so prediction-dependent coloring has data to compute from.
    """
    settings = loupe.settings
    if settings.get("scenePredictionRef"):
        return
    try:
        from ffast.renderers.vispy.local_scene import available_prediction_refs
        refs = available_prediction_refs(loupe.env, loupe.selectedDatasetKey)
    except Exception:
        refs = []
    if refs:
        settings.setParameter("scenePredictionRef", refs[0], refresh=True)


def _apply_coloring_selection(loupe):
    """The one handler that turns the Coloring selection into ``atomColorSource``.

    Other modules (force error, displacement) contribute label→source entries to
    ``loupe._colorSourceByLabel`` instead of registering their own competing
    handlers.
    """
    settings = loupe.settings
    label = settings.get("atomColorType")
    source = _resolve_color_source(label, loupe._colorSourceByLabel)
    hook = loupe._colorSourceHooks.get(label)
    if hook is not None:
        hook(loupe)
    # A metric source needs a prediction on the view; attach one if the user (or
    # a hook) hasn't already, otherwise the server falls back to element colors.
    if source.startswith("metric:"):
        _ensure_prediction(loupe)
    settings.setParameter("atomColorSource", source, refresh=True)


def addSettings(UIHandler, loupe):
    settings = loupe.settings
    settings.addParameters(**{
        "atomColorType": ["Elements"],   # combo selection (UI state)
        "scenePredictionRef": ["", "applyScenePrediction"],
        "atomColorSource": ["element", "applyColorSource"],
        "atomColorMap": ["viridis", "applyColormap"],
    })
    settings.markAsPerDataset("scenePredictionRef")

    # Single source of truth for the Coloring combo (ADR 0016). Modules register
    # their labels here; ``_applyColoring`` is the only handler that writes
    # atomColorSource, so selectors no longer clobber each other.
    loupe._colorSourceByLabel = {"Elements": "element"}
    loupe._colorSourceHooks = {}
    loupe._colorLabelToMetricId = {}   # metric coloring labels → metric id
    loupe._applyColoring = partial(_apply_coloring_selection, loupe)
    settings.addParameterActions("atomColorType", loupe._applyColoring)


def addSettingsPane(UIHandler, loupe):
    from UI.Templates import SettingsPane, ComboBox
    from PySide6 import QtWidgets

    settings = loupe.settings
    pane = SettingsPane(UIHandler, settings, parent=loupe)

    pane.addSetting(
        "ComboBox",
        "Coloring",
        settingsKey="atomColorType",
        items=["Elements"],
        labelWidth=60,
    )

    # ADR 0040: Colormap + Prediction are meaningless for element coloring, so
    # hide them until Coloring leaves "Elements".
    def _colorIsElements():
        return settings.get("atomColorType") == "Elements"

    # Prediction selector (dynamic model list)
    row = QtWidgets.QWidget(pane)
    row_layout = QtWidgets.QHBoxLayout(row)
    row_layout.setContentsMargins(0, 0, 0, 0)
    lbl = QtWidgets.QLabel("Prediction", row)
    lbl.setFixedWidth(140)
    row_layout.addWidget(lbl)
    row_layout.addStretch()
    loupe.predictionComboBox = ComboBox(parent=row)
    loupe.predictionComboBox.setMinimumWidth(142)
    loupe.predictionComboBox.currentIndexChanged.connect(loupe.onPredictionComboChanged)
    row_layout.addWidget(loupe.predictionComboBox)
    loupe._predictionComboRefs = []
    loupe._predictionComboUpdating = False
    pane.layout.addWidget(row)
    loupe.updatePredictionComboBox()

    colormap = pane.addSetting(
        "ComboBox",
        "Colormap",
        settingsKey="atomColorMap",
        items=["viridis", "inferno", "plasma", "coolwarm", "hot", "bwr", "force_error"],
        labelWidth=60,
    )
    colormap.setHideCondition(_colorIsElements)  # refreshed via pane.updateVisibilities

    # The prediction row is a raw widget (not a SettingsPane control), so drive
    # its visibility directly off the Coloring value.
    def _syncPredictionRow():
        row.setVisible(not _colorIsElements())
    settings.addParameterActions("atomColorType", _syncPredictionRow)
    _syncPredictionRow()

    loupe.addSidebarPane("COLOR BY", pane)


def metric_color_label(entry):
    """Display name for a metric catalog entry — its label, or the id."""
    return entry.get("label") or entry["id"]


def _colorable_metric_entries(loupe):
    """Atom-colorable metric entries from the server's catalog (ADR 0016).

    The server owns the registry; the client builds metric controls from the
    catalog it received (``env.remote.metricCatalog``), so config-loaded external
    metrics appear too. Falls back to the local built-in registry only until the
    catalog arrives. Each entry is ``{id, label, shape, parameters}``.
    """
    catalog = getattr(loupe.env, "metricCatalog", None)
    if not catalog:
        try:
            import ffast.metrics.builtin  # noqa: F401 — register built-ins
            from ffast.metrics.catalog import build_metric_catalog
            from ffast.metrics.registry import _default_registry
            catalog = {e["id"]: e for e in build_metric_catalog(_default_registry)}
        except Exception:
            catalog = {}
    entries = catalog.values() if isinstance(catalog, dict) else catalog
    return [e for e in entries if e.get("shape") in _COLORABLE_SHAPES]


def addMetricControls(UIHandler, loupe):
    """Populate the Coloring combo with metric display names from the server
    catalog, plus parameter controls for the selected metric coloring."""
    from PySide6 import QtWidgets

    pane = loupe.getSettingsPane("COLOR BY")
    settings = loupe.settings
    coloring_combo = pane.settingsWidgets.get("Coloring")

    loupe._metricParamsById = {}   # metric id → parameters dict (from catalog)

    # Parameter controls for the selected metric (shown only for metric colorings).
    container = pane.addSetting("Container", "Metric Controls", layout="vertical")
    container.setHideCondition(
        lambda: settings.get("atomColorType") not in loupe._colorLabelToMetricId
    )
    param_container = QtWidgets.QWidget(container)
    param_layout = QtWidgets.QVBoxLayout(param_container)
    param_layout.setContentsMargins(0, 0, 0, 0)
    container.layout.addWidget(param_container)
    loupe._metricParamContainer = param_container
    loupe._metricParamLayout = param_layout

    def _add_metric_entries():
        """Add colorable metrics not yet present to the combo + maps. Additive
        and idempotent, so it is safe to re-run when the catalog updates (e.g.
        external metrics arriving after connect)."""
        for entry in _colorable_metric_entries(loupe):
            mid = entry["id"]
            if mid in loupe._metricParamsById:
                continue
            label = metric_color_label(entry)
            if coloring_combo is not None:
                coloring_combo.addItems([label])
            loupe._colorSourceByLabel[label] = f"metric:{mid}"
            loupe._colorLabelToMetricId[label] = mid
            loupe._metricParamsById[mid] = entry.get("parameters", {})

    def _rebuild_for_selection():
        mid = loupe._colorLabelToMetricId.get(settings.get("atomColorType"))
        if mid is not None:
            _rebuild_param_controls(
                loupe, loupe._metricParamsById.get(mid, {}), param_container, param_layout
            )

    _add_metric_entries()
    settings.addParameterActions("atomColorType", _rebuild_for_selection)
    _rebuild_for_selection()

    # Rebuild when the server catalog arrives or changes (external metrics, etc.).
    loupe.eventSubscribe("METRIC_CATALOG_UPDATED", _add_metric_entries)


def _rebuild_param_controls(loupe, params, container, layout):
    """Clear and regenerate Qt controls from a metric's parameter schema (as
    plain dicts from the server catalog).

    Each control change is sent to the server as a SET_PARAMETER on the
    ffast.atom_color stage (the metric is computed server-side, ADR 0016), so the
    parameter actually affects the produced colors.
    """
    from PySide6 import QtWidgets

    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    for key, param in (params or {}).items():
        ptype = param.get("type")
        row = QtWidgets.QWidget(container)
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)

        label = QtWidgets.QLabel(key, row)
        label.setFixedWidth(55)
        row_layout.addWidget(label)

        if ptype == "choice":
            widget = QtWidgets.QComboBox(row)
            widget.addItems(param.get("choices", []))
            widget.setCurrentText(str(param.get("default")))
            def on_choice_changed(text, k=key):
                loupe._setColorParam(k, text)
            widget.currentTextChanged.connect(on_choice_changed)

        elif ptype == "float":
            widget = QtWidgets.QDoubleSpinBox(row)
            widget.setDecimals(4)
            if param.get("min") is not None:
                widget.setMinimum(param["min"])
            if param.get("max") is not None:
                widget.setMaximum(param["max"])
            widget.setValue(param.get("default") or 0.0)
            def on_float_changed(value, k=key):
                loupe._setColorParam(k, float(value))
            widget.valueChanged.connect(on_float_changed)

        elif ptype == "bool":
            widget = QtWidgets.QCheckBox(row)
            widget.setChecked(bool(param.get("default")))
            def on_bool_changed(state, k=key):
                loupe._setColorParam(k, bool(state))
            widget.stateChanged.connect(on_bool_changed)

        else:
            continue

        row_layout.addWidget(widget)
        layout.addWidget(row)


def loadLoupe(UIHandler, loupe):

    addSettings(UIHandler, loupe)
    addSettingsPane(UIHandler, loupe)
    addMetricControls(UIHandler, loupe)


CLIENT_FEATURES = [ClientFeature(widget_factory=loadLoupe)]

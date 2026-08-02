"""Panel engine — config-driven 2D Panels (ADR 0021, Phase 1).

A **Panel** is a **Panel Kind** (timeline / density / scatter / table) bound to
**Metric IDs**. Panels never compute: they read Metric Results from
``getWatchedData()`` / the metric cache and draw. Every reduction (smoothing,
KDE, downsampling) lives in a **Transform Metric**
(``ffast/metrics/builtin/transform_metrics.py``), so a Panel only ever assigns
already-computed arrays to axes.

The engine is one generic plot widget (:class:`MetricPlotPanel`) and one generic
table widget (:class:`MetricTablePanel`) parameterised by a :class:`PanelKind`
strategy. New chart archetypes are added by registering a new ``PanelKind`` — no
new widget code.

Phase-1 scope: the engine + four kinds + the catalog + the Python
:func:`add_panel` API. Deferred by design:

* **Subbing** for density/scatter (Phase 3) — only the trivial timeline
  inverse-map is wired here; density/scatter declare ``subbable = False``.
* **Auto-generated controls** from Parameter Schemas (Phase 2).
* The ``{metric, transform, params}`` → Transform Metric **compiler** and the
  **TOML** schema (Phase 5). This module takes already-named metric IDs.
"""
import logging

import numpy as np
from PySide6 import QtCore, QtWidgets

from ffast.config.user import getConfig
from UI.Plots import BasicPlotWidget, Table
from UI.Templates import Slider

logger = logging.getLogger("FFAST")


# --------------------------------------------------------------------------- #
# Metric access helpers
# --------------------------------------------------------------------------- #
def _metric_value(env, metric_id, model, dataset, params=None):
    """Cached MetricResult values for (model, dataset), or None (ADR 0019)."""
    key = env.data.make_metric_cache_key(metric_id, params or {}, model, dataset)
    result = env.data.getCacheByKey(key, subChecks=False)
    return None if result is None else result.values


def _resolve_unit(unit):
    """A unit spec is a userConfig key (e.g. ``"energyUnit"``) or a literal."""
    if unit is None:
        return None
    try:
        resolved = getConfig(unit)
    except Exception:
        return unit
    return resolved if resolved is not None else unit


def _set_axis(panel, axis, value):
    """``value`` is ``None`` | ``"label"`` | ``("label", unit_key)``."""
    if value is None:
        return
    if isinstance(value, (tuple, list)):
        label, unit = value[0], _resolve_unit(value[1])
    else:
        label, unit = value, None
    (panel.setXLabel if axis == "x" else panel.setYLabel)(label, unit)


def _compute_params(metric_id):
    """[(name, ParameterSchema)] for the ``role == "compute"`` params of a Metric.

    These are the tunable knobs a Panel surfaces as controls (Phase 2); presentation
    params are not Panel controls. Returns [] for unknown metrics or none declared."""
    from ffast.metrics.registry import default_registry

    if not default_registry.has(metric_id):
        return []
    schema, _ = default_registry.get(metric_id)
    return [
        (name, p)
        for name, p in schema.parameters.items()
        if getattr(p, "role", None) == "compute"
    ]


def _source_metric(metric_id):
    """The indexed source a Transform Metric reduces (ADR 0021: subbing reads this,
    not the drawn curve). Prefers the input named ``src``; else the first input that
    is itself a registered Metric."""
    from ffast.metrics.registry import default_registry

    if not default_registry.has(metric_id):
        return None
    schema, _ = default_registry.get(metric_id)
    src = schema.inputs.get("src")
    if src and default_registry.has(src):
        return src
    for ref in schema.inputs.values():
        if default_registry.has(ref):
            return ref
    return None


def _named_input(metric_id, key):
    """The registered Metric bound to input ``key`` of ``metric_id``, or None."""
    from ffast.metrics.registry import default_registry

    if not default_registry.has(metric_id):
        return None
    ref = default_registry.get(metric_id)[0].inputs.get(key)
    return ref if (ref and default_registry.has(ref)) else None


def _shape_dim_names(metric_id):
    from ffast.metrics.registry import default_registry

    if not default_registry.has(metric_id):
        return []
    shape = default_registry.get(metric_id)[0].shape
    dims_ = shape if isinstance(shape, tuple) else (shape,)
    return [getattr(d, "name", str(d)) for d in dims_]


def _metric_is_per_atom(metric_id):
    """True if the metric is per-atom/per-component (needs flat→config mapping for
    subbing) rather than per-frame (frame indices map directly)."""
    return "N_atoms" in _shape_dim_names(metric_id)


def _flat_to_config(flat_indices, dataset):
    """Map flat force-component indices → configuration indices: variable datasets
    use molecule_offsetsx3, uniform datasets divide by components-per-config."""
    flat_indices = np.asarray(flat_indices)
    if getattr(dataset, "isVariable", False):
        offsets = np.asarray(dataset.molecule_offsets) * 3  # 3 components / atom
        return np.unique(np.searchsorted(offsets[1:], flat_indices, side="right"))
    n_atoms = dataset.getNAtoms()
    return np.unique(flat_indices // (n_atoms * 3))


# --------------------------------------------------------------------------- #
# Panel Kind contract
# --------------------------------------------------------------------------- #
class PanelKind:
    """Strategy describing one archetype: its widget, the metric roles it binds,
    how it draws a series, and (plots only) how a viewport maps back to indices.

    ``roles`` are the spec keys that must each name a Metric ID. ``shapes`` maps
    each role to the Metric Shape it expects (documentation + future validation;
    shape-checking against the registry is a later enhancement)."""

    name = None
    widget = "plot"          # "plot" | "table"
    subbable = False
    roles = ()
    shapes = {}
    x_default = None
    y_default = None

    def bound_metrics(self, spec):
        """Ordered Metric IDs this Panel binds (one per role)."""
        return [spec[role] for role in self.roles]

    def subbing_sources(self, spec):
        """Extra indexed-source Metric IDs to bind so subbing can read them.

        Default none. A kind whose drawn metric loses the per-frame index (density)
        returns the source it reduces so it is computed/cached alongside the curve."""
        return []

    def metric_dependencies(self, spec, param_values=None):
        """{metric_id: params} the DataWatcher tracks — params scoped *per metric*.

        Each metric's cache key folds in only *its own* compute params, so a param
        is attached to a metric only if that metric declares it. Values equal to the
        schema default are pruned, so the default state yields ``{}`` (shares the
        cache key with any plain consumer of the same metric). Subbable kinds also
        bind their indexed sources so the inverse-map can read them from cache."""
        param_values = param_values or {}
        metric_ids = list(self.bound_metrics(spec))
        if self.subbable:
            for src in self.subbing_sources(spec):
                if src and src not in metric_ids:
                    metric_ids.append(src)
        deps = {}
        for mid in metric_ids:
            params = {}
            for pname, pschema in _compute_params(mid):
                val = param_values.get((mid, pname), pschema.default)
                if val != pschema.default:
                    params[pname] = val
            deps[mid] = params
        return deps

    def validate(self, spec):
        missing = [r for r in self.roles if r not in spec]
        if missing:
            raise ValueError(
                f"Panel kind '{self.name}' is missing metric role(s) {missing}; "
                f"got spec keys {sorted(spec)}"
            )

    def primary_metric(self, spec):
        return spec[self.roles[0]] if self.roles else self.name

    # plot kinds override these -------------------------------------------- #
    def apply_labels(self, panel, spec):
        _set_axis(panel, "x", spec.get("x_label", self.x_default))
        _set_axis(panel, "y", spec.get("y_label", self.y_default))

    def draw(self, panel, data, spec):
        """Draw one watched-data entry (one modelxdataset series)."""
        raise NotImplementedError

    def draw_overlay(self, panel, data_list, spec):
        """Draw once after all series (e.g. a scatter diagonal). Default: nothing."""
        return None

    def sub_indices(self, panel, dataset, model, spec):
        """Viewport → parent-dataset configuration indices, or None (no subbing)."""
        return None


# --------------------------------------------------------------------------- #
# Generic widgets
# --------------------------------------------------------------------------- #
class _ParamControls:
    """Mixin: auto-build controls from bound Metrics' compute Parameter Schemas and
    route changes through a debounced recompute (ADR 0021, Phase 2).

    A control change stores the new per-(metric, param) value and restarts a debounce
    timer; on timeout the Panel re-declares its metric dependencies (new params → new
    cache key) and queues the recompute. ``DATA_UPDATED`` then redraws. The Panel Kind
    declares no controls itself — they come entirely from the Metrics it binds, so a
    new tunable appears by adding a param to a Metric's schema (zero Panel code)."""

    _DEBOUNCE_MS = 200

    def _initParams(self):
        self._param_values = {}            # {(metric_id, param_name): value}
        overrides = self._spec.get("params") or {}
        for mid in self._kind.bound_metrics(self._spec):
            for pname, _pschema in _compute_params(mid):
                if pname in overrides:
                    self._param_values[(mid, pname)] = overrides[pname]
        self._debounce = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self._DEBOUNCE_MS)
        self._debounce.timeout.connect(self._applyParams)

    def _currentDeps(self):
        return self._kind.metric_dependencies(self._spec, self._param_values)

    def _onParamChanged(self, metric_id, pname, value):
        self._param_values[(metric_id, pname)] = value
        self._debounce.start()            # coalesce rapid changes (slider drags)

    def _applyParams(self):
        self.setMetricDependencies(self._currentDeps())
        self.dataWatcher.loadContent()    # queue any now-missing (param-keyed) results

    def _paramTargets(self, pname):
        """Bound/source Metric IDs of this Panel that declare compute param ``pname``."""
        targets = list(self._kind.bound_metrics(self._spec))
        if self._kind.subbable:
            targets += self._kind.subbing_sources(self._spec)
        return [m for m in targets if any(n == pname for n, _ in _compute_params(m))]

    def hasParam(self, pname):
        """True if any bound/source Metric declares compute param ``pname`` — lets a
        tab-level control pick out only the Panels it actually drives."""
        return bool(self._paramTargets(pname))

    def setSharedParam(self, pname, value):
        """Set a compute param on every bound/source Metric that declares it, and
        recompute immediately. Lets a tab-level control (e.g. the energy-shift
        checkbox) drive a parameter shared across several Panels. Returns whether
        any Metric declared the param (so callers can skip unaffected Panels)."""
        targets = self._paramTargets(pname)
        for mid in targets:
            self._param_values[(mid, pname)] = value
        if targets:
            self._applyParams()
        return bool(targets)

    def _addControl(self, widget):
        # Both BasicPlotWidget and Table own an optionsToolbar (left-aligned, stretch
        # at the end), so inserting at 0 keeps controls left of the stretch.
        self.optionsToolbar.layout.insertWidget(0, widget)

    def _buildParamControls(self):
        hidden = set(self._spec.get("hidden_params", ()))  # params driven externally
        for mid in self._kind.bound_metrics(self._spec):
            for pname, pschema in _compute_params(mid):
                if pname in hidden:
                    continue
                control = self._makeControl(mid, pname, pschema)
                if control is not None:
                    self._addControl(control)

    def _makeControl(self, mid, pname, pschema):
        label = pschema.label or pname
        current = self._param_values.get((mid, pname), pschema.default)
        ptype = pschema.type

        if ptype == "int":
            s = Slider(
                parent=self, hasEditBox=True, label=label,
                nMin=pschema.min if pschema.min is not None else 0,
                nMax=pschema.max if pschema.max is not None else 99999,
            )
            s.setValue(int(current), quiet=True)
            s.setCallbackFunc(
                lambda v, m=mid, p=pname: self._onParamChanged(m, p, int(v))
            )
            return s

        if ptype == "float":
            box = QtWidgets.QDoubleSpinBox(self)
            if pschema.min is not None:
                box.setMinimum(pschema.min)
            if pschema.max is not None:
                box.setMaximum(pschema.max)
            box.setValue(float(current))
            box.valueChanged.connect(
                lambda v, m=mid, p=pname: self._onParamChanged(m, p, float(v))
            )
            return self._labeled(label, box)

        if ptype == "choice":
            combo = QtWidgets.QComboBox(self)
            combo.addItems([str(c) for c in pschema.choices])
            combo.setCurrentText(str(current))
            combo.currentTextChanged.connect(
                lambda v, m=mid, p=pname: self._onParamChanged(m, p, v)
            )
            return self._labeled(label, combo)

        if ptype == "bool":
            cb = QtWidgets.QCheckBox(label, self)
            cb.setChecked(bool(current))
            cb.stateChanged.connect(
                lambda st, m=mid, p=pname: self._onParamChanged(m, p, bool(st))
            )
            return cb

        logger.warning(
            "panels: no control widget for param type '%s' (%s.%s)", ptype, mid, pname
        )
        return None

    def _labeled(self, label, widget):
        box = QtWidgets.QWidget(self)
        lay = QtWidgets.QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(QtWidgets.QLabel(label))
        lay.addWidget(widget)
        return box


class MetricPlotPanel(_ParamControls, BasicPlotWidget):
    """Generic plot Panel; all chart behaviour is delegated to its PanelKind."""

    def __init__(self, handler, kind, spec, **kwargs):
        self._kind = kind
        self._spec = spec
        # Analysis Tab name, stashed onto the ContentTab by UIHandler.addContentTab
        # -- read before super().__init__ reparents this widget under it, since a
        # Panel Display Override's identity (ADR 0029) needs it at apply_labels time.
        tab_name = getattr(kwargs.get("parent"), "tabName", "") or ""
        super().__init__(
            handler,
            name=spec["name"],
            title=spec.get("title", spec["name"]),
            isSubbable=kind.subbable,
            hasLegend=spec.get("legend", True),
            **kwargs,
        )
        self._initParams()
        self.setMetricDependencies(self._currentDeps())
        kind.apply_labels(self, spec)
        self._displayOverrideKey = (tab_name, kind.name, kind.bound_metrics(spec))
        self._loadDisplayOverride()
        self._buildParamControls()
        tooltip = spec.get("tooltip")
        if tooltip:
            self.infoButton.setToolTip(tooltip)

    def addPlots(self):
        data_list = self.getWatchedData()
        for data in data_list:
            self._kind.draw(self, data, self._spec)
        self._kind.draw_overlay(self, data_list, self._spec)

    def getDatasetSubIndices(self, dataset, model):
        idx = self._kind.sub_indices(self, dataset, model, self._spec)
        return np.array([], dtype=int) if idx is None else idx


class MetricTablePanel(_ParamControls, Table):
    """Generic table Panel: a scalar Metric over the modelxdataset grid."""

    def __init__(self, handler, kind, spec, **kwargs):
        self._kind = kind
        self._spec = spec
        super().__init__(
            handler,
            name=spec["name"],
            title=spec.get("title", spec["name"]),
            isSubbable=False,
            **kwargs,
        )
        self._initParams()
        self.setMetricDependencies(self._currentDeps())
        self._buildParamControls()

    # Table rendering is delegated to the PanelKind, mirroring how plot kinds own
    # draw/sub_indices — so a bespoke table (e.g. atomic_table) overrides these
    # without a new widget class.
    def getSize(self):
        return self._kind.table_size(self)

    def getLeftHeader(self, i):
        return self._kind.table_left_header(self, i)

    def getTopHeader(self, j):
        return self._kind.table_top_header(self, j)

    def getValue(self, i, j):
        return self._kind.table_value(self, i, j)


# --------------------------------------------------------------------------- #
# Built-in Panel Kinds
# --------------------------------------------------------------------------- #
import ffast.metrics.dims as dims  # noqa: E402  (after BasicPlotWidget import block)


class TimelineKind(PanelKind):
    """X = configuration index, Y = a per-frame Metric. Subbable (x-range → frames)."""

    name = "timeline"
    subbable = True
    roles = ("y",)
    shapes = {"y": dims.N_frames}
    x_default = "Configuration index"

    def draw(self, panel, data, spec):
        y = np.asarray(data["dataEntry"][spec["y"]].values).ravel()
        # antialias=False: a timeline is a long per-frame signal; zoomed in, its
        # off-screen points reach million-pixel device coords and AA coverage
        # math over segments that large is what stalls the software raster
        # (~180ms/paint). The signal is dense/noisy so aliasing is not visible,
        # and dropping AA removes the stall without changing what you see.
        panel.plot(
            np.arange(y.shape[0]), y, autoColor=data, autoLabel=data,
            antialias=False,
        )

    def sub_indices(self, panel, dataset, model, spec):
        # Phase 1: drawn-index ≈ frame index. The smoothing-window offset that a
        # Transform-Metric introduces (valid convolution shortens the series) is
        # corrected in Phase 3.
        (x0, x1), _ = panel.getRanges()
        n = dataset.getN()
        return np.arange(max(0, int(x0)), min(n, int(x1) + 1))


class DensityKind(PanelKind):
    """One density Transform Metric → a (2, G) curve (row 0 x, row 1 density)."""

    name = "density"
    subbable = True
    roles = ("value",)
    shapes = {"value": (dims.curve_xy, dims.grid)}
    y_default = "Density"

    def draw(self, panel, data, spec):
        v = np.asarray(data["dataEntry"][spec["value"]].values)
        panel.plot(v[0], v[1], autoColor=data, autoLabel=data)

    def subbing_sources(self, spec):
        # The density curve has no per-frame index; subbing reads the per-frame
        # source the curve was reduced from, so bind it too.
        src = _source_metric(spec["value"])
        return [src] if src else []

    def sub_indices(self, panel, dataset, model, spec):
        value_mid = spec["value"]
        src = _source_metric(value_mid)
        if src is None:
            return None
        deps = panel._currentDeps()
        vals = _metric_value(panel.env, src, model, dataset, deps.get(src, {}))
        if vals is None:
            return None
        v = np.asarray(vals).ravel()
        # Match the drawn curve: if this density is shifted, subtract the same
        # offset from the source before filtering (so subbing tracks the shifted x).
        if panel._param_values.get((value_mid, "shifted"), False):
            shift_mid = _named_input(value_mid, "shift")
            sv = _metric_value(panel.env, shift_mid, model, dataset, {}) if shift_mid else None
            if sv is not None and np.asarray(sv).size:
                v = v - float(np.asarray(sv).ravel()[0])
        # mirror-KDE x-axis is |value|; filter the source on the same domain.
        v = np.abs(v)
        (x0, x1), _ = panel.getRanges()
        return np.unique(np.argwhere((v >= x0) & (v <= x1)).ravel())


class ScatterKind(PanelKind):
    """Metric-X vs Metric-Y, index-aligned. Optional ``diagonal`` reference line."""

    name = "scatter"
    subbable = True
    roles = ("x", "y")
    shapes = {"x": dims.N_frames, "y": dims.N_frames}

    def _xy(self, data, spec):
        x = np.asarray(data["dataEntry"][spec["x"]].values).ravel()
        y = np.asarray(data["dataEntry"][spec["y"]].values).ravel()
        return x, y

    def draw(self, panel, data, spec):
        x, y = self._xy(data, spec)
        # Visual-only point cap (Phase 1). Becomes an adaptive LTTB/M4 downsample
        # Transform Metric later; subbing ignores it and filters the full source.
        n = int(getConfig("scatterPlotNPoints"))
        if x.shape[0] > n:
            idx = np.round(np.linspace(0, x.shape[0] - 1, n)).astype(int)
            x, y = x[idx], y[idx]
        panel.plot(x, y, scatter=True, autoColor=data, autoLabel=data)

    def draw_overlay(self, panel, data_list, spec):
        if not spec.get("diagonal") or not data_list:
            return
        xs, ys = [], []
        for data in data_list:
            x, y = self._xy(data, spec)
            if x.size:
                xs.append(x)
                ys.append(y)
        if not xs:
            return
        import pyqtgraph as pg
        from PySide6.QtCore import Qt

        combined = np.concatenate(xs + ys)
        lo, hi = float(combined.min()), float(combined.max())
        pen = pg.mkPen((150, 150, 150), width=1, style=Qt.PenStyle.DashLine)
        panel.plot(np.array([lo, hi]), np.array([lo, hi]), pen=pen)

    def sub_indices(self, panel, dataset, model, spec):
        # Box-filter the FULL indexed source (downsampling is visual-only, so we
        # sub every matching structure, not just the drawn points).
        deps = panel._currentDeps()
        xv = _metric_value(panel.env, spec["x"], model, dataset, deps.get(spec["x"], {}))
        yv = _metric_value(panel.env, spec["y"], model, dataset, deps.get(spec["y"], {}))
        if xv is None or yv is None:
            return None
        (x0, x1), (y0, y1) = panel.getRanges()
        x = np.asarray(xv).ravel()
        y = np.asarray(yv).ravel()
        flat = np.argwhere((x > x0) & (x < x1) & (y > y0) & (y < y1)).ravel()
        # Per-atom/component metrics (force scatter) map flat indices → configs;
        # per-frame metrics (energy scatter) are already config indices.
        if _metric_is_per_atom(spec["x"]):
            return _flat_to_config(flat, dataset)
        return np.unique(flat)


class TableKind(PanelKind):
    """A scalar Metric rendered over the modelxdataset grid.

    The four ``table_*`` methods are the table analog of ``draw``/``sub_indices``:
    :class:`MetricTablePanel` delegates to them, so a bespoke table kind subclasses
    this and overrides them."""

    name = "table"
    widget = "table"
    roles = ("value",)
    shapes = {"value": dims.scalar}

    def table_size(self, panel):
        return (len(panel.getModelDependencies()), len(panel.getDatasetDependencies()))

    def table_left_header(self, panel, i):
        keys = panel.getModelDependencies()
        model = panel.env.models.get(keys[i]) if i < len(keys) else None
        return model.getDisplayName() if model else "?"

    def table_top_header(self, panel, j):
        keys = panel.getDatasetDependencies()
        dataset = panel.env.datasets.get(keys[j]) if j < len(keys) else None
        return dataset.getDisplayName() if dataset else "?"

    def table_value(self, panel, i, j):
        models = panel.getModelDependencies()
        datasets = panel.getDatasetDependencies()
        if i >= len(models) or j >= len(datasets):
            return ""
        model = panel.env.models.get(models[i])
        dataset = panel.env.datasets.get(datasets[j])
        mid = panel._spec["value"]
        params = panel._currentDeps().get(mid, {})
        v = _metric_value(panel.env, mid, model, dataset, params)
        if v is None:
            return ""
        return f"{float(v):.{panel._spec.get('precision', 2)}f}"


# --------------------------------------------------------------------------- #
# Bespoke Panel Kinds (Phase 5 ports) — registered like any other kind, so a
# declarative tab can name them. They are "bespoke" because they read a tab
# selector or reduce inline; the generic engine stays unaware of them.
# --------------------------------------------------------------------------- #
def _element_order(dataset):
    """Sorted unique atomic numbers — the index order of per-element metrics."""
    return list(np.unique(np.asarray(dataset.getElements())))


class GroupedDensityKind(PanelKind):
    """Density distribution split into one curve per group (e.g. per element).

    Binds a Metric of shape ``(N_groups, curve_xy, grid)`` under role ``value``
    (rows ordered by sorted-unique Z = ``_element_order``); an optional
    ``aggregate`` role binds a single ``(curve_xy, grid)`` "All atoms" curve.
    Which groups are drawn — and their colours — come from the tab selector
    (``getSelectedAtomInfo`` → ``{name: {index, color}}``), so the selector is the
    colour/legend channel (the Vega-Lite grouping model): the server computes
    every group's curve once and the Panel just filters + draws. Replaces the
    bespoke inline-reducing ``atomic_density``."""

    name = "grouped_density"
    subbable = False
    roles = ("value",)
    shapes = {"value": (dims.N_elements, dims.curve_xy, dims.grid)}
    y_default = "Density"

    def bound_metrics(self, spec):
        mids = [spec["value"]]
        if spec.get("aggregate"):
            mids.append(spec["aggregate"])
        return mids

    def draw(self, panel, data, spec):
        selector = spec.get("selector_obj")
        atom_types = selector.getSelectedAtomInfo() if selector else {}
        atom_mode = len(atom_types) > 1
        dataset = data["dataset"]
        de = data["dataEntry"]
        order = _element_order(dataset)

        agg_mid = spec.get("aggregate")
        if "All" in atom_types and agg_mid and de.get(agg_mid) is not None:
            agg = np.asarray(de[agg_mid].values)
            if atom_mode:
                panel.plot(agg[0], agg[1], color=atom_types["All"]["color"],
                           autoLabel=data)
            else:
                panel.plot(agg[0], agg[1], autoColor=data, autoLabel=data)

        entry = de.get(spec["value"])
        if entry is None:
            return
        curves = np.asarray(entry.values)  # (N_groups, curve_xy, grid)
        for atom, info in atom_types.items():
            if atom == "All":
                continue
            zi = info["index"]
            if zi not in order:
                continue
            row = order.index(zi)
            if row >= curves.shape[0]:
                continue
            c = curves[row]
            if atom_mode:
                panel.plot(c[0], c[1], color=info["color"], autoLabel=data)
            else:
                panel.plot(c[0], c[1], autoColor=data, autoLabel=data)


class GroupedTableKind(TableKind):
    """Per-group MAE/RMSE table driven by the element picker. Columns bind two
    per-group metrics (roles ``mae``/``rmse``, shape ``(N_groups,)``); optional
    ``mae_all``/``rmse_all`` roles supply the "All atoms" row's values. Single
    selected group → rows are (dataset/model) pairs; multiple → rows are groups.
    Generic over the metric ids (declared in config); replaces the hardcoded
    ``atomic_table``."""

    name = "grouped_table"
    roles = ("mae", "rmse")

    def bound_metrics(self, spec):
        mids = [spec["mae"], spec["rmse"]]
        for r in ("mae_all", "rmse_all"):
            if spec.get(r):
                mids.append(spec[r])
        return mids

    def _atom_info(self, panel):
        selector = panel._spec.get("selector_obj")
        return selector.getSelectedAtomInfo() if selector else {}

    def _pairs(self, panel):
        """(dataset, model) pairs to show as rows in single-group mode — lazy-safe.

        Gated on the bound per-group MAE Metric being computed (its result is
        client-cached even when the raw forces/energy arrays are not, under the
        lazy S4c load flip). The old ``getData(...) is not None`` gate returned
        nothing once arrays stopped being held client-side → empty single-element
        table (the long-standing "table blank for one element" bug)."""
        env = panel.env
        mae = panel._spec["mae"]
        pairs = []
        for d in panel.getDatasetDependencies():
            dataset = env.datasets.get(d)
            for m in panel.getModelDependencies():
                model = env.models.get(m)
                if _metric_value(env, mae, model, dataset) is not None:
                    pairs.append((d, m))
        return pairs

    def table_size(self, panel):
        atom_types = self._atom_info(panel)
        if not atom_types:
            return (0, 2)
        rows = len(atom_types) if len(atom_types) > 1 else len(self._pairs(panel))
        return (rows, 2)

    def table_top_header(self, panel, j):
        label = "MAE" if j == 0 else ("RMSE" if j == 1 else "/")
        # Single-group mode rows are (dataset/model) pairs, so name the group in
        # the column header — otherwise the table shows no clue which element the
        # error belongs to. Multi-group mode rows ARE the groups (left header).
        atom_types = self._atom_info(panel)
        if len(atom_types) == 1:
            return f"{list(atom_types.keys())[0]} {label}"
        return label

    def table_left_header(self, panel, i):
        atom_types = self._atom_info(panel)
        if len(atom_types) > 1:
            return list(atom_types.keys())[i]
        pairs = self._pairs(panel)
        if i >= len(pairs):
            return "/"
        dkey, mkey = pairs[i]
        dataset = panel.env.datasets.get(dkey)
        model = panel.env.models.get(mkey)
        ds = dataset.getDisplayName() if dataset else dkey
        mn = model.getDisplayName() if model else mkey
        return f"{ds} / {mn}" if len(panel.getDatasetDependencies()) > 1 else mn

    def table_value(self, panel, i, j):
        env = panel.env
        infos = self._atom_info(panel)
        atom_types = list(infos.keys())
        if not atom_types:
            return ""
        if len(atom_types) > 1:
            datasets = panel.getDatasetDependencies()
            models = panel.getModelDependencies()
            if not datasets or not models:
                return ""
            dkey, mkey, atom = datasets[0], models[0], atom_types[i]
        else:
            pairs = self._pairs(panel)
            if i >= len(pairs):
                return ""
            dkey, mkey, atom = pairs[i][0], pairs[i][1], atom_types[0]

        model = env.models.get(mkey)
        dataset = env.datasets.get(dkey)
        spec = panel._spec
        if atom == "All":
            mid = spec.get("mae_all") if j == 0 else spec.get("rmse_all")
            if mid is None:
                return ""
            v = _metric_value(env, mid, model, dataset)
            return "" if v is None else f"{float(v):.2f}"

        mid = spec["mae"] if j == 0 else spec["rmse"]
        vals = _metric_value(env, mid, model, dataset)
        if vals is None:
            return ""
        order = _element_order(dataset)
        zi = infos[atom]["index"]
        if zi not in order:
            return ""
        return f"{float(vals[order.index(zi)]):.2f}"


class OverlayTimelineKind(PanelKind):
    """Several per-frame series on one axis, each min-subtracted + peak-normalised
    in-draw (visual, smoothing-window-dependent — like the legacy gyradius overlay).
    Binds a list of (smoothed) series metrics under role ``series``; one shared
    Smoothing control drives every series' hidden ``window`` param."""

    name = "overlay_timeline"
    subbable = True
    roles = ()
    x_default = "Configuration index"

    def bound_metrics(self, spec):
        return list(spec.get("series", []))

    def draw(self, panel, data, spec):
        series = spec.get("series", [])
        labels = spec.get("series_labels", [])
        de = data["dataEntry"]
        for i, mid in enumerate(series):
            res = de.get(mid)
            if res is None:
                continue
            y = np.asarray(res.values, dtype=float).ravel()
            if y.size:
                y = y - np.min(y)
                peak = np.max(y)
                if peak > 0:
                    y = y / peak
            # antialias=False: same as TimelineKind -- a long per-frame signal
            # whose off-screen points reach huge device coords when zoomed;
            # AA-stroking those stalls the software raster, and the dense signal
            # hides the aliasing.
            kwargs = {"autoLabel": data, "antialias": False}
            if i < len(labels):
                kwargs["label"] = labels[i]
            if i == 0:
                kwargs["autoColor"] = data  # first series colours per (model,dataset)
            panel.plot(np.arange(y.shape[0]), np.abs(y), **kwargs)

    def sub_indices(self, panel, dataset, model, spec):
        (x0, x1), _ = panel.getRanges()
        n = dataset.getN()
        return np.arange(max(0, int(x0)), min(n, int(x1) + 1))


# --------------------------------------------------------------------------- #
# Catalog + public API
# --------------------------------------------------------------------------- #
PANEL_KINDS = {}


def register_panel_kind(kind):
    """Register a Panel Kind in the catalog (the 2D analog of the Stage Catalog)."""
    PANEL_KINDS[kind.name] = kind
    return kind


for _kind in (TimelineKind(), DensityKind(), ScatterKind(), TableKind(),
              GroupedDensityKind(), GroupedTableKind(), OverlayTimelineKind()):
    register_panel_kind(_kind)


def make_panel(handler, kind_name, parent=None, **spec):
    """Build a Panel widget from a kind name + a declarative spec.

    spec keys: the kind's metric roles (e.g. ``y=`` / ``x=,y=`` / ``value=``),
    optional ``name`` / ``title`` / ``tooltip`` / ``legend`` / ``params`` /
    ``x_label`` / ``y_label`` (label or ``(label, unit_key)``) / ``precision``
    (table) / ``diagonal`` (scatter)."""
    kind = PANEL_KINDS.get(kind_name)
    if kind is None:
        raise ValueError(
            f"Unknown panel kind '{kind_name}'. Known kinds: {sorted(PANEL_KINDS)}"
        )
    kind.validate(spec)
    spec.setdefault("name", f"{kind_name}:{kind.primary_metric(spec)}")
    if kind.widget == "table":
        return MetricTablePanel(handler, kind, spec, parent=parent)
    return MetricPlotPanel(handler, kind, spec, parent=parent)


def add_panel(ct, kind_name, row, col, rowspan=1, colspan=1, **spec):
    """Build a Panel and place it in a ContentTab's grid, wired to data selection.

    Returns the widget so callers can attach extra options if needed."""
    panel = make_panel(ct.handler, kind_name, parent=ct, **spec)
    ct.addWidget(panel, row, col, rowspan, colspan)
    if getattr(ct, "dataSelector", None) is not None:
        ct.addDataSelectionCallback(panel.setModelDatasetDependencies)
    else:
        logger.warning(
            "add_panel: ContentTab has no data selector; '%s' won't auto-refresh "
            "on selection (call setModelDatasetDependencies manually).",
            spec.get("name", kind_name),
        )
    return panel

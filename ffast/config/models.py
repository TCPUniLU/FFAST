from __future__ import annotations
from typing import Any, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator

class MetricModuleConfig(BaseModel):
    """An external metric module, loaded by either a file path or a Python import path.

    Exactly one of ``path`` (a file relative to the declaring config) or
    ``import_path`` (a dotted module name resolved on ``sys.path``) must be set.
    """
    model_config = ConfigDict(extra="forbid")
    path: str | None = None
    import_path: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "MetricModuleConfig":
        if (self.path is None) == (self.import_path is None):
            raise ValueError("metric module requires exactly one of 'path' or 'import_path'")
        return self

class FieldMetricConfig(BaseModel):
    """A Dataset Field exposed as a passthrough Metric (ADR 0023).

    Compiles ``ref`` (e.g. ``reference.atoms.charges``) into a registered
    passthrough metric ``id``; the Metric Shape is inferred from the field kind
    (``atoms`` → per-atom, ``info`` → per-frame). No Python required.
    """
    model_config = ConfigDict(extra="forbid")
    id: str
    ref: str
    label: str = ""
    unit: str = ""

    @model_validator(mode="after")
    def _valid(self) -> "FieldMetricConfig":
        from ffast.metrics.inputs import is_field_ref
        if "." not in self.id:
            raise ValueError(f"field metric id '{self.id}' must be namespaced (contain a dot)")
        if not is_field_ref(self.ref):
            raise ValueError(
                f"field metric ref '{self.ref}' must be "
                "{reference,prediction}.{info,atoms}.<key>"
            )
        return self


class ExprMetricConfig(BaseModel):
    """An Expression Metric: element-wise algebra over refs, no Python (ADR 0042).

    ``expr`` is an element-wise arithmetic string over the named ``vars`` (each
    binding an Expression Variable to a raw ref, a **Dataset Field**, or a
    registered **Metric ID**); ``compile_expr_metric`` registers the result as an
    ordinary Metric under ``id``. The reserved variable ``n_atoms`` (a
    per-structure atom count) is auto-provided, so a ``vars`` key named
    ``n_atoms`` is a Configuration Failure. The whitelist, same-shape rule, and
    AST validation are enforced by the compiler at config-load.
    """
    model_config = ConfigDict(extra="forbid")
    id: str
    expr: str
    vars: dict[str, str] = Field(default_factory=dict)
    label: str = ""
    unit: str = ""

    @model_validator(mode="after")
    def _valid(self) -> "ExprMetricConfig":
        if "." not in self.id:
            raise ValueError(f"expr metric id '{self.id}' must be namespaced (contain a dot)")
        if "n_atoms" in self.vars:
            raise ValueError(
                f"expr metric '{self.id}': 'n_atoms' is a reserved Expression "
                f"Variable (auto-provided per-structure atom count) — rename it"
            )
        return self


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modules: list[MetricModuleConfig] = Field(default_factory=list)
    fields: list[FieldMetricConfig] = Field(default_factory=list)
    expr: list[ExprMetricConfig] = Field(default_factory=list)

class AtomColorPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_id: str
    colormap: str = "viridis"
    vmin: float | None = None
    vmax: float | None = None


class PanelMetricRef(BaseModel):
    """An Analysis Panel's bound metric, in the compiler's authoring form
    (ADR 0021). ``transform`` is a single transform name, a pipeline of names, or
    null for a raw metric; ``params`` are *identity* params folded into the
    compiled id (compute params come from the transform schema, not here)."""
    model_config = ConfigDict(extra="forbid")
    metric: str
    transform: Union[str, list[str], None] = None
    params: dict[str, Any] = Field(default_factory=dict)


# The panel kind of a 3D panel (ADR 0056): a view, not a plot.
PANEL_KIND_3D = "3d"


class StartSettings(BaseModel):
    """How an independent 3D panel looks when it opens (ADR 0056 rule 13): its
    colouring, display, bonds and force arrows. Never which dataset or
    prediction it shows, nor its camera. Colouring by a metric and force
    arrows use the panel's own prediction. A key left out keeps today's
    default. If ``colour_by`` names a metric the server lacks, the browser
    shows element colours and says why."""
    model_config = ConfigDict(extra="forbid")
    # "element", "displacement", or a per-atom metric id ("ffast.force_mae").
    colour_by: str | None = None
    colormap: str | None = None
    atom_size: float | None = Field(default=None, gt=0)
    bond_width: int | None = Field(default=None, ge=10, le=100)   # percent
    bond_colour: str | None = Field(
        default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    force_arrows: bool | None = None
    force_length: float | None = Field(default=None, ge=1, le=200)
    force_normalised: bool | None = None


# An axis label is null | "label" | ["label", "<userConfig unit key>"].
AxisLabel = Union[str, list[str], None]
# A panel role binds one metric, or (overlay kinds) a list of series.
PanelRole = Union[PanelMetricRef, list[PanelMetricRef]]


class PanelConfig(BaseModel):
    """One Panel: a Panel Kind placed in the tab grid, binding metric roles.

    ``kind`` names a registered Panel Kind (timeline / density / scatter / table
    / bespoke). ``metrics`` maps each of that kind's roles to a metric ref. The
    renderer-side keys (``x_label`` … ``options``) pass straight through to the
    engine spec; ``controls`` names panel-level control widgets and the server
    ignores everything but the metric refs (server stays plot-ignorant).

    ``scroll_group`` is a layout-only hint: panels sharing a non-null group name
    are placed together in one horizontal scroll strip (the table row of the
    legacy error tabs) at the *first* member's ``row``/``col``/span, instead of
    each taking its own grid cell.

    ``kind = "3d"`` is a **3D panel** (ADR 0056): a cell showing a visualization
    view instead of plotting metrics. Browser only; it binds no metric roles.
    It is ``view = "linked"`` (the default: it shows the main view) or
    ``"independent"``; an independent panel also has ``link_frame`` and
    ``link_camera`` (follow the main view's frame and camera; both on by
    default) and its starting look, ``start``. A linked panel stores only its
    place."""
    model_config = ConfigDict(extra="forbid")
    kind: str
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    title: str | None = None
    tooltip: str | None = None
    legend: bool = True
    metrics: dict[str, PanelRole] = Field(default_factory=dict)
    x_label: AxisLabel = None
    y_label: AxisLabel = None
    diagonal: bool = False
    precision: int = 2
    hidden_params: list[str] = Field(default_factory=list)
    controls: list[str] = Field(default_factory=list)
    scroll_group: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    view: Literal["linked", "independent"] = "linked"
    link_frame: bool = True
    link_camera: bool = True
    start: StartSettings | None = None

    @model_validator(mode="after")
    def _3d_panel_binds_no_metrics(self) -> "PanelConfig":
        if self.kind == PANEL_KIND_3D and self.metrics:
            raise ValueError("a 3D panel shows a 3D view and takes no 'metrics'")
        if self.kind != PANEL_KIND_3D and self.view != "linked":
            raise ValueError("only a 3D panel has a 'view'")
        unlinked = not self.link_frame or not self.link_camera
        if self.view == "linked" and (unlinked or self.start is not None):
            raise ValueError(
                "'link_frame', 'link_camera' and 'start' are for an "
                "independent 3D panel (view = \"independent\"); a linked "
                "one shows the main view")
        return self


class AnalysisTabConfig(BaseModel):
    """A declarative Analysis Tab: a named grid of Panels (ADR 0021). ``selector``
    names a bespoke tab-level data selector from the control registry (e.g. the
    element picker); null uses the default model/dataset selector. ``controls``
    names tab-level control widgets from the registry (e.g. the energy-shift
    toggle) that drive a shared compute-param across the tab's Panels.

    ``column_widths`` and ``row_heights`` (ADR 0056) are relative sizes, one per
    column and one per row: ``[2, 1]`` makes the first column twice as wide.
    A list may be longer than the panels reach, for columns or rows left
    empty, never shorter. With ``row_heights`` the rows share the window's
    height; without it rows are at least 300 px and the tab scrolls. Columns
    are at least 400 px; when they do not fit, the tab scrolls sideways. The
    browser reads them; the desktop ignores them."""
    model_config = ConfigDict(extra="forbid")
    name: str
    has_data_selector: bool = True
    selector: str | None = None
    controls: list[str] = Field(default_factory=list)
    panels: list[PanelConfig] = Field(default_factory=list)
    column_widths: list[float] | None = None
    row_heights: list[float] | None = None

    @model_validator(mode="after")
    def _sizes_fit_the_grid(self) -> "AnalysisTabConfig":
        columns = max((p.col + p.colspan for p in self.panels), default=0)
        rows = max((p.row + p.rowspan for p in self.panels), default=0)
        for field, sizes, count, what in (
            ("column_widths", self.column_widths, columns, "columns"),
            ("row_heights", self.row_heights, rows, "rows"),
        ):
            if sizes is None:
                continue
            if len(sizes) < count:
                entries = "entry" if len(sizes) == 1 else "entries"
                raise ValueError(
                    f"{field} has {len(sizes)} {entries}; the tab has {count} {what}")
            if any(size <= 0 for size in sizes):
                raise ValueError(f"{field} must be positive")
        return self


class VisualizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    atom_color: AtomColorPresentation | None = None
    tabs: list[AnalysisTabConfig] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    visualization: VisualizationConfig = Field(default_factory=VisualizationConfig)


    
from __future__ import annotations
from typing import Any, Union
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


class MetricsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modules: list[MetricModuleConfig] = Field(default_factory=list)
    fields: list[FieldMetricConfig] = Field(default_factory=list)

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
    each taking its own grid cell."""
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


class AnalysisTabConfig(BaseModel):
    """A declarative Analysis Tab: a named grid of Panels (ADR 0021). ``selector``
    names a bespoke tab-level data selector from the control registry (e.g. the
    element picker); null uses the default model/dataset selector. ``controls``
    names tab-level control widgets from the registry (e.g. the energy-shift
    toggle) that drive a shared compute-param across the tab's Panels."""
    model_config = ConfigDict(extra="forbid")
    name: str
    has_data_selector: bool = True
    selector: str | None = None
    controls: list[str] = Field(default_factory=list)
    panels: list[PanelConfig] = Field(default_factory=list)


class VisualizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    atom_color: AtomColorPresentation | None = None
    tabs: list[AnalysisTabConfig] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    visualization: VisualizationConfig = Field(default_factory=VisualizationConfig)


    
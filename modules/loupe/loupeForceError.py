from UI.clientFeatures import ClientFeature
from client.dataWatcher import DataWatcher

DEPENDENCIES = ["loupeAtoms"]

# Each atom's force error length (ADR 0065 keep boxes; was ffast.force_mae).
FORCE_ERROR_LENGTH = "ffast.force_difference[atom+structure;length]"
# The desktop has no keep boxes (ADR 0065 section 9), so its Coloring list
# offers these settings of the force and acceleration bases: what the deleted
# per-atom and per-element metrics gave, and their per-component twins.
DESKTOP_COLOURINGS = (
    FORCE_ERROR_LENGTH,
    "ffast.force_difference[atom+structure;component;mae]",
    "ffast.force_difference[element;component;mae]",
    "ffast.force_difference[element;component;rmse]",
    "ffast.accel_difference[atom+structure;length]",
    "ffast.accel_difference[atom;length;mae]",
    "ffast.accel_difference[element;length;mae]",
)


def loadData(env):
    # Self-register the force built-ins this module colours by (Force
    # Difference and its keep-box settings, ADR 0065) instead of relying on
    # another module's load order (was the only reason this depended on the
    # retired basicErrors anchor).
    from ffast.metrics.builtin import accel_metrics, force_metrics  # noqa: F401
    from ffast.metrics.registry import default_registry
    for metric_id in DESKTOP_COLOURINGS:
        default_registry.ensure(metric_id)


def addSettings(UIHandler, loupe):
    # Prediction selection is handled by the single "Prediction" combo in
    # loupeAtoms — this module no longer adds a second, duplicate model selector.
    # The DataWatcher is kept because Loupe hooks prediction-change resync to it
    # (_ensureAdapterHooks).
    dw = DataWatcher(loupe.env)
    loupe.forceErrorDataWatcher = dw
    # Repaint trigger only: fires when force-error metric data lands (ADR 0019).
    dw.setMetricDependencies({FORCE_ERROR_LENGTH: {}})

    settings = loupe.settings

    # Repaint when newly computed metrics arrive, but only while a metric coloring
    # is active (prevents a storm of re-applies on unrelated data updates).
    def _on_force_data_update():
        if settings.get("atomColorType") in loupe._colorLabelToMetricId:
            loupe._applyColoring()

    dw.addCallback(_on_force_data_update)


def loadLoupe(UIHandler, loupe):
    addSettings(UIHandler, loupe)


CLIENT_FEATURES = [ClientFeature(widget_factory=loadLoupe)]

from UI.clientFeatures import ClientFeature
from client.dataWatcher import DataWatcher

DEPENDENCIES = ["loupeAtoms"]


def loadData(env):
    # Self-register the force built-ins this module colours by (``ffast.force_mae``)
    # instead of relying on another module's load order (was the only reason this
    # depended on the retired basicErrors anchor).
    from ffast.metrics.builtin import force_metrics  # noqa: F401


def addSettings(UIHandler, loupe):
    # Prediction selection is handled by the single "Prediction" combo in
    # loupeAtoms — this module no longer adds a second, duplicate model selector.
    # The DataWatcher is kept because Loupe hooks prediction-change resync to it
    # (_ensureAdapterHooks).
    dw = DataWatcher(loupe.env)
    loupe.forceErrorDataWatcher = dw
    # Repaint trigger only: fires when force-error metric data lands (ADR 0019).
    dw.setMetricDependencies({"ffast.force_mae": {}})

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

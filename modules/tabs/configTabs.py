"""Config-driven Analysis Tabs (ADR 0021, Phase 5).

The single module that realises the declarative tabs: ``loadData`` compiles every
configured tab's Transform Metrics (it runs on the server and the headless thread
*before* ``registry.freeze()``, and on the client), and ``loadUI`` builds the tabs
on the client via the Panel engine. The server never sees Panel Kinds or layout —
only the metric refs it must compute.

All four built-in analysis tabs (Basic Errors, Subsystem Errors, Atomic Errors,
Gyration) are now canonical declarative tabs built here from
``ffast/config/builtin_tabs/`` — their legacy modules have been retired. ``loadData``
self-registers every ffast built-in it needs, so this module has no load-order
dependencies.
"""
import logging

logger = logging.getLogger("FFAST")

DEPENDENCIES = []


def _project_config():
    from pathlib import Path

    from ffast.config.loader import discover_config, load_project_config

    try:
        path = discover_config(Path.cwd())
        return load_project_config(path) if path else None
    except Exception:
        logger.warning("configTabs: project config unavailable", exc_info=True)
        return None


def loadData(env):
    # Register the base metrics the tabs reduce, then compile every Transform
    # Metric referenced by the bundled + project tabs into the default registry.
    # Runs before freeze on the server, so the compiled metrics are computable.
    from ffast.metrics.builtin import (  # noqa: F401
        atomic_metrics, energy_metrics, force_metrics, structure_metrics,
        transform_metrics,
    )
    from ffast.config.tabs import compile_tabs_metrics, merge_tabs
    from ffast.metrics.fields import compile_field_metrics

    project_config = _project_config()
    # Dataset Field passthrough metrics (ADR 0023) come from the project config's
    # [[metrics.fields]] and must register before freeze, alongside tab metrics.
    if project_config is not None:
        try:
            fids = compile_field_metrics(project_config.metrics.fields)
            if fids:
                logger.info("configTabs: compiled %d Dataset Field metric(s)", len(fids))
        except Exception:
            logger.exception("configTabs: failed compiling Dataset Field metrics")

    try:
        tabs = merge_tabs(project_config)
        ids = compile_tabs_metrics(tabs)
        logger.info("configTabs: compiled %d analysis-tab metric(s)", len(ids))
    except Exception:
        logger.exception("configTabs: failed compiling analysis-tab metrics")


def loadUI(UIHandler, env):
    from UI.panels_toml import build_analysis_tabs

    build_analysis_tabs(UIHandler, env)


from UI.clientFeatures import DatasetFeature  # noqa: E402

DATASET_FEATURES = [DatasetFeature(metric_ids=[], widget_factory=loadUI)]

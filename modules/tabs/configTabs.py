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
    # Compile every declarative metric the project + bundled tabs contribute
    # (Dataset Fields, Expression Metrics, Analysis-Tab Transform Metrics) into
    # the default registry before freeze, so they are computable. The compiler
    # registers the base built-ins first and collects per-entry config errors.
    #
    # Client policy: log each config error *clearly* (no traceback) and keep
    # running with the offending metric absent — the server enforces the stricter
    # "refuse to start on a Configuration Failure" policy (server._main); a config
    # error surfaces there and in `ffast-cli metrics validate` (ADR 0042).
    from ffast.config.tabs import compile_project_metrics

    result = compile_project_metrics(_project_config())
    for context, msg in result.errors:
        logger.error("configTabs: metric config error in %s: %s", context, msg)
    logger.info("configTabs: compiled %d declarative metric(s)", len(result.ids))


def loadUI(UIHandler, env):
    from UI.panels_toml import build_analysis_tabs

    build_analysis_tabs(UIHandler, env)


from UI.clientFeatures import DatasetFeature  # noqa: E402

DATASET_FEATURES = [DatasetFeature(metric_ids=[], widget_factory=loadUI)]

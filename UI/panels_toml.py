"""Build declarative Analysis Tabs from config (ADR 0021, Phase 5).

Translates :class:`ffast.config.models.AnalysisTabConfig` into live widgets using
the Panel engine (:mod:`UI.panels`) and the control/selector registries
(:mod:`UI.controls`). This is the client-only half of the config-driven path; the
metric compilation half (:func:`ffast.config.tabs.compile_tabs_metrics`) runs on
both the server and the client.
"""
import logging

from ffast.config.tabs import merge_tabs, resolve_ref

logger = logging.getLogger("FFAST")


def _panel_spec(panel_cfg, selector_obj):
    """PanelConfig → the engine spec dict ``make_panel`` consumes.

    Metric roles are compiled to concrete ids (a list role stays a list, e.g. an
    overlay's ``series``); renderer-side keys and kind-specific ``options`` pass
    straight through; the tab selector is injected so bespoke kinds can read it."""
    spec = {}
    for role, ref in panel_cfg.metrics.items():
        if isinstance(ref, list):
            spec[role] = [resolve_ref(r) for r in ref]
        else:
            spec[role] = resolve_ref(ref)

    if panel_cfg.title is not None:
        spec["title"] = panel_cfg.title
    if panel_cfg.tooltip is not None:
        spec["tooltip"] = panel_cfg.tooltip
    spec["legend"] = panel_cfg.legend
    if panel_cfg.x_label is not None:
        spec["x_label"] = panel_cfg.x_label
    if panel_cfg.y_label is not None:
        spec["y_label"] = panel_cfg.y_label
    spec["diagonal"] = panel_cfg.diagonal
    spec["precision"] = panel_cfg.precision
    if panel_cfg.hidden_params:
        spec["hidden_params"] = list(panel_cfg.hidden_params)
    spec.update(panel_cfg.options or {})
    if selector_obj is not None:
        spec["selector_obj"] = selector_obj
    return spec


def build_analysis_tab(UIHandler, env, tab):
    from UI.ContentTab import ContentTab
    from UI.Templates import HorizontalContainerScrollArea
    from UI.controls import make_control, make_selector, make_tab_control
    from UI.panels import make_panel

    use_default_selector = tab.has_data_selector and tab.selector is None
    ct = ContentTab(UIHandler, hasDataSelector=use_default_selector)
    UIHandler.addContentTab(ct, tab.name)

    selector_obj = None
    if tab.selector is not None:
        selector_obj = make_selector(tab.selector, UIHandler, parent=ct)
        ct.setDataSelector(selector_obj)

    panels = []
    scroll_groups = {}  # name -> [first PanelConfig, HorizontalContainerScrollArea]
    for pcfg in tab.panels:
        spec = _panel_spec(pcfg, selector_obj)
        panel = make_panel(ct.handler, pcfg.kind, parent=ct, **spec)
        panels.append(panel)

        if pcfg.scroll_group:
            entry = scroll_groups.get(pcfg.scroll_group)
            if entry is None:
                scroll = HorizontalContainerScrollArea(parent=ct)
                scroll.content.layout.setSpacing(32)
                scroll_groups[pcfg.scroll_group] = entry = [pcfg, scroll]
            entry[1].addContent(panel)
        else:
            ct.addWidget(panel, pcfg.row, pcfg.col, pcfg.rowspan, pcfg.colspan)

        if getattr(ct, "dataSelector", None) is not None:
            ct.addDataSelectionCallback(panel.setModelDatasetDependencies)
        for cname in pcfg.controls:
            panel._addControl(make_control(cname, panel))

    # Each horizontal scroll strip sits at its first member's grid slot.
    for first_cfg, scroll in scroll_groups.values():
        scroll.addStretch()
        ct.addWidget(scroll, first_cfg.row, first_cfg.col,
                     first_cfg.rowspan, first_cfg.colspan)

    # Tab-level controls (e.g. the energy-shift toggle shared across panels).
    for cname in tab.controls:
        ct.topLayout.addWidget(make_tab_control(cname, ct, panels))

    return ct


def build_analysis_tabs(UIHandler, env, tabs=None):
    """Build every configured Analysis Tab. ``tabs`` defaults to the merged
    bundled + project tabs."""
    if tabs is None:
        project = _discover_project_config()
        tabs = merge_tabs(project)
    built = []
    for tab in tabs:
        try:
            built.append(build_analysis_tab(UIHandler, env, tab))
        except Exception:
            logger.exception("Failed to build analysis tab '%s'", tab.name)
    return built


def _discover_project_config():
    from pathlib import Path

    from ffast.config.loader import discover_config, load_project_config

    try:
        path = discover_config(Path.cwd())
        return load_project_config(path) if path else None
    except Exception:
        logger.warning("Could not load project config for analysis tabs", exc_info=True)
        return None

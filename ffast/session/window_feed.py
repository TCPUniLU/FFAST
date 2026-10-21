"""WindowFeed: the one server-side owner of the state every window holds (ADR 0062).

A browser window keeps its own copy of some shared server state: the dataset
and model announcements, the metric catalog, the tab layout and the
user-metrics list. This class builds each piece, in one function each, replays
them to a new window on connect, and keeps the list of open windows' sessions
that "Used by" reads to find views coloured by a metric.

``ServerSession`` and ``server.py`` call the feed rather than building these
messages themselves. There is one feed per server (``env.window_feed``).
"""
from __future__ import annotations

import logging
import weakref

from ffast.protocol import control

logger = logging.getLogger("FFAST")


class WindowFeed:
    def __init__(self, env, broadcast) -> None:
        # ``env`` may be None (a test builds a catalog with no env); every
        # attribute is read with ``getattr(env, name, None)``.
        self.env = env
        self._broadcast = broadcast
        # Sessions of the open windows. A WeakSet for now (ADR 0062 step 6).
        self.sessions: weakref.WeakSet = weakref.WeakSet()

    def attach(self, session) -> None:
        self.sessions.add(session)

    # ── "Used by": views coloured by a metric ───────────────────────────────

    def view_colouring_users(self, metric_id: str) -> list[str]:
        """"a view's colouring" when any open view of any connection colours its
        atoms by ``metric_id`` (the lookup behind delete-while-used, ADR 0057)."""
        target = f"metric:{metric_id}"
        for session in list(self.sessions):
            for view in list(session.views.values()):
                params = view.state.parameters.get("ffast.atom_color", {})
                if params.get("source") == target:
                    return ["the main view's colouring"]
        return []

    # ── builders: each returns packed bytes ─────────────────────────────────

    def dataset_meta(self, dataset) -> bytes | None:
        """REMOTE_DATASET_META for one dataset; ``None`` when it has no
        fingerprint."""
        from ffast.protocol import DatasetMeta
        from ffast.protocol.rpc import pack
        fingerprint = getattr(dataset, "fingerprint", None)
        if fingerprint is None:
            return None
        return pack(
            control.REMOTE_DATASET_META,
            (fingerprint,),
            DatasetMeta.model_validate(dataset.toMetaDict()).model_dump(),
        )

    def model_meta(self, model_fp, model) -> bytes:
        """REMOTE_MODEL_META for one model (a ghost prediction or a real
        server-side model, Stage 2), with the datasets it has predictions for."""
        from ffast.cache import CacheKey
        from ffast.protocol import ModelMeta
        from ffast.protocol.rpc import pack
        name = getattr(model, "name", None) or model_fp[:8]
        dataset_fps = []
        for cache_key in list(self.env.cache.keys()):
            ck = CacheKey.try_parse(cache_key)
            if ck is not None and ck.matches_model(model_fp) and ck.dataset_fp:
                if ck.dataset_fp not in dataset_fps:
                    dataset_fps.append(ck.dataset_fp)
        return pack(
            control.REMOTE_MODEL_META,
            (model_fp,),
            ModelMeta(name=name, dataset_fingerprints=dataset_fps).model_dump(),
        )

    def metric_catalog(self) -> bytes:
        """METRIC_CATALOG, so the client builds metric controls from the server's
        registry (ADR 0016). Registers the built-in metrics first."""
        import ffast.metrics.builtin  # noqa: F401 — register built-in metrics
        from ffast.metrics.catalog import build_metric_catalog
        from ffast.metrics.registry import _default_registry
        from ffast.protocol import MetricCatalog
        from ffast.protocol.rpc import pack
        catalog = build_metric_catalog(
            _default_registry, getattr(self.env, "user_metrics", None))
        return pack(control.METRIC_CATALOG, [], MetricCatalog(metrics=catalog).model_dump())

    def tab_layout(self) -> bytes:
        """TAB_LAYOUT. The server's tab sources (built-in, project and user
        tabs, ADR 0056) when it has them; else a layout cached at startup; else
        the bundled tabs."""
        from ffast.protocol import TabLayout
        from ffast.protocol.rpc import pack
        sources = getattr(self.env, "tab_sources", None)
        if sources is not None:
            tabs = sources.layout()
        else:
            tabs = getattr(self.env, "analysis_tab_layout", None)
            if tabs is None:
                from ffast.config.tabs import build_tab_layout, merge_tabs
                tabs = build_tab_layout(merge_tabs())
        return pack(control.TAB_LAYOUT, [], TabLayout(tabs=tabs).model_dump())

    def user_metrics(self) -> bytes:
        """USER_METRICS: the user-metrics list."""
        from ffast.protocol.rpc import pack
        um = getattr(self.env, "user_metrics", None)
        return pack(control.USER_METRICS, [], {"metrics": um.entries() if um else []})

    # ── replay ──────────────────────────────────────────────────────────────

    def replay(self) -> list[tuple[bytes, str]]:
        """The messages a new window gets, each with a label for the log: each
        dataset announcement, each model announcement, the catalog. A piece
        that fails to build is logged as a warning and left out."""
        out: list[tuple[bytes, str]] = []

        # ── datasets, and the subsets windows made (a hidden one is not listed) ──
        try:
            datasets = self.env.datasets.all()
        except Exception:
            datasets = []
        for dataset in datasets:
            try:
                data = self.dataset_meta(dataset)
            except Exception as exc:
                logger.warning("State replay: dataset %r error: %s",
                               getattr(dataset, "fingerprint", None), exc)
                continue
            if data is not None:
                out.append((data, f"dataset {dataset.fingerprint!r}"))

        # ── models ──────────────────────────────────────────────────────────
        try:
            models = list(self.env.models.items())
        except Exception:
            models = []
        for model_fp, model in models:
            try:
                out.append((self.model_meta(model_fp, model), f"model {model_fp[:8]!r}"))
            except Exception as exc:
                logger.warning("State replay: model %r error: %s", model_fp[:8], exc)

        # ── the catalog ─────────────────────────────────────────────────────
        try:
            out.append((self.metric_catalog(), "METRIC_CATALOG"))
        except Exception as exc:
            logger.warning("State replay: METRIC_CATALOG error: %s", exc)
        return out

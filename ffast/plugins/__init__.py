"""Bundled server plugins (ADR 0048).

Dataset loaders (``ffast.plugins.loaders``) and model backends
(``ffast.plugins.models``) ship inside the ``ffast`` package itself, so a
headless ``pip install ffast`` discovers and registers them without the
Desktop-Client ``modules/`` tree. Discovered by real dotted-name import (see
``ffast.core.plugin_discovery``), not the ``modules/`` glob.
"""

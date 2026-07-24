"""FFAST Headless Core — the Environment engine and its collaborators.

ADR 0047 relocates the Qt-free ``Environment`` graph (event/task spine,
registries, data service, loading coordinator, connection manager, and the
``Environment``/``HeadlessEnvironment`` classes themselves) out of the flat
``client/`` Desktop-Client dirs into this package, so the server import closure
is ``client/``-free. Migration is phased behind re-export shims; see the ADR.
"""

"""Test helper: expose ADR-0020 composed sub-objects on a flat env double.

After the Environment decomposition (ADR 0020) production code reaches data,
registries and the session through ``env.data`` / ``env.datasets`` /
``env.models`` / ``env.remote``.  Test doubles implement the old flat API
(``getDataset`` / ``getModel`` / ``getCacheByKey`` / ``cache`` …).  Calling
``_attach_env_facets(double)`` wires the sub-objects to the double's existing
methods so the same doubles work against the composed API.
"""

import types


def _attach_env_facets(env):
    _m = env.models if isinstance(getattr(env, "models", None), dict) else None
    env.data = env  # data-access methods (getData/getCacheByKey/…) live on the double
    env.datasets = types.SimpleNamespace(
        get=getattr(env, "getDataset", lambda *a, **k: None),
        all=getattr(env, "getAllDatasets", lambda **k: []),
        all_keys=getattr(env, "getAllDatasetKeys", lambda: []),
    )
    env.models = types.SimpleNamespace(
        get=(_m.get if _m is not None else getattr(env, "getModel", lambda *a, **k: None)),
        all_keys=((lambda: list(_m)) if _m is not None
                  else getattr(env, "getAllModelKeys", lambda: [])),
        all=((lambda **k: list(_m.values())) if _m is not None
             else getattr(env, "getAllModels", lambda **k: [])),
    )
    env.remote = types.SimpleNamespace(
        sendViewCommand=getattr(env, "sendViewCommand", lambda **k: None),
        openRemoteView=getattr(env, "openRemoteView", lambda *a, **k: None),
        closeRemoteView=getattr(env, "closeRemoteView", lambda *a, **k: None),
        taskFetchRemoteDataset=getattr(env, "taskFetchRemoteDataset", lambda *a, **k: None),
    )

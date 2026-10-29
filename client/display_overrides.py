"""Panel Display Override / Colorbar Display Override persistence (ADR 0029).

Client-local cosmetic state -- axis label text/size, legend text/size/position,
and the 3D colorbar's label/unit/size/position -- silently rewritten by the app
whenever a user edits one of these; never hand-authored, so distinct from
Visualization Configuration. Also carries no Metric or schema, so distinct from
a Presentation Parameter. See docs/adr/0029 and CONTEXT.md.

Saved under ~/.ffast/display_overrides.json (parallel to Session Records) as
``{"panels": {<panel key>: {...}}, "colorbar": {<metric id>: {...}}}``.
"""
import json
import logging
import os

logger = logging.getLogger("FFAST")

OVERRIDES_FILE = os.path.expanduser(
    os.path.join("~", ".ffast", "display_overrides.json")
)

# Mtime-keyed cache so N panels built in one tab share one disk read+parse
# instead of each doing its own (was measurably laggy on tab construction).
# Keyed on path too since tests monkeypatch OVERRIDES_FILE per-run.
_cache = {"path": None, "mtime": None, "data": None}


def _load():
    try:
        mtime = os.path.getmtime(OVERRIDES_FILE)
    except OSError:
        mtime = None
    if _cache["data"] is not None and _cache["path"] == OVERRIDES_FILE and _cache["mtime"] == mtime:
        return _cache["data"]
    if mtime is None:
        data = {"panels": {}, "colorbar": {}}
    else:
        try:
            with open(OVERRIDES_FILE) as f:
                data = json.load(f)
        except Exception:
            data = {"panels": {}, "colorbar": {}}
        data.setdefault("panels", {})
        data.setdefault("colorbar", {})
    _cache["path"] = OVERRIDES_FILE
    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


def _write(data):
    os.makedirs(os.path.dirname(OVERRIDES_FILE), exist_ok=True)
    try:
        with open(OVERRIDES_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        logger.warning("Failed to write display_overrides.json: %s", exc)
        return
    _cache["path"] = OVERRIDES_FILE
    try:
        _cache["mtime"] = os.path.getmtime(OVERRIDES_FILE)
    except OSError:
        _cache["mtime"] = None
    _cache["data"] = data


def _pop_nested(d, path):
    """Remove d[path[0]]...[path[-1]] in place, pruning empty parent dicts."""
    if not path:
        return
    key, rest = path[0], path[1:]
    if key not in d:
        return
    if rest:
        _pop_nested(d[key], rest)
        if not d[key]:
            del d[key]
    else:
        del d[key]


def _set_nested(entry, path, value):
    """Set entry[path[0]]...[path[-1]] = value; None/"" clears it instead
    (Q9: an emptied field reverts to the Panel Kind / colorbar default)."""
    if value in (None, ""):
        _pop_nested(entry, list(path))
        return
    d = entry
    for p in path[:-1]:
        d = d.setdefault(p, {})
    d[path[-1]] = value


def panel_key(tab_name, kind_name, metric_ids):
    """Content-based Panel identity (ADR 0029): keyed on what the Panel binds,
    not its grid position, so it survives TOML reordering/insertion."""
    ids = []
    for m in metric_ids:
        if isinstance(m, (list, tuple)):
            ids.extend(m)
        elif m is not None:
            ids.append(m)
    return f"{tab_name}|{kind_name}|{','.join(sorted(ids))}"


def get_panel_override(tab_name, kind_name, metric_ids):
    """This Panel's saved override dict, or {} if it has none."""
    data = _load()
    return data["panels"].get(panel_key(tab_name, kind_name, metric_ids), {})


def set_panel_override(tab_name, kind_name, metric_ids, path, value):
    """Set (or clear, for a None/empty ``value``) a nested field of this
    Panel's override. ``path`` is e.g. ``("x_label", "text")`` or
    ``("legend", "entries", "<dataset>|<model>")``."""
    data = _load()
    key = panel_key(tab_name, kind_name, metric_ids)
    entry = data["panels"].get(key, {})
    _set_nested(entry, path, value)
    if entry:
        data["panels"][key] = entry
    else:
        data["panels"].pop(key, None)
    _write(data)


def get_colorbar_override(metric_id):
    """The Colorbar Display Override for ``metric_id``, or {} if it has none."""
    data = _load()
    return data["colorbar"].get(metric_id, {})


def set_colorbar_override(metric_id, path, value):
    """Set (or clear) a nested field of the colorbar override for ``metric_id``
    -- the client-tracked Metric ID currently driving atom-coloring."""
    data = _load()
    entry = data["colorbar"].get(metric_id, {})
    _set_nested(entry, path, value)
    if entry:
        data["colorbar"][metric_id] = entry
    else:
        data["colorbar"].pop(metric_id, None)
    _write(data)

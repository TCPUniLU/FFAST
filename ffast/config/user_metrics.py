"""User metrics: formulas defined while the app runs (ADR 0057).

A **user metric** is an Expression Metric (ADR 0042) saved by the server as one
TOML file in ``~/.ffast/metrics/``, in exactly the project's ``[[metrics.expr]]``
format, so *Export* is a copy. It is registered into the live metric registry
after the project's metrics, and can be edited, replaced and removed while the
server runs. A formula can only do arithmetic over named inputs, so whoever holds
the access token can define one without being able to run code.

This module is the pure, Qt-free half: the file store and the rules (ids,
versions, delete-while-used, project-wins). What must happen *around* a change —
refresh the worker pool, drop cached results, tell every window — needs the
Environment and is done by the caller from the ids a change returns
(``Change.affected``).

Server only: the desktop client and ``ffast-cli`` never load these (rule 14).
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import tomli_w
from pydantic import ValidationError

from ffast.config.models import ExprMetricConfig
from ffast.metrics.dims import shape_to_str

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path("~/.ffast/metrics")
DEFAULT_PREFIX = "user."

_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")

_HEADER = (
    "# A metric saved by FFAST (ADR 0057). Same format as a project's\n"
    "# [[metrics.expr]]: paste it into ffast.toml, or use Export.\n"
)


SESSION_FILE = "user_metrics.toml"

_SESSION_HEADER = (
    "# Formulas of the user metrics this session uses (ADR 0057 rule 15), in the\n"
    "# [[metrics.expr]] format. Loading the session offers to add the ones the\n"
    "# server lacks; a formula cannot run code, so carrying it is safe.\n"
)


class MetricError(ValueError):
    """A metric change the rules refuse; the message is shown to the user."""


class MetricConflict(MetricError):
    """The server's copy changed since the editor loaded it (rule 6)."""


def metric_revision(cfg: ExprMetricConfig) -> str:
    """Identifies a metric's content: equal revisions, equal metrics."""
    text = json.dumps(cfg.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _toml(cfg: ExprMetricConfig) -> str:
    """The metric as someone would write it in ``ffast.toml``: an
    ``[[metrics.expr]]`` table with a ``[metrics.expr.vars]`` table, no defaults."""
    data = cfg.model_dump(mode="json", exclude_defaults=True)
    variables = data.pop("vars", {})
    text = "[[metrics.expr]]\n" + tomli_w.dumps(data)
    if variables:
        text += "\n  [metrics.expr.vars]\n" + "".join(
            f"  {line}\n" for line in tomli_w.dumps(variables).splitlines())
    return text


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first.get("loc", ()))
    msg = str(first.get("msg", "")).removeprefix("Value error, ")
    return f"{where}: {msg}" if where else msg


class UserMetricStore:
    """The metric files in one folder. Reads on demand, writes at once."""

    def __init__(self, root: Path | str = DEFAULT_DIR) -> None:
        self.root = Path(root).expanduser()
        #: (file, message) for each file that could not be read
        self.errors: list[tuple[str, str]] = []

    def metrics(self) -> list[ExprMetricConfig]:
        """Every readable metric file, in file-name order."""
        self.errors = []
        if not self.root.is_dir():
            return []
        out = []
        for path in sorted(self.root.glob("*.toml")):
            try:
                out.append(self._read(path))
            except (OSError, ValueError, ValidationError) as exc:
                self.errors.append((path.name, str(exc)))
                logger.warning("User metric %s skipped: %s", path, exc)
        return out

    @staticmethod
    def _read(path: Path) -> ExprMetricConfig:
        """One metric per file, as ``[[metrics.expr]]`` (the project form)."""
        data = tomllib.loads(path.read_text())
        entries = (data.get("metrics") or {}).get("expr") or []
        if len(entries) != 1:
            raise ValueError(f"a metric file holds exactly one metric, found {len(entries)}")
        return ExprMetricConfig.model_validate(entries[0])

    def write(self, cfg: ExprMetricConfig) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        file = f"{cfg.id}.toml"
        text = _HEADER + "\n" + _toml(cfg)
        tmp = self.root / f".{file}.tmp"
        tmp.write_text(text)
        tmp.replace(self.root / file)

    def delete(self, id: str) -> None:
        (self.root / f"{id}.toml").unlink(missing_ok=True)


@dataclass
class Change:
    """What a change did: the metric it was about, and every metric whose
    results may now differ (that one and everything built on it). The caller
    drops those results, refreshes the workers and announces the update."""
    id: str
    affected: set[str] = field(default_factory=set)


@dataclass
class _Entry:
    cfg: ExprMetricConfig
    status: str = "ok"          # "ok" | "hidden" | "error"
    message: str = ""


def _locked(method):
    """Run a changing method holding the object's lock."""
    @functools.wraps(method)
    def run(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return run


class UserMetrics:
    """The user metrics of one server, registered into ``registry``.

    ``baseline`` is the set of metric ids that existed before any user metric
    (built-in and project): they win an id clash and are read-only.
    ``builtin_ids`` are the ones among them that ship with FFAST, so the rest
    can be labelled "project". Other sources of "who uses this metric" (tabs, a
    view's colouring) plug in through ``usage_providers``.
    """

    def __init__(self, store: UserMetricStore, registry=None,
                 builtin_ids: set[str] | None = None) -> None:
        from ffast.metrics.registry import default_registry
        self.store = store
        self.registry = registry if registry is not None else default_registry
        self.builtin_ids = builtin_ids
        self.baseline: set[str] = set()
        self.usage_providers: list[Callable[[str], list[str]]] = []
        self._entries: dict[str, _Entry] = {}
        # Changes run on worker threads, one per window that asked: take turns.
        self._lock = threading.RLock()

    # ── start-up ────────────────────────────────────────────────────────────
    def load(self) -> list[tuple[str, str]]:
        """Register every metric file, after the project's metrics (rule 3).

        Fail-soft: returns ``(id, message)`` for each that did not compile; it
        stays listed, with the reason. A file whose id a built-in or project
        metric already holds is listed as hidden. Files may build on each
        other, so compile in rounds until a round adds nothing.
        """
        self.baseline = set(self.registry.list_metrics())
        self._entries = {}
        pending = []
        for cfg in self.store.metrics():
            if cfg.id in self._entries:
                continue
            entry = self._entries[cfg.id] = _Entry(cfg)
            if cfg.id in self.baseline:
                entry.status = "hidden"
                entry.message = f"Hidden: the project defines {cfg.id}"
            else:
                pending.append(entry)
        while pending:
            failed = []
            for entry in pending:
                try:
                    self._compile(entry.cfg)
                except Exception as exc:
                    entry.status, entry.message = "error", str(exc)
                    failed.append(entry)
            if len(failed) == len(pending):
                break
            for entry in pending:
                if entry not in failed:
                    entry.status, entry.message = "ok", ""
            pending = failed
        errors = self.registry.freeze()
        for mid, msg in errors:
            logger.error("User metrics: %s: %s", mid, msg)
        return [(e.cfg.id, e.message) for e in self._entries.values() if e.status == "error"]

    def _compile(self, cfg: ExprMetricConfig) -> None:
        from ffast.metrics.expr import compile_expr_metric
        compile_expr_metric(cfg.id, cfg.expr, dict(cfg.vars), label=cfg.label,
                            unit=cfg.unit, registry=self.registry)

    # ── reading ─────────────────────────────────────────────────────────────
    def origin(self, id: str) -> str:
        """"user", "built-in" or "project"."""
        entry = self._entries.get(id)
        if entry is not None and entry.status == "ok":
            return "user"
        if self.builtin_ids is not None and id not in self.builtin_ids:
            return "project"
        return "built-in"

    def used_by(self, id: str) -> list[str]:
        """Who would break if ``id`` went away: other metrics (not the derived
        transform metrics, which follow their tab), then whatever the providers
        report (tab and panel, a view's colouring)."""
        users = [f"metric {m}" for m in self.registry.direct_dependents_of(id)
                 if "__" not in m]
        for provider in self.usage_providers:
            users.extend(provider(id))
        return users

    def entries(self) -> list[dict]:
        """The user metrics as the pane lists them."""
        out = []
        for e in self._entries.values():
            d = {
                "id": e.cfg.id, "label": e.cfg.label, "unit": e.cfg.unit,
                "expr": e.cfg.expr, "vars": dict(e.cfg.vars),
                "status": e.status, "message": e.message,
                "revision": metric_revision(e.cfg), "shape": None, "colour_problem": None,
            }
            if e.status == "ok" and self.registry.has(e.cfg.id):
                from ffast.metrics.expr import metric_colour_problem
                d["shape"] = shape_to_str(self.registry.get(e.cfg.id)[0].shape)
                d["colour_problem"] = metric_colour_problem(e.cfg.id, self.registry)
            out.append(d)
        return out

    def get(self, id: str) -> ExprMetricConfig | None:
        entry = self._entries.get(id)
        return entry.cfg if entry is not None else None

    def registry_without(self, id: str):
        """The registry as a formula named ``id`` should see it: without its own
        old entry, so it cannot depend on itself."""
        return _Without(self.registry, id)

    def check(self, data: dict) -> dict:
        """Validate a formula without changing anything (rule 11): returns
        ``{ok, error, shape, colour_problem}``. Touches no data."""
        from ffast.metrics.expr import analyze_expr, colour_problem
        try:
            cfg = ExprMetricConfig.model_validate(data)
        except ValidationError as exc:
            return {"ok": False, "error": _validation_message(exc), "shape": None}
        try:
            # Editing: the metric's own old entry must not be seen as a
            # dependency of itself, so look through a copy without it.
            _inputs, shape, _parts = analyze_expr(
                cfg.expr, dict(cfg.vars), registry=self.registry_without(cfg.id), id=cfg.id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "shape": None}
        return {"ok": True, "error": None, "shape": shape_to_str(shape),
                "colour_problem": colour_problem(cfg.expr, cfg.vars.values(),
                                                 self.registry_without(cfg.id))}

    # ── changing ────────────────────────────────────────────────────────────
    @_locked
    def save(self, data: dict, previous_id: str | None = None,
             base_revision: str | None = None, overwrite: bool = False) -> Change:
        """Create a metric (``previous_id`` None) or edit one, keeping its id
        (rule 5). Validates, re-registers, then writes the file; on any failure
        the registry and the file are as they were."""
        try:
            cfg = ExprMetricConfig.model_validate(data)
        except ValidationError as exc:
            raise MetricError(_validation_message(exc)) from None
        if not _ID.match(cfg.id):
            raise MetricError(
                f"{cfg.id!r} is not a usable id: letters, digits and underscores "
                f"in dot-separated parts, such as mylab.e_per_atom")

        current = self._entries.get(cfg.id)
        if previous_id is None:
            if self.registry.has(cfg.id) or current is not None:
                raise MetricError(f"The id {cfg.id} is already taken")
        else:
            if cfg.id != previous_id:
                raise MetricError("An edit keeps the metric's id; use Duplicate to make a copy")
            if current is None:
                if self.registry.has(previous_id):
                    raise self._not_editable(previous_id)
                # Another window deleted it while this one edited (rule 6):
                # a conflict, and "save anyway" makes it again under the same id.
                if not overwrite:
                    raise MetricConflict("Deleted in another window")
                return self._register(cfg, None)
            if current.status == "hidden":
                raise MetricError(f"{current.message}; Duplicate it under a new id, or delete it")
            if (not overwrite and base_revision is not None
                    and base_revision != metric_revision(current.cfg)):
                raise MetricConflict("Changed in another window")

        return self._register(cfg, current)

    def _not_editable(self, id: str) -> MetricError:
        if self.registry.has(id):
            return MetricError(f"{id} is a {self.origin(id)} metric; use Duplicate to make your own copy")
        return MetricError(f"No user metric {id}")

    def _register(self, cfg: ExprMetricConfig, current: _Entry | None) -> Change:
        from ffast.metrics.expr import _ExprFn, analyze_expr
        id = cfg.id
        registry = self.registry
        old = registry.unregister(id) if registry.has(id) else None
        old_shape = old[0].shape if old is not None else None
        try:
            self._compile(cfg)
            errors = registry.freeze()
            if errors:
                raise MetricError("; ".join(msg for _m, msg in errors))
            if old is not None:
                new_shape = registry.get(id)[0].shape
                if tuple(new_shape) != tuple(old_shape):
                    self._check_dependents(id, _ExprFn, analyze_expr)
        except Exception as exc:
            if registry.has(id):
                registry.unregister(id)
            if old is not None:
                registry.restore(id, old)
            registry.freeze()
            raise exc if isinstance(exc, MetricError) else MetricError(str(exc)) from None
        # Dependents that are formulas keep working only if their own shape is
        # unchanged; the check above ran only when this metric's shape moved.
        self.store.write(cfg)
        self._entries[id] = _Entry(cfg)
        return Change(id, {id, *registry.dependents_of(id)})

    def _check_dependents(self, id: str, expr_fn, analyze_expr) -> None:
        from ffast.metrics.transforms import _TransformFn
        for dep in self.registry.direct_dependents_of(id):
            schema, fn = self.registry.get(dep)
            if isinstance(fn, _TransformFn):
                # A transform that says shape = "inherit" copied its source's
                # shape when it was registered (ADR 0061). An inherited unit is
                # not checked: a stale unit only changes an axis label.
                new = self.registry.get(id)[0].shape
                if fn.transform.shape == "inherit" and tuple(new) != tuple(schema.shape):
                    raise MetricError(
                        f"This change would give {dep} another shape "
                        f"({shape_to_str(schema.shape)} → {shape_to_str(new)})")
                continue
            if not isinstance(fn, expr_fn):
                continue
            refs = fn.user_vars
            try:
                _i, shape, _p = analyze_expr(fn.expr, refs, registry=_Without(self.registry, dep), id=dep)
            except ValueError as exc:
                raise MetricError(f"This change would break {dep}: {exc}") from None
            if tuple(shape) != tuple(schema.shape):
                raise MetricError(
                    f"This change would give {dep} another shape "
                    f"({shape_to_str(schema.shape)} → {shape_to_str(shape)})")

    @_locked
    def delete(self, id: str) -> Change:
        """Delete a user metric. Refused while anything uses it (rule 7); the
        message lists the users."""
        current = self._entries.get(id)
        if current is None:
            raise self._not_editable(id)
        if current.status != "ok":      # hidden or failed: nothing registered
            self.store.delete(id)
            del self._entries[id]
            return Change(id, set())
        users = self.used_by(id)
        if users:
            raise MetricError(f"{id} is still used by: {', '.join(users)}")
        derived = [m for m in self.registry.dependents_of(id) if "__" in m]
        for m in derived:               # transform metrics of a tab that is gone
            self.registry.unregister(m)
        self.registry.unregister(id)
        errors = self.registry.freeze()
        for mid, msg in errors:
            logger.error("User metrics: after deleting %s, %s: %s", id, mid, msg)
        self.store.delete(id)
        del self._entries[id]
        return Change(id, {id, *derived})

    # ── sessions (rules 15, 16) ─────────────────────────────────────────────
    def session_formulas(self) -> list[ExprMetricConfig]:
        """The formulas a saved session carries: every user metric that
        compiled, so the session opens the same on another server. (A session
        stores no use of a metric — no views or tabs — so there is nothing
        narrower to carry; see the ADR's "Found while building".)"""
        return [e.cfg for _i, e in sorted(self._entries.items()) if e.status == "ok"]

    @_locked
    def plan_session(self, formulas: list[ExprMetricConfig]) -> SessionPlan:
        """What loading a session with these formulas would add. A formula this
        server already has (same id, same formula) adds nothing. One whose id
        the server has with a different formula is a clash: it is added as
        ``<id>.2`` (the next free number), and the session's other formulas
        built on it are pointed at that. The server's own metric is untouched."""
        renamed: dict[str, str] = {}
        planned: set[str] = set()

        def remapped(cfg):
            vars_ = {n: renamed.get(r, r) for n, r in cfg.vars.items()}
            return cfg.model_copy(update={"vars": vars_, "id": renamed.get(cfg.id, cfg.id)})

        def taken(id_):
            return self.registry.has(id_) or id_ in self._entries or id_ in planned

        def same_as_server(cfg):
            have = self._entries.get(cfg.id)
            return have is not None and have.status == "ok" and have.cfg.expr == cfg.expr \
                and dict(have.cfg.vars) == dict(cfg.vars)

        changed = True
        while changed:
            changed = False
            for cfg in formulas:
                if cfg.id in renamed:
                    continue
                r = remapped(cfg)
                if taken(r.id) and not same_as_server(r):
                    n = 2
                    while taken(f"{cfg.id}.{n}"):
                        n += 1
                    renamed[cfg.id] = f"{cfg.id}.{n}"
                    planned.add(renamed[cfg.id])
                    changed = True
        plan = SessionPlan(renamed=renamed)
        for cfg in formulas:
            r = remapped(cfg)
            if r.id in planned or not taken(r.id):
                plan.add.append(r)
        return plan

    @_locked
    def add_session(self, plan: SessionPlan) -> Change:
        """Save what ``plan_session`` found. The formulas may build on each
        other, so add in rounds until a round adds nothing."""
        pending = list(plan.add)
        affected: set[str] = set()
        last: Exception | None = None
        while pending:
            added = []
            for cfg in pending:
                try:
                    change = self.save(cfg.model_dump(mode="json"))
                except MetricError as exc:
                    last = exc
                    continue
                affected |= change.affected
                added.append(cfg)
            if not added:
                raise MetricError(f"Could not add {pending[0].id}: {last}")
            pending = [c for c in pending if c not in added]
        return Change(plan.add[0].id, affected)

    def export(self, id: str) -> str:
        """The metric as TOML to paste into a project's ffast.toml."""
        entry = self._entries.get(id)
        if entry is None:
            raise MetricError(f"No user metric {id}")
        return _toml(entry.cfg)


def formulas_toml(cfgs: list[ExprMetricConfig]) -> str:
    """Formulas as one TOML text, an ``[[metrics.expr]]`` entry each."""
    return _SESSION_HEADER + "".join("\n" + _toml(c) for c in cfgs)


def read_formulas(path: Path | str) -> list[ExprMetricConfig]:
    """The formulas in a session's file; empty if there is none or it is unreadable."""
    try:
        data = tomllib.loads(Path(path).read_text())
        return [ExprMetricConfig.model_validate(e) for e in (data.get("metrics") or {}).get("expr") or []]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, ValidationError) as exc:
        logger.warning("Session formulas %s unreadable: %s", path, exc)
        return []


@dataclass
class SessionPlan:
    """What loading a session would add (rule 16).

    ``add`` are the formulas to save, in the order given, each under the id it
    will have; ``renamed`` maps the session's id to the new one for every clash
    (the server has that id with a different formula), so the session's own
    formulas built on it are pointed at the new id. An identical formula is not
    in ``add``."""
    add: list[ExprMetricConfig] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.add)

    def describe(self) -> list[dict]:
        """For the question put to the user: each metric, and the id it would get."""
        back = {new: old for old, new in self.renamed.items()}
        return [{"id": c.id, "label": c.label, "expr": c.expr,
                 "clash_with": back.get(c.id)} for c in self.add]


class _Without:
    """A registry view that does not see one metric — so a formula being edited
    is checked as if its old self were gone, and cannot depend on itself."""

    def __init__(self, registry, hidden_id: str) -> None:
        self._registry = registry
        self._hidden = hidden_id

    def has(self, metric_id) -> bool:
        return metric_id != self._hidden and self._registry.has(metric_id)

    def get(self, metric_id):
        if metric_id == self._hidden:
            raise KeyError(metric_id)
        return self._registry.get(metric_id)

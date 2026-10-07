"""User tabs and the three tab sources (ADR 0056 rules 9, 10, 14, 16).

A tab comes from one of three sources: **built-in** (``builtin_tabs/``),
**project** (a project's ``[[visualization.tabs]]``) or **user** (built in the
browser and saved by the server, one TOML file per tab in ``~/.ffast/tabs/``
on the server's machine). All three use the same format.

A user tab may **replace** a built-in or project tab: it takes the original's
place in the tab bar, and deleting it ("Reset to original") brings the
original back. Which tab a user tab replaces, the original's fingerprint when
it was edited (so the mark can say the original has changed since), the
**hidden** list and the order of new user tabs are app state, written by the
app, so they live in a side file, ``state.json``, and never in a tab file: a
tab file can be copied into a project, or sent to someone, as it is.

Pure Python and Qt-free; the server owns one :class:`TabSources` and the
browser reads and changes it over the wire. The desktop ignores user tabs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w
from pydantic import ValidationError

from ffast.config.models import PANEL_KIND_3D, AnalysisTabConfig

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path("~/.ffast/tabs")
STATE_FILE = "state.json"
MAIN_VIEW_RULE = "At least one tab must show the main view"

_HEADER = (
    "# A tab saved by FFAST (ADR 0056). Same format as a built-in tab: copy it\n"
    "# into another machine's ~/.ffast/tabs/, or use Export for a project's\n"
    "# ffast.toml.\n"
)


class TabError(ValueError):
    """A tab change the rules refuse; the message is shown to the user."""


def tab_fingerprint(tab: AnalysisTabConfig) -> str:
    """Identifies a tab's content: equal fingerprints, equal tabs."""
    text = json.dumps(tab.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _authoring(tab: AnalysisTabConfig) -> dict:
    """The tab as someone would write it: no defaults, no nulls."""
    return tab.model_dump(mode="json", exclude_defaults=True, exclude_none=True)


def _shows_main_view(tab: AnalysisTabConfig) -> bool:
    return any(p.kind == PANEL_KIND_3D and _is_linked(p) for p in tab.panels)


def _is_linked(panel) -> bool:
    return getattr(panel, "view", "linked") == "linked"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "tab"


@dataclass
class UserTab:
    file: str
    tab: AnalysisTabConfig
    replaces: str | None = None     # the built-in/project tab it stands in for
    original: str | None = None     # that tab's fingerprint when it was edited


class UserTabStore:
    """The tab files and ``state.json`` in one folder. Reads on demand, so a
    file copied in by hand appears at the next layout; writes at once."""

    def __init__(self, root: Path | str = DEFAULT_DIR) -> None:
        self.root = Path(root).expanduser()
        #: (file, message) for each tab file that could not be read
        self.errors: list[tuple[str, str]] = []

    # ── state.json ──────────────────────────────────────────────────────────
    def _state(self) -> dict:
        try:
            state = json.loads((self.root / STATE_FILE).read_text())
        except FileNotFoundError:
            state = {}
        except (OSError, ValueError) as exc:
            logger.warning("User tabs: %s unreadable (%s); starting it afresh", STATE_FILE, exc)
            state = {}
        return {
            "replaces": dict(state.get("replaces") or {}),
            "hidden": list(state.get("hidden") or []),
            "order": list(state.get("order") or []),
        }

    def _write_state(self, state: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / STATE_FILE).write_text(json.dumps(state, indent=2) + "\n")

    @property
    def hidden(self) -> list[str]:
        return self._state()["hidden"]

    def set_hidden(self, name: str, hidden: bool) -> None:
        state = self._state()
        names = [n for n in state["hidden"] if n != name]
        state["hidden"] = names + [name] if hidden else names
        self._write_state(state)

    # ── tab files ───────────────────────────────────────────────────────────
    def tabs(self) -> list[UserTab]:
        """Every readable tab file, in the order the tabs were made."""
        self.errors = []
        if not self.root.is_dir():
            return []
        state = self._state()
        found: dict[str, UserTab] = {}
        for path in sorted(self.root.glob("*.toml")):
            try:
                found[path.name] = UserTab(path.name, self._read(path))
            except (OSError, ValueError, ValidationError) as exc:
                self.errors.append((path.name, str(exc)))
                logger.warning("User tab %s skipped: %s", path, exc)
                continue
            record = state["replaces"].get(path.name) or {}
            found[path.name].replaces = record.get("tab")
            found[path.name].original = record.get("original")
        order = [f for f in state["order"] if f in found]
        order += [f for f in found if f not in order]   # copied in by hand
        return [found[f] for f in order]

    @staticmethod
    def _read(path: Path) -> AnalysisTabConfig:
        """One tab per file, as ``[[tabs]]`` (the built-in form) or
        ``[[visualization.tabs]]`` (an Export, pasted as it came)."""
        data = tomllib.loads(path.read_text())
        tabs = data.get("tabs") or (data.get("visualization") or {}).get("tabs") or []
        if len(tabs) != 1:
            raise ValueError(f"a tab file holds exactly one tab, found {len(tabs)}")
        return AnalysisTabConfig.model_validate(tabs[0])

    def write(self, tab: AnalysisTabConfig, *, file: str | None = None,
              replaces: str | None = None, original: str | None = None) -> str:
        """Write `tab` to `file` (a new file named after the tab if None) and
        record what it replaces. Returns the file name."""
        self.root.mkdir(parents=True, exist_ok=True)
        if file is None:
            file = f"{_slug(tab.name)}.toml"
            n = 2
            while (self.root / file).exists():
                file = f"{_slug(tab.name)}-{n}.toml"
                n += 1
        text = _HEADER + "\n" + tomli_w.dumps({"tabs": [_authoring(tab)]})
        tmp = self.root / f".{file}.tmp"
        tmp.write_text(text)
        tmp.replace(self.root / file)
        state = self._state()
        if replaces:
            state["replaces"][file] = {"tab": replaces, "original": original}
        if file not in state["order"]:
            state["order"].append(file)
        self._write_state(state)
        return file

    def delete(self, file: str, name: str) -> None:
        (self.root / file).unlink(missing_ok=True)
        state = self._state()
        state["replaces"].pop(file, None)
        state["order"] = [f for f in state["order"] if f != file]
        state["hidden"] = [n for n in state["hidden"] if n != name]
        self._write_state(state)


@dataclass
class TabEntry:
    """One tab of the merged bar."""
    tab: AnalysisTabConfig
    source: str                      # "built-in" | "project" | "user"
    file: str | None = None          # user tabs: the file in the store
    replaces: str | None = None      # user tabs: the original they stand in for
    original_changed: bool = False   # that original changed since the edit
    hidden: bool = False


@dataclass
class TabSources:
    """The built-in, project and user tabs, merged into the browser's tab bar.

    ``registry`` is the metric registry tabs resolve against; on a running
    server it is fixed, so a tab may only use metrics already there (rule 11).
    """
    builtin: list[AnalysisTabConfig]
    project: list[AnalysisTabConfig] = field(default_factory=list)
    store: UserTabStore | None = None
    registry: object = None

    @classmethod
    def for_project(cls, project_config=None, store: UserTabStore | None = None,
                    registry=None) -> "TabSources":
        from ffast.config.tabs import load_builtin_tabs
        project = list(project_config.visualization.tabs) if project_config is not None else []
        return cls(load_builtin_tabs(), project, store, registry)

    def compile(self) -> list[tuple[str, str]]:
        """Compile the metrics of every tab, user tabs included, before the
        registry is frozen (server startup). Fail-soft: returns (tab, message)
        for each tab that would not compile; that tab is left out of the
        layout later."""
        from ffast.config.tabs import compile_tabs_metrics
        errors = []
        for entry in self.entries():
            try:
                compile_tabs_metrics([entry.tab], registry=self.registry)
            except Exception as exc:
                errors.append((entry.tab.name, str(exc)))
        return errors

    # ── reading ─────────────────────────────────────────────────────────────
    def entries(self) -> list[TabEntry]:
        """Built-in, then project, then user tabs in the order they were made;
        a user tab that replaces another sits in that one's place."""
        user = self.store.tabs() if self.store else []
        hidden = set(self.store.hidden) if self.store else set()
        base = [(t, "built-in") for t in self.builtin] + [(t, "project") for t in self.project]
        base_names = {t.name for t, _ in base}
        standing_in = {u.replaces: u for u in user if u.replaces in base_names}
        out = []
        for tab, source in base:
            u = standing_in.get(tab.name)
            if u is None:
                out.append(TabEntry(tab, source, hidden=tab.name in hidden))
            else:
                out.append(TabEntry(
                    u.tab, "user", u.file, tab.name,
                    original_changed=u.original is not None and u.original != tab_fingerprint(tab),
                    hidden=u.tab.name in hidden))
        for u in user:
            if u.replaces not in base_names:   # a new tab, or its original is gone
                out.append(TabEntry(u.tab, "user", u.file, hidden=u.tab.name in hidden))
        return out

    def layout(self) -> list[dict]:
        """The TAB_LAYOUT payload: each tab as the browser draws it, plus where
        it comes from. A tab whose metrics cannot be resolved is left out."""
        from ffast.config.tabs import tab_layout
        out = []
        for e in self.entries():
            try:
                d = tab_layout(e.tab, registry=self.registry)
            except Exception as exc:
                logger.warning("Tab %r left out of the layout: %s", e.tab.name, exc)
                continue
            d.update(source=e.source, replaces=e.replaces,
                     original_changed=e.original_changed, hidden=e.hidden,
                     revision=tab_fingerprint(e.tab))
            out.append(d)
        return out

    def export(self, name: str) -> str:
        """The tab as TOML to paste into a project's ffast.toml."""
        entry = self._entry(name)
        return tomli_w.dumps({"visualization": {"tabs": [_authoring(entry.tab)]}})

    # ── changing ────────────────────────────────────────────────────────────
    def save(self, tab_data: dict, previous_name: str | None = None) -> str:
        """Save a tab built in the browser. `previous_name` is the tab it was
        edited from: a user tab is rewritten, a built-in or project tab gets a
        user tab replacing it; None makes a new tab. Returns the saved name."""
        store = self._writable()
        try:
            tab = AnalysisTabConfig.model_validate(tab_data)
        except ValidationError as exc:
            raise TabError(_validation_message(exc)) from None
        self._check_metrics(tab)

        entries = self.entries()
        editing = self._entry(previous_name, entries) if previous_name is not None else None
        if any(e.tab.name == tab.name and e is not editing for e in entries):
            raise TabError(f"A tab named {tab.name} already exists")
        after = [tab if e is editing else e.tab for e in entries if not e.hidden or e is editing]
        if editing is None:
            after.append(tab)
        if not any(_shows_main_view(t) for t in after):
            raise TabError(MAIN_VIEW_RULE)

        if editing is None:
            store.write(tab)
        elif editing.source == "user":
            store.write(tab, file=editing.file)
            if editing.hidden and editing.tab.name != tab.name:
                store.set_hidden(editing.tab.name, False)
        else:
            store.write(tab, replaces=editing.tab.name, original=tab_fingerprint(editing.tab))
        return tab.name

    def delete(self, name: str) -> None:
        """Delete a user tab; for one that replaces another, that is "Reset to
        original"."""
        store = self._writable()
        entry = self._entry(name)
        if entry.source != "user":
            raise TabError(f"{name} is a {entry.source} tab; only your own tabs can be deleted")
        entries = self.entries()
        rest = [e.tab for e in entries if e is not entry and not e.hidden]
        original = next((t for t in self.builtin + self.project if t.name == entry.replaces), None)
        if original is not None:
            rest.append(original)
        if not any(_shows_main_view(t) for t in rest):
            raise TabError(MAIN_VIEW_RULE)
        store.delete(entry.file, entry.tab.name)

    def set_hidden(self, name: str, hidden: bool) -> None:
        store = self._writable()
        self._entry(name)
        if hidden:
            shown = [e.tab for e in self.entries() if not e.hidden and e.tab.name != name]
            if not any(_shows_main_view(t) for t in shown):
                raise TabError(MAIN_VIEW_RULE)
        store.set_hidden(name, hidden)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _writable(self) -> UserTabStore:
        if self.store is None:
            raise TabError("This server keeps no user tabs")
        return self.store

    def _entry(self, name, entries=None) -> TabEntry:
        for e in entries if entries is not None else self.entries():
            if e.tab.name == name:
                return e
        raise TabError(f"No tab named {name}")

    def _check_metrics(self, tab: AnalysisTabConfig) -> None:
        """Every metric the tab binds must exist; on a frozen registry a new
        transform is not compiled (rule 11: later work)."""
        from ffast.config.tabs import resolve_ref
        from ffast.metrics.registry import default_registry
        registry = self.registry if self.registry is not None else default_registry
        existing = _ExistingOnly(registry)
        for panel in tab.panels:
            for role in panel.metrics.values():
                for ref in role if isinstance(role, list) else [role]:
                    try:
                        mid = resolve_ref(ref, registry=existing)
                    except Exception as exc:
                        raise TabError(f"{ref.metric}: {exc}") from None
                    if not registry.has(mid):
                        raise TabError(f"{mid}: this server has no such metric")


class _ExistingOnly:
    """A registry view that resolves refs to metrics already registered and
    makes no new ones: a running server's metric graph and worker pool are
    already set up, so a metric added now could not be computed."""

    def __init__(self, registry) -> None:
        self._registry = registry

    def has(self, metric_id) -> bool:
        return self._registry.has(metric_id)

    def get(self, metric_id):
        return self._registry.get(metric_id)

    def metric(self, *args, **kwargs):
        raise ValueError("uses a transform this server has not compiled; "
                         "add the tab to the project's ffast.toml and restart")


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    where = ".".join(str(p) for p in first.get("loc", ()))
    return f"{where}: {first.get('msg')}" if where else str(first.get("msg"))

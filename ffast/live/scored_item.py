"""A scored item on the server (ADR 0093): a dataset whose scores read like a live run's.

``ScoredItemDataset`` is a view of the scored dataset with every frame. Its
fingerprint starts with ``live:`` so the browser treats it as a run almost
everywhere. ``ScoredItem`` tails the item folder (scores, events, state) the
way ``LiveRun`` tails a run folder, through the shared ``AnalysisReader``, and
sends the same ``LIVE_RUN_UPDATE`` with ``kind: "scored"``.

Everything but the scores belongs to the parent dataset: its metric results are
cached under the parent's identity (``score_parent``, see ``input_resolver``).
Scored items are kept in ``env.scored_items``, never in ``env.live_runs``:
nothing that stops or lists runs may see them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from ffast.live import run_folder as rf
from ffast.live import scored_items as si
from ffast.live.analysis_reader import AnalysisReader
from ffast.live.live_run import LIVE_PREFIX, RESEND_S, POLL_S, _series_of
from ffast.loaders.dataset import DatasetView

logger = logging.getLogger("FFAST")

SCORED_PREFIX = LIVE_PREFIX + "scored-"
SHOWN_EVENTS = ("scoring_start", "scoring_done", "scoring_cancelled", "warning", "refused", "failed", "alarm")
EVENTS_KEPT = 200


def settings_text(settings: dict) -> str:
    """"0.5 fs · 5 ps", or "no time · 5 ps" when the file gave no time between frames."""
    dt = settings.get("frame_dt_ps")
    persist = float(settings.get("persist_ps") or 0)
    left = "no time" if dt is None else f"{dt * 1000:g} fs"
    return f"{left} · {persist:g} ps"


def item_name(parent_name: str, bundle_dir, settings: dict) -> str:
    from ffast.live.references import describe

    return f"{parent_name} · {describe(bundle_dir)['name']} · {settings_text(settings)}"


class ScoredItemDataset(DatasetView):
    """Every frame of ``parent``, standing for one scoring of it."""

    datasetName = "Scored item"

    def __init__(self, parent, iid: str, name: str) -> None:
        self.item_id = iid
        self._item_name = name
        self.analysis_source = None         # the ScoredItem, set by it
        super().__init__(parent)

    @property
    def score_parent(self):
        return self.parent

    @property
    def frozen(self):
        return False                        # never offered Freeze: its frames cannot move

    def getFingerprint(self):
        return SCORED_PREFIX + self.item_id

    def updatePath(self):
        self.path = self._item_name

    def lineageMeta(self) -> dict:
        return {**super().lineageMeta(), "scored": True}

    def getInfo(self):
        return [("Scored from", self.parent.getDisplayName()), ("Scored item", self._item_name)]


class ScoredItem(AnalysisReader):
    """One scored item folder: follows its scores and state."""

    kind_word = "scored item"

    def __init__(self, env, folder: Path, dataset: ScoredItemDataset, broadcast) -> None:
        self.env = env
        self.folder = Path(folder)
        self.dataset = dataset
        dataset.analysis_source = self
        self.run_id = dataset.item_id        # LIVE_ATTRIBUTION finds an item by this
        self.broadcast = broadcast
        self.registered = False
        self.error: str | None = None
        self.item: dict = si.read_item(self.folder) or {}
        self.events: list[dict] = []
        self.progress: dict = {}
        self._init_analysis()
        self._listed: tuple = ()
        self._last_sent = 0.0
        self.task: asyncio.Task | None = None
        self._steps = si._steps_of(dataset.parent)

    # ── names ───────────────────────────────────────────────────────────────
    @property
    def display_name(self) -> str:
        return self.dataset.getName()

    @property
    def tab_name(self) -> str:
        return f"Scores {self.display_name}"

    @property
    def fingerprint(self) -> str:
        return self.dataset.fingerprint

    def frame_count(self) -> int:
        return int(self.dataset.getN())

    def step_of(self, frame: int):
        return int(self._steps[frame]) if self._steps is not None else int(frame)

    @property
    def steps(self) -> list[int]:
        n = self.frame_count()
        return [int(s) for s in self._steps] if self._steps is not None else list(range(n))

    # ── reading (any thread) ────────────────────────────────────────────────
    def read_files(self) -> dict:
        rows = self.read_analysis(self.folder)
        progress = {}
        if self.analysis_id:
            progress = rf.read_json(self.folder / rf.ANALYSIS_DIR / self.analysis_id / rf.PROGRESS) or {}
        events = [e for e in rf.read_events(self.folder) if e.get("kind") in SHOWN_EVENTS]
        return {"rows": rows, "item": si.read_item(self.folder) or self.item,
                "progress": progress, "events": events}

    # ── applying (the server's event loop) ──────────────────────────────────
    def apply(self, got: dict) -> bool:
        changed = bool(got["rows"]) or got["item"] != self.item or got["progress"] != self.progress \
            or got["events"] != self.events
        self.item, self.progress, self.events = got["item"], got["progress"], got["events"]
        self.rows += got["rows"]
        from ffast.metrics import run_values

        if self.registered and run_values.keys_of(self) != list(self._listed):
            self._listed = tuple(run_values.keys_of(self))
            run_values.sync(self.env)
        if got["rows"]:
            # A result stored before these scores came (or a failure for scores not there
            # yet) would otherwise be served for the longer series.
            store = getattr(self.env, "results", None)
            if store is not None:
                store.dataset_grew(self.fingerprint)
        return changed

    def refused_frames(self) -> dict:
        return {int(r["frame"]): r["refused"] for r in list(self.rows) if "refused" in r}

    def message(self) -> bytes:
        from ffast.protocol import control
        from ffast.protocol.rpc import pack

        return pack(control.LIVE_RUN_UPDATE, (self.run_id,), {
            "kind": "scored",
            "run_id": self.run_id,
            "parent": self.dataset.parent.fingerprint,
            "state": self.item.get("state"),
            "reason": self.item.get("reason"),
            "refused_frames": self.refused_frames(),
            "practice": None,
            "paused": False,
            "path": str(self.folder),
            "fingerprint": self.fingerprint if self.registered else None,
            "tab": self.tab_name if self.registered else None,
            "name": self.display_name,
            "steps": self.steps,
            "n_frames": self.frame_count(),
            "analysis_id": self.analysis_id,
            "bundle": self.settings.get("bundle"),
            "role": self.settings.get("role", "centroid"),
            "series": _series_of(self.rows, self.settings),
            "progress": self.progress,
            "producer": None,
            "end": None,
            "stop": None,
            "stop_requested": False,
            "stop_on_alarm": False,
            "events": self.events[-EVENTS_KEPT:],
            "error": self.error,
        })

    def announce(self) -> None:
        self._last_sent = time.monotonic()
        self.broadcast(self.message())

    async def follow(self, poll_s: float = POLL_S) -> None:
        """Read new scores until the dataset is closed; then hide the item (its
        scores stay on disk, and scoring again with the same settings brings it back)."""
        from ffast.live.live_run import _tabs_changed

        while True:
            got = await asyncio.to_thread(self.read_files)
            if self.registered and self.env.datasets.get(self.fingerprint) is not self.dataset:
                logger.info("scored item %s: dataset closed, hidden", self.run_id)
                si.set_hidden(self.folder, True)
                self.env.scored_items.pop(self.run_id, None)
                _tabs_changed(self.env)
                from ffast.metrics import run_values

                run_values.sync(self.env)
                return
            if self.apply(got) or time.monotonic() - self._last_sent > RESEND_S:
                self.announce()
            await asyncio.sleep(poll_s)


def open_scored_item(env, folder: Path, parent, broadcast) -> ScoredItem:
    """Register the item in ``folder`` under ``parent``, start following it.

    Opening one already open returns it. The dataset is added at once, so the
    item appears in the list before its first score."""
    from ffast.live.live_run import _tabs_changed, live_tabs  # noqa: F401 — tabs read env.scored_items

    item = si.read_item(folder)
    if not item:
        raise ValueError(f"{folder} is not a scored-item folder")
    items = getattr(env, "scored_items", None)
    if items is None:
        items = env.scored_items = {}
    existing = items.get(item["item_id"])
    if existing is not None and existing.task is not None and not existing.task.done():
        existing.announce()
        return existing
    settings = item.get("settings") or {}
    dataset = ScoredItemDataset(parent, item["item_id"], item_name(parent.getName(), item["bundle"], settings))
    dataset.initialise()
    scored = ScoredItem(env, folder, dataset, broadcast)
    items[scored.run_id] = scored
    sources = getattr(env, "tab_sources", None)
    if sources is not None and sources.live is None:
        sources.live = lambda layout: live_tabs(env, layout)
    env.datasets.add(dataset)
    scored.registered = True
    _tabs_changed(env)
    scored.apply(scored.read_files())
    scored.announce()
    scored.task = asyncio.ensure_future(scored.follow())
    return scored


def reopen_scored_items(env, parent, broadcast) -> list[ScoredItem]:
    """Bring back the scored items saved for ``parent`` (not hidden), after the same
    file is loaded again (Decided 10). Runs on the server's event loop."""
    out = []
    for folder in si.find_items(parent.getFingerprint()):
        try:
            out.append(open_scored_item(env, folder, parent, broadcast))
        except Exception as exc:    # a damaged item folder must not stop the load
            logger.warning("scored item %s could not be reopened: %s", folder.name, exc)
    return out

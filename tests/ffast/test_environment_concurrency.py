"""Concurrency hardening tests (ADR 0044 Phase 3).

Two controllers loading/deleting at once used to be able to run their
loadDataset/loadModel bodies concurrently on separate worker threads
(``asyncio.to_thread``), racing on the shared Environment's dataset/model
registries. ``Environment.mutation_lock`` (client/environment.py) now
serializes the registry-mutating tail of each load/delete; these tests prove
it actually excludes concurrent access rather than just existing.
"""
from __future__ import annotations

import asyncio
import threading
import time

from client.environment import Environment
from client.loading_coordinator import LoadingCoordinator


def _ensure_event_loop():
    """Other suite tests may close/clear the process-global event loop; the
    headless Environment's TaskManager grabs asyncio.get_event_loop() at
    construction, which raises on a cleared loop. A real process always has
    one — ensure the same here so this test is order-independent."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


class TestDeleteObjectMutationLock:
    def test_concurrent_deletes_never_overlap(self):
        _ensure_event_loop()
        env = Environment(headless=True)
        state = {"in_section": 0, "max_concurrent": 0}
        guard = threading.Lock()

        class _SlowDatasets:
            def exists(self, key):
                return key in ("a", "b")

            def delete(self, key):
                with guard:
                    state["in_section"] += 1
                    state["max_concurrent"] = max(
                        state["max_concurrent"], state["in_section"]
                    )
                time.sleep(0.05)  # wide enough that a race would overlap
                with guard:
                    state["in_section"] -= 1

        class _NoModels:
            def exists(self, key):
                return False

        env.datasets = _SlowDatasets()
        env.models = _NoModels()

        t1 = threading.Thread(target=env.deleteObject, args=("a",))
        t2 = threading.Thread(target=env.deleteObject, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert state["max_concurrent"] == 1


class TestLoadModelMutationLock:
    def test_registry_mutation_runs_inside_the_lock(self, tmp_path):
        model_path = tmp_path / "model.bin"
        model_path.write_text("x")

        class _FakeModel:
            def __init__(self, env, path):
                pass

            def initialise(self):
                pass

        class _RecordingLock:
            def __init__(self):
                self._lock = threading.Lock()
                self.entries = 0

            def __enter__(self):
                self.entries += 1
                self._lock.acquire()

            def __exit__(self, *exc_info):
                self._lock.release()

        class _FakeModels:
            def __init__(self):
                self.added = []

            def add(self, model):
                self.added.append(model)

        class _FakeEnv:
            def __init__(self):
                self.modelTypes = {"zero": _FakeModel}
                self.models = _FakeModels()
                self.mutation_lock = _RecordingLock()

        env = _FakeEnv()
        LoadingCoordinator(env).loadModel(str(model_path), "zero")

        assert env.mutation_lock.entries == 1
        assert len(env.models.added) == 1


class TestLoadDatasetKeepsFileReadOutsideTheLock:
    """Regression: prediction_keys' atomsList fallback used to run INSIDE
    mutation_lock, so a slow re-read of the file (dataset without a cached
    ``.atomsList``) would hold the lock for the read's duration — and since
    ``_on_delete_object`` acquires the same lock synchronously on the event
    loop thread (no ``asyncio.to_thread``), that would freeze the whole event
    loop, not just serialize the registry mutation. loadDataset now resolves
    atomsList before entering the lock."""

    def test_atomslist_resolution_happens_before_lock_is_entered(self, tmp_path):
        dataset_path = tmp_path / "dataset.xyz"
        dataset_path.write_text("x")
        events = []

        class _FakeDataset:
            """Stands in for both the loader class AND its returned dataset —
            ``datasetTypes["ase (auto)"](path, **kwargs)`` constructs one of
            these directly. Deliberately no ``.atomsList`` attribute, forcing
            the ``ase.io.read`` fallback loadDataset must keep unlocked."""
            def __init__(self, path, **kwargs):
                pass

            def initialise(self):
                pass

        class _RecordingLock:
            def __enter__(self):
                events.append("lock-enter")

            def __exit__(self, *exc_info):
                events.append("lock-exit")

        class _FakeDatasets:
            def add(self, dataset, slice_num):
                pass

        class _FakeEnv:
            def __init__(self):
                self.datasetTypes = {"ase (auto)": _FakeDataset}
                self.datasets = _FakeDatasets()
                self.mutation_lock = _RecordingLock()

        coord = LoadingCoordinator(_FakeEnv())
        coord._loadPredictionsFromKeys = lambda *a, **k: events.append("predictions")
        coord.lookForGhosts = lambda: events.append("ghosts")

        def _fake_ase_read(path, index):
            events.append("file-read")
            return []

        import ase.io
        real_read = ase.io.read
        ase.io.read = _fake_ase_read
        try:
            coord.loadDataset(
                str(dataset_path), "ase (auto)",
                prediction_keys=[("e", "f", "m")],
            )
        finally:
            ase.io.read = real_read

        assert events == ["file-read", "lock-enter", "predictions", "ghosts", "lock-exit"]

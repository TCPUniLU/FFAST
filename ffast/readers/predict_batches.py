"""Model predictions over memory-saving datasets, one frame batch at a time."""

from __future__ import annotations

import numpy as np

from ffast.core.flat_frames import FlatFrames
from ffast.io.batched import BatchedArray
from ffast.io.budget import budget
from ffast.io.result_array import result_writer
from ffast.io.extxyz_arrays import NotEnoughMemory


def predict(model, dataset, task_id=None):
    offsets = np.asarray(dataset.getOffsets())
    if not isinstance(dataset.getAllCoordinates(), BatchedArray):
        wanted = 3 * (int(offsets[-1]) * 24 + dataset.getN() * 8)
        try:
            reservation = budget().reserve(wanted)
            lease = reservation.__enter__()
        except NotEnoughMemory:
            pass  # The source fits, but predictions need bounded batches.
        else:
            try:
                e, f = model.predict(dataset, taskID=task_id)
                e = np.asarray(e)
                f = FlatFrames.of(f)
                budget().retain(e, lease)
                budget().retain(f.values, lease)
                return e, f
            finally:
                reservation.__exit__(None, None, None)
    energy = result_writer((dataset.getN(),), float)
    try:
        forces = result_writer((int(offsets[-1]), 3), float)
    except BaseException:
        energy.abort()
        raise
    try:
        a = 0
        while a < dataset.getN():
            # Inputs, model preprocessing and output buffers share RAM.
            rows = max(1, budget().block_bytes // 128)
            b = max(
                a + 1,
                int(np.searchsorted(offsets, offsets[a] + rows, side="right"))
                - 1,
            )
            b = min(b, dataset.getN())
            with budget().reserve(int(offsets[b] - offsets[a]) * 128):
                e, f = model.predict(
                    dataset, indices=np.arange(a, b), taskID=None
                )
                energy.append(np.asarray(e).reshape(-1))
                forces.append(FlatFrames.of(f).values)
            if task_id is not None:
                model.eventPush(
                    "TASK_PROGRESS",
                    task_id,
                    progMax=dataset.getN(),
                    prog=b,
                    message=f"Predicting: {b:,} of {dataset.getN():,} frames…",
                )
            a = b
        return energy.finish(), FlatFrames(forces.finish(), offsets)
    except BaseException:
        energy.abort()
        forces.abort()
        raise

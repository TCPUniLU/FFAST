import numpy as np


class FlatFrames:
    """Per-atom values of many frames in one array (ADR 0080): ``values``,
    every atom frame after frame, and ``offsets``, where each frame starts."""

    def __init__(self, values, offsets):
        values = np.asarray(values)
        offsets = np.asarray(offsets, dtype=np.int64)
        if (offsets.ndim != 1 or len(offsets) == 0 or offsets[0] != 0
                or np.any(np.diff(offsets) < 0) or offsets[-1] != len(values)):
            raise ValueError(
                "offsets must start at 0, never decrease and end at len(values)")
        self.values = values
        self.offsets = offsets

    @classmethod
    def of(cls, value):
        """A FlatFrames from a FlatFrames, a list of per-frame arrays or a
        ``(frames, atoms, ...)`` array."""
        if isinstance(value, cls):
            return value
        if isinstance(value, (list, tuple)):
            if len(value) == 0:
                return cls(np.zeros((0, 3)), [0])
            parts = [np.asarray(v) for v in value]
            counts = np.array([len(p) for p in parts], dtype=np.int64)
            return cls(np.concatenate(parts), np.concatenate(([0], np.cumsum(counts))))
        arr = np.asarray(value)
        if arr.ndim >= 3:
            frames, atoms = arr.shape[:2]
            return cls(arr.reshape(-1, *arr.shape[2:]),
                       np.arange(frames + 1, dtype=np.int64) * atoms)
        raise ValueError(
            "a plain array does not say where each frame starts; "
            "pass FlatFrames(values, offsets)")

    def __len__(self):
        return len(self.offsets) - 1

    def __getitem__(self, i):
        if isinstance(i, (bool, np.bool_)) or not isinstance(i, (int, np.integer)):
            raise TypeError("index a FlatFrames with one integer; use frames() for several")
        i = int(i)
        n = len(self)
        if i < 0:
            i += n
        if not 0 <= i < n:
            raise IndexError("frame index out of range")
        return self.values[self.offsets[i]:self.offsets[i + 1]]

    def __iter__(self):
        for i in range(len(self)):
            yield self[i]

    def frames(self, idx):
        """The frames ``idx`` (in that order, repeats allowed) as a new FlatFrames."""
        idx = np.asarray(idx, dtype=np.int64).reshape(-1)
        starts = self.offsets[idx]
        counts = self.offsets[idx + 1] - starts
        within = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
        flat = np.repeat(starts, counts) + within
        return FlatFrames(self.values[flat], np.concatenate(([0], np.cumsum(counts))))

    def atoms(self, flat_index, offsets):
        """The values at ``flat_index``, framed by the new ``offsets``."""
        return FlatFrames(self.values[flat_index], offsets)

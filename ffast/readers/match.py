"""The one check every prediction passes before it attaches (ADR 0095, decision 6).

Whatever read the prediction (a reader, ASE, an ``.npz``, or the dataset's own
file) describes it as ``Facts``; the check compares them with the dataset.

- **Refused** (``PredictionRefused``): a different frame count, different atoms
  per frame, or different elements when the file has them. The message names
  the first frame that differs.
- **Warned**: positions that differ by more than 0.001 Å (files often round
  them), and unit labels that differ from the dataset's. FFAST never converts
  units.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ffast.chemistry import zIntToZStr
from ffast.readers.contract import ReaderError, warn

POSITION_TOLERANCE = 0.001   # Å


class PredictionRefused(ReaderError):
    """The prediction does not match its dataset; the text says what differs."""


@dataclass
class Facts:
    """What a prediction file holds, for the frames it gave. ``None`` means
    the file does not say, and that check is skipped."""

    name: str                  # the prediction's name, as the messages quote it
    n_kept: int | None         # frames it gave (after the stride)
    n_file_frames: int | None = None   # frames its file has (before the stride)
    counts: np.ndarray | None = None   # atoms of each kept frame
    numbers: np.ndarray | None = None  # atomic numbers, every kept atom
    positions: np.ndarray | None = None  # (atoms, 3), every kept atom
    units: dict | None = None  # dimension -> label


def check_prediction(dataset, facts: Facts) -> list[str]:
    """Raise PredictionRefused when ``facts`` do not fit ``dataset``; else
    return the warnings it also sent (through ``warn``)."""
    name = facts.name
    offsets = _call(dataset, "getOffsets")
    label = _frame_label(dataset)

    source = getattr(dataset, "sourceFrameCount", None)
    if facts.n_file_frames is not None and source is not None and facts.n_file_frames != source:
        raise PredictionRefused(
            f"Prediction '{name}' has {facts.n_file_frames:,} frames, but the dataset's file "
            f"has {source:,}.")
    if facts.n_kept is not None and facts.n_kept != dataset.getN():
        raise PredictionRefused(
            f"Frame count mismatch for prediction '{name}'. "
            f"Expected {dataset.getN()} frames, got {facts.n_kept}.")

    if facts.counts is not None and offsets is not None:
        mine, theirs = np.asarray(facts.counts), np.diff(np.asarray(offsets))
        differ = np.flatnonzero(mine != theirs)
        if differ.size:
            i = int(differ[0])
            raise PredictionRefused(
                f"Atom count mismatch for prediction '{name}': frame {label(i)} has "
                f"{int(mine[i])} atoms, the dataset's frame {label(i)} has {int(theirs[i])}.")

        elements = _call(dataset, "getAllElements")
        if facts.numbers is not None and elements is not None:
            numbers = np.asarray(facts.numbers)
            elements = np.asarray(elements)
            if numbers.shape == elements.shape:
                differ = np.flatnonzero(numbers != elements)
                if differ.size:
                    k = int(differ[0])
                    frame = int(np.searchsorted(offsets, k, side="right") - 1)
                    raise PredictionRefused(
                        f"Element mismatch for prediction '{name}': in frame {label(frame)}, "
                        f"atom {k - int(offsets[frame])} is {zIntToZStr[int(numbers[k])]} but the "
                        f"dataset's is {zIntToZStr[int(elements[k])]}.")

    problems = []
    positions = _call(dataset, "getAllCoordinates")
    if facts.positions is not None and positions is not None and offsets is not None:
        theirs, mine = np.asarray(positions), np.asarray(facts.positions)
        if theirs.shape == mine.shape and mine.size:
            gap = np.abs(mine - theirs).max(axis=1)
            far = np.flatnonzero(gap > POSITION_TOLERANCE)
            if far.size:
                frame = int(np.searchsorted(offsets, far[0], side="right") - 1)
                problems.append(
                    f"Prediction '{name}': positions differ from the dataset's by up to "
                    f"{float(gap.max()):.4f} Å (first in frame {label(frame)}). "
                    f"A file may round its positions.")

    mine = facts.units or {}
    theirs = _call(dataset, "getUnits") or {}
    for dimension in sorted(mine):
        if dimension in theirs and mine[dimension] != theirs[dimension]:
            problems.append(
                f"Prediction '{name}': its {dimension} unit is {mine[dimension]}, the "
                f"dataset's is {theirs[dimension]}. FFAST does not convert units.")

    for text in problems:
        warn(text)
    return problems


def _call(dataset, method):
    """A dataset's answer, or None when it does not have the method."""
    fn = getattr(dataset, method, None)
    return fn() if callable(fn) else None


def _frame_label(dataset):
    """Frame number as the user knows it: the file's, when the dataset kept
    every n-th frame of it."""
    kept = getattr(dataset, "sourceFrames", None)
    return (lambda i: int(kept[i]) if kept is not None and i < len(kept) else i)

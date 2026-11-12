"""What a reader author imports and returns (ADR 0095).

A reader is a small class that returns plain numpy arrays and never touches
FFAST's own objects. This module holds the two names an author raises or
calls, and the shapes of the two dicts a reader describes itself with.
"""
from __future__ import annotations

import logging
from typing import TypedDict

# The logger every load warning uses (``FFAST``): the warning relay sends its
# WARNING records to every window as a pop-up that the notification bell keeps
# (ADR 0066).
logger = logging.getLogger("FFAST")


class ReaderError(Exception):
    """A problem the user can act on. The pop-up shows exactly this text, with
    no traceback: ``ReaderError("Frame 12 has no forces")``."""


def warn(text: str) -> None:
    """Warn the user and go on loading: the text goes to the pop-ups and the
    bell list. ``warn("3 frames have no cell")``."""
    logger.warning(text)


class Contents(TypedDict, total=False):
    """What a file holds, as ``reader.contents()`` returns it. Every key may
    be left out: a missing one means "none"."""

    positions: bool              # the file has atom positions
    numbers: bool                # ... atomic numbers
    cell: bool                   # ... a cell per frame
    energies: list[str]          # the names of its energy sets, ``energy:<name>`` in read()
    forces: list[str]            # the names of its force sets, ``forces:<name>`` in read()
    frame_fields: list[str]      # extra per-frame values, ``frame:<field>`` in read()
    atom_fields: list[str]       # extra per-atom values, ``atom:<field>`` in read()
    units: dict[str, str]        # dimension -> label, for example {"energy": "eV"}


class CanWrite(TypedDict, total=False):
    """What a format can hold when a reader also writes (ADR 0095, decision 11).
    A missing key means "no limit". A reader lists its formats in
    ``write_formats``, each ``{"format", "label", "ext", "can_write"}``, and
    gives ``open_writer(path, contents, format)``: an object with
    ``write(chunk)`` (several times, the dict ``read()`` returns) and
    ``close()``, and a ``skipped`` count of frames it left out."""

    frames: int                  # at most this many structures
    energies: int                # at most this many energy sets
    forces: int                  # at most this many force sets
    cell: bool                   # False: the format has no cell
    frame_fields: bool
    atom_fields: bool
    variable_atoms: bool         # False: every frame must have the same atoms

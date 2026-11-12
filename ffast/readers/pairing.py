"""Pairing a file's energy names with its force names (ADR 0095, decision 5).

A reader lists energy names and force names separately. FFAST pairs the ones
that match (``dft`` with ``dft``); the user can change the pairing.
"""
from __future__ import annotations

from ffast.readers.contract import ReaderError


def pair_names(energies, forces, energy=None, force=None, file="the file"):
    """The ``(energy name, force name)`` one prediction or reference takes.

    ``energy`` and ``force`` are the user's choices: ``None`` leaves the side
    to FFAST, ``""`` means "none" and is kept. A side left to FFAST takes the
    same-named partner of the other side, else its own first name. With both
    left to FFAST, the first energy name that also names forces wins. Either
    result is ``None`` when the file has no names of that kind (or the user
    chose none). An unknown chosen name is a ReaderError listing the names.
    """
    for what, chosen, names in (("energy", energy, energies), ("force", force, forces)):
        if chosen and chosen not in names:
            listed = ", ".join(names) or "none"
            raise ReaderError(f"{file} has no {what} named '{chosen}' (it has: {listed})")
    first = lambda names: names[0] if names else None
    if energy is None and force is None:
        match = next((n for n in energies if n in forces), None)
        return (match, match) if match else (first(energies), first(forces))
    if energy is None:
        energy = force if force and force in energies else first(energies)
    elif force is None:
        force = energy if energy and energy in forces else first(forces)
    return (energy or None, force or None)

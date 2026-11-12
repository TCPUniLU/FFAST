"""A file's energies for a sample by energy (ADR 0096): which energy, its
histogram for the load dialog, and the preview of a sample.

The energies come from the reader's ``energies`` pass (``text.get_energies``),
read once per file and kept. Energies are per atom when the frames' atom
counts differ (decision 6).
"""
from __future__ import annotations

import os

import numpy as np

from ffast.readers.contract import ReaderError

HISTOGRAM_BINS = 200     # the probe's fine histogram: the dialog draws it before a preview


def file_energies(reader_class, path):
    """``(energies {name: per-frame array}, atom counts)`` of ``path``.
    ReaderError when the reader does not list energies, or the file has none."""
    from ffast.readers import text

    name = os.path.basename(path)
    if not callable(getattr(reader_class, "energies", None)):
        raise ReaderError(f"{name} cannot be sampled by energy: the {reader_class.name} "
                          f"reader does not list energies")
    energies = text.get_energies(reader_class, path)
    if not energies:
        raise ReaderError(f"{name} cannot be sampled by energy: it has no energies")
    return energies, text.get_table(reader_class, path)[1]


def choose_energy(reader_class, path, energies, wanted=None):
    """The energy name to sample by: ``wanted`` when given, else the first
    name the reader lists that the file has, else the first the file has."""
    if wanted:
        if wanted not in energies:
            raise ReaderError(f"{os.path.basename(path)} has no energy named '{wanted}' to "
                              f"sample by; it has: {', '.join(energies)}")
        return wanted
    listed = reader_class(path).contents().get("energies") or []
    return next((e for e in listed if e in energies), next(iter(energies)))


def per_atom_values(values, counts):
    """``(values to bin, per_atom)``: per atom when atom counts differ."""
    counts = np.asarray(counts)
    if np.unique(counts).size > 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.asarray(values, dtype=float) / counts, True
    return np.asarray(values, dtype=float), False


def energy_histogram(reader_class, path, energy=None, bins=HISTOGRAM_BINS):
    """The probe's answer: energy names, the one shown, per atom or total, a
    fine histogram (``bins`` equal bins from min to max) and the frames left
    out (no energy). Binned here: a file can have millions of frames."""
    energies, counts = file_energies(reader_class, path)
    chosen = choose_energy(reader_class, path, energies, energy)
    values, per_atom = per_atom_values(energies[chosen], counts)
    finite = values[np.isfinite(values)]
    if finite.size:
        hist, edges = np.histogram(finite, bins=bins, range=(finite.min(), finite.max()))
    else:
        hist, edges = np.zeros(0, dtype=int), np.zeros(0)
    return {
        "names": list(energies), "energy": chosen, "per_atom": per_atom,
        "edges": edges.tolist(), "counts": hist.tolist(),
        "n_frames": int(len(values)), "left_out": int(len(values) - finite.size),
    }


def energy_preview(reader_class, path, sampling):
    """What a load with ``sampling`` would keep, per bin: the sample's own
    edges, all frames and kept frames per bin. ``error`` names what is wrong
    with the settings instead."""
    from ffast.core.loading_coordinator import _samplingSettings
    from ffast.readers import sampling as energy_sampling

    try:
        energies, counts = file_energies(reader_class, path)
        chosen = choose_energy(reader_class, path, energies, (sampling or {}).get("energy"))
        settings = _samplingSettings(sampling, chosen)
        result = energy_sampling.sample_by_energy(
            energies[chosen], settings["n"], method=settings["method"],
            bins=settings["bins"], seed=settings["seed"], n_atoms=counts)
    except (ReaderError, ValueError) as e:
        return {"error": str(e)}
    return {
        "error": None, "energy": chosen, "per_atom": result.per_atom,
        "edges": result.edges.tolist(), "counts_all": result.counts_all.tolist(),
        "counts_kept": result.counts_kept.tolist(), "bins_used": result.bins_used,
        "left_out": result.left_out, "kept": int(len(result.frames)),
        "kept_all": result.kept_all, "n_frames": int(len(counts)),
    }

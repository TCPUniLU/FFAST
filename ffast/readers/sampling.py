"""Choose frames by energy (ADR 0096).

One pure function, numpy only. It takes one energy per frame and returns the
frame numbers to keep, plus the histograms the load dialog shows.

Bins are equal-width, from the lowest to the highest energy. Two ways to share
the N frames among them:

* ``"even"``: about the same number from each non-empty bin, so the rare
  high-energy frames are not drowned by the many low-energy ones;
* ``"proportional"``: each bin's share follows how many frames it holds,
  rounded up, so every non-empty bin keeps at least one frame.

The number of frames per bin never depends on the seed. The seed only decides
which frames of a bin are picked.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

METHODS = ("even", "proportional")
DEFAULT_SEED = 42


@dataclass(frozen=True)
class SampleResult:
    frames: np.ndarray        # sorted file frame numbers (int64)
    edges: np.ndarray         # bin edges, bins_used + 1 values
    counts_all: np.ndarray    # frames with an energy, per bin
    counts_kept: np.ndarray   # kept frames, per bin
    bins_used: int
    per_atom: bool            # energies were divided by the atom count
    left_out: int             # frames with a missing or non-finite energy
    kept_all: bool            # N was not below the frames with an energy: all kept


def automatic_bins(values: np.ndarray, n: int) -> int:
    """Freedman-Diaconis: width = 2 * IQR / cbrt(N), capped at N / 2 bins."""
    cap = max(1, n // 2)
    span = float(values.max() - values.min())
    if span == 0.0:
        return 1
    q1, q3 = np.percentile(values, [25, 75])
    width = 2.0 * (q3 - q1) / n ** (1.0 / 3.0)
    if not width > 0.0:                 # IQR 0: most frames equal, a few far away
        return cap
    return int(max(1, min(math.ceil(span / width), cap)))


def sample_by_energy(energies, n, *, method="even", bins=None, seed=DEFAULT_SEED,
                     n_atoms=None) -> SampleResult:
    """Pick ``n`` frames spread over the energy range. See the module text."""
    if method not in METHODS:
        raise ValueError(f"Unknown sampling method {method!r}; use 'even' or 'proportional'.")
    n = int(n)
    if n < 2:
        raise ValueError("Sampling by energy needs to keep at least 2 frames.")
    energies = np.asarray(energies, dtype=float)
    per_atom = False
    if n_atoms is not None:
        n_atoms = np.asarray(n_atoms)
        if n_atoms.shape != energies.shape:
            raise ValueError("n_atoms must have one value per frame.")
        per_atom = bool(np.unique(n_atoms).size > 1)
        if per_atom:
            with np.errstate(divide="ignore", invalid="ignore"):
                energies = energies / n_atoms
    usable = np.isfinite(energies)
    left_out = int((~usable).sum())
    index = np.flatnonzero(usable)              # file frame numbers that have an energy
    if index.size == 0:
        raise ValueError("The file has no energies to sample by.")
    values = energies[index]
    kept_all = n >= index.size
    n_eff = min(n, index.size)

    n_bins = automatic_bins(values, n_eff) if bins is None else int(bins)
    if n_bins < 1:
        raise ValueError("The number of bins must be at least 1.")
    lo, hi = float(values.min()), float(values.max())
    edges = np.linspace(lo, hi, n_bins + 1)
    which = np.digitize(values, edges[1:-1]) if n_bins > 1 else np.zeros(values.size, dtype=int)
    counts_all = np.bincount(which, minlength=n_bins)

    if kept_all:
        shares = counts_all.copy()
    elif method == "even":
        shares = _even_shares(counts_all, n_eff)
    else:
        shares = _proportional_shares(counts_all, n_eff)

    rng = np.random.default_rng(seed)
    chosen = []
    for b in range(n_bins):
        if shares[b] == 0:
            continue
        members = index[which == b]
        if shares[b] >= members.size:
            chosen.append(members)
        else:
            chosen.append(rng.choice(members, size=int(shares[b]), replace=False))
    frames = np.sort(np.concatenate(chosen)).astype(np.int64)
    return SampleResult(frames=frames, edges=edges, counts_all=counts_all,
                        counts_kept=shares.astype(np.int64), bins_used=n_bins,
                        per_atom=per_atom, left_out=left_out, kept_all=kept_all)


def _even_shares(counts: np.ndarray, n: int) -> np.ndarray:
    """Equal shares, a short bin gives all it has, the rest is shared again."""
    shares = np.zeros_like(counts)
    remaining = n
    while remaining > 0:
        active = np.flatnonzero(shares < counts)
        if active.size == 0:
            break
        base, extra = divmod(remaining, active.size)
        for rank, b in enumerate(active):
            give = min(base + (1 if rank < extra else 0), counts[b] - shares[b])
            shares[b] += give
            remaining -= give
    return shares


def _proportional_shares(counts: np.ndarray, n: int) -> np.ndarray:
    """ceil(share of the frames * N), then correct the total one frame at a time."""
    total = int(counts.sum())
    exact = counts / total * n
    shares = np.minimum(counts, np.ceil(exact).astype(counts.dtype))
    while shares.sum() > n:
        # Take from the bin that was rounded up the most, so the correction is spread
        # over the bins and none is thinned by more than about one frame.
        above_one = np.flatnonzero(shares > 1)
        pool = above_one if above_one.size else np.flatnonzero(shares > 0)
        shares[pool[np.argmax(shares[pool] - exact[pool])]] -= 1
    while shares.sum() < n:
        room = np.flatnonzero(shares < counts)
        shares[room[np.argmax(counts[room])]] += 1
    return shares

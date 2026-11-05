"""Attribution of one live frame, computed when asked (ADR 0087, plan decision 19).

DetectAna's reconstruction-based contribution (RBC) per descriptor group, for
one test (track and score), divided by that score's threshold for the run's
role. That ratio is the **attribution strength** shown on a fixed 0–2 scale:
strengths overlap and never sum to the score, so they are never called shares.
"""

from __future__ import annotations

import re

import numpy as np

TOP_K = 10
_UNIT = re.compile(r"_(Å|rad|deg)$")
_WORDS = {"dih": "dihedral", "rmsd": "RMSD"}


def group_label(group_id: str) -> str:
    """"bonds:bond_C0-C2_Å" → "bond C0-C2"; "dihedrals:dih_C3-C2-C0-C5" → "dihedral C3-C2-C0-C5"."""
    name = group_id.split(":", 1)[-1]
    name = _UNIT.sub("", name)
    return " ".join(_WORDS.get(w, w) for w in name.split("_"))


def ratios_of_tests(bundle, scores: dict, role: str) -> list[dict]:
    """Every active test's score ÷ its threshold for one frame, worst first."""
    out = []
    for track, score in bundle.tests:
        value = float(np.asarray(scores[track][score])[0])
        out.append({"track": track, "score": score,
                    "ratio": value / bundle.thresholds(track)[score][role]})
    out.sort(key=lambda t: t["ratio"], reverse=True)
    return out


def frame_attribution(bundle, positions: np.ndarray, role: str = "centroid",
                      test: tuple[str, str] | None = None, k: int = TOP_K) -> dict:
    """What drove one frame's score for ``test`` (default: the frame's worst test).

    Returns ``tests`` (every test's ratio, for the selector), ``test``, its
    ``ratio``, the ``k`` strongest ``groups`` (label, atoms, size, strength ÷
    threshold), ``atom_strength`` (per atom, ÷ threshold) and ``bond_strength``
    ("i-j" → ÷ threshold) for bonds that are a group of their own.
    """
    pos = np.asarray(positions, dtype=np.float64)[None]
    scores = bundle.score(pos)
    tests = ratios_of_tests(bundle, scores, role)
    if test is None:
        chosen = tests[0]
    else:
        chosen = next((t for t in tests if (t["track"], t["score"]) == tuple(test)), None)
        if chosen is None:
            raise ValueError(f"the bundle has no active test {test[0]} {test[1]}")
    att = bundle.attribute(pos, chosen["track"], score=chosen["score"])
    limit = float(att.thresholds[role])
    rel = att.strength[0] / limit
    order = np.argsort(rel, kind="stable")[::-1][:k]
    groups = [{"id": att.group_ids[g], "label": group_label(att.group_ids[g]),
               "atoms": list(att.group_atoms[g]), "size": int(att.group_sizes[g]),
               "strength": float(rel[g])} for g in order]
    bonds = {}
    for g, atoms in enumerate(att.group_atoms):
        if len(atoms) == 2:
            i, j = sorted(atoms)
            bonds[f"{i}-{j}"] = float(rel[g])
    return {"tests": tests, "test": [chosen["track"], chosen["score"]], "ratio": chosen["ratio"],
            "role": role, "groups": groups,
            "atom_strength": [float(v) for v in att.atom_strength[0] / limit],
            "bond_strength": bonds}

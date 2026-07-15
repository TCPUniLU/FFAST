"""Actionable diagnostics for malformed (ext)xyz files.

ASE's multi-frame reader raises a bare ``invalid literal for int() with base
10: 'Properties=...'`` when a frame boundary is off by a line — most often a
newline lost while concatenating trajectory chunks, which fuses the next
frame's atom-count onto the previous atom line. That message names neither the
file, the line, nor the cause.

:func:`diagnose_xyz` walks the file as ``natoms / comment / natoms atom-lines``
frames and returns a one-line, human-actionable description of the *first*
structural defect (or ``None`` if the frame structure is consistent, in which
case the caller keeps the original exception — the problem is something else).

Pure and Qt-free, so both the desktop loader and the headless server can wrap
their ASE reads with it. It reads text, never trusts it as science: it only
locates the boundary, it does not repair the data.
"""
from __future__ import annotations

_SCAN_HINT = (
    " This looks like a missing newline at a frame boundary — the atom-count "
    "line was fused onto the previous atom line (common when trajectory chunks "
    "are concatenated). Restore the newline, or re-export the file."
)


def diagnose_xyz_lines(lines) -> str | None:
    """Return a message about the first frame-structure defect, or ``None``.

    ``lines`` is the file split into lines (no trailing newlines needed).
    """
    n_lines = len(lines)
    i = 0
    frame = 0
    while i < n_lines:
        raw = lines[i].strip()

        if raw == "":
            # Trailing blank lines at EOF are harmless; a blank mid-file is not.
            if all(not ln.strip() for ln in lines[i:]):
                return None
            return (
                f"malformed (ext)xyz: blank line {i + 1} where an atom-count was "
                f"expected (after {frame} frame(s))."
            )

        try:
            natoms = int(raw)
        except ValueError:
            hint = _SCAN_HINT if lines[i].lstrip().startswith("Properties=") else ""
            return (
                f"malformed (ext)xyz near line {i + 1}: expected an atom-count "
                f"integer after {frame} frame(s), but found "
                f"{lines[i].strip()[:70]!r}.{hint}"
            )

        if natoms < 0:
            return (
                f"malformed (ext)xyz: frame {frame + 1} at line {i + 1} declares a "
                f"negative atom count ({natoms})."
            )

        # frame occupies: count (i) + comment (i+1) + natoms atom lines.
        if i + 2 + natoms > n_lines:
            have = max(0, n_lines - (i + 2))
            return (
                f"malformed (ext)xyz: frame {frame + 1} at line {i + 1} declares "
                f"{natoms} atoms but the file ends after {have} atom line(s)."
            )

        i += 2 + natoms
        frame += 1

    return None


def diagnose_xyz(path: str) -> str | None:
    """:func:`diagnose_xyz_lines` over a file path; ``None`` if unreadable
    (leave that failure to the original reader) or structurally consistent."""
    try:
        with open(path, "r", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    return diagnose_xyz_lines(lines)


_XYZ_EXTS = (".xyz", ".extxyz")


def read_ase_or_explain(path, index=":"):
    """``ase.io.read(path, index=index)``, but turn a parse failure on an xyz
    file into an actionable error naming the malformed frame.

    On any read failure for a ``.xyz``/``.extxyz`` file, run :func:`diagnose_xyz`;
    if it finds a concrete defect, raise ``ValueError`` with that message chained
    from the original exception. Non-xyz formats, or xyz files with no locatable
    structural defect, re-raise the original exception unchanged.
    """
    import ase.io

    try:
        return ase.io.read(path, index=index)
    except Exception as exc:
        if str(path).lower().endswith(_XYZ_EXTS):
            detail = diagnose_xyz(path)
            if detail:
                raise ValueError(detail) from exc
        raise

"""The one place FFAST imports DetectAna (ADR 0086: an optional, lazy dependency).

The reference-bundle API exists from DetectAna 0.3.0 on; an older install is
refused with the command that fixes it.
"""

from __future__ import annotations

MIN_VERSION = (0, 3, 0)
INSTALL_HINT = (
    "install DetectAna with the reference-bundle API (0.3.0 or later): "
    "pip install 'ffast[live]'"
)


class DetectAnaMissing(RuntimeError):
    """DetectAna is absent or too old for live scoring."""


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for p in text.split(".")[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def require_detectana():
    """Return the ``detectana`` module, or raise ``DetectAnaMissing``."""
    try:
        import detectana
    except ImportError as exc:
        raise DetectAnaMissing(f"DetectAna is not installed; {INSTALL_HINT}") from exc
    version = getattr(detectana, "__version__", "0")
    has_api = all(hasattr(detectana, n) for n in ("fit", "load", "ReferenceBundle", "BundleError"))
    if _version_tuple(version) < MIN_VERSION or not has_api:
        raise DetectAnaMissing(f"DetectAna {version} has no reference-bundle API; {INSTALL_HINT}")
    return detectana

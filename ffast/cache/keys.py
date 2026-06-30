"""Structured cache-key identities (the **Cache Key** / **Prediction Array Key**).

The Environment caches every computed quantity in one fingerprint-keyed dict.
Historically the key was a bare ``str`` of the form ``identity__model__dataset``
glued and re-parsed by hand at ~10 call sites. That convention was unsound: the
``identity`` token is a DataType key *or* a Metric ID, and a Metric ID may itself
contain ``__`` (a Transform Metric like ``ffast.force_mae__kde__p1a2b3c4``). A
left-to-right positional split therefore cannot tell where the identity ends and
the model fingerprint begins.

This module makes the key a deep value type with a single owner of the format:

* **Construction** goes through ``CacheKey(...)`` / ``from_string``; serialization
  through ``format()``.
* **Deserialization** (``parse``) is **right-anchored** — the last two ``__``
  segments are the model then dataset fingerprint (fingerprints never contain
  ``__``), and everything before is the opaque identity. This is the entire
  soundness argument; no caller counts segments.
* The frozen dataclass is **hashable**, so it can be used directly as the
  in-memory cache dict key. The flat string only appears at the disk
  (``cache/*.npz``, ``info.json``) and RPC boundaries.

Note on validation: a fingerprint is **not** always hex — the zero baseline
model uses the literal fingerprint ``"zeroModel"`` — so slot validation is
*structural* (non-empty, no embedded ``__``, ``nil`` sentinel reserved for the
absent case), not charset-based.

``ffast`` never imports ``client``; this module is pure string logic and is
shared by ``client/``, ``server.py`` and the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SEP = "__"
NIL = "nil"  # serialized sentinel for an absent model/dataset slot (None in memory)


def _check_slot(name: str, value: Optional[str]) -> None:
    """A model/dataset fingerprint slot must be ``None`` or a single, clean token.

    ``None`` is the canonical absent value (serializes to ``nil``); the literal
    string ``"nil"`` is reserved for the wire form, so passing it in memory is a
    bug. A fingerprint never contains ``SEP`` (right-anchoring relies on this).
    """
    if value is None:
        return
    if value == "":
        raise ValueError(f"CacheKey {name} fingerprint must be non-empty (use None for absent)")
    if value == NIL:
        raise ValueError(f"CacheKey {name} fingerprint {NIL!r} is the wire sentinel; use None in memory")
    if SEP in value:
        raise ValueError(f"CacheKey {name} fingerprint {value!r} must not contain {SEP!r}")


@dataclass(frozen=True)
class CacheKey:
    """Identity of one entry in the Environment's fingerprint-keyed cache.

    Fields
    ------
    dtype:
        The leading identity token — a DataType key (e.g. ``"forces"``) or a
        Metric ID (e.g. ``"ffast.force_mae__kde__p1a2b3c4"``). Opaque; may
        contain ``__``.
    model_fp / dataset_fp:
        The Model / Dataset fingerprint, or ``None`` when the quantity is model-
        or dataset-independent (serialized as ``nil``).
    """

    dtype: str
    model_fp: Optional[str] = None
    dataset_fp: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.dtype:
            raise ValueError("CacheKey dtype (identity token) must be non-empty")
        _check_slot("model", self.model_fp)
        _check_slot("dataset", self.dataset_fp)

    # ── serialization (the ONE place that knows the flat format) ──────────────
    def format(self) -> str:
        """Serialize to the flat ``identity__model__dataset`` string."""
        return SEP.join((self.dtype, self.model_fp or NIL, self.dataset_fp or NIL))

    @classmethod
    def parse(cls, s: str) -> "CacheKey":
        """Right-anchored decode. Raises ``ValueError`` on a malformed key.

        The last two segments are model then dataset; the rest (which may itself
        contain ``__``) is the opaque identity.
        """
        if not isinstance(s, str):
            raise ValueError(f"CacheKey.parse expected str, got {type(s).__name__}")
        parts = s.split(SEP)
        if len(parts) < 3:
            raise ValueError(f"CacheKey {s!r} has too few {SEP!r}-segments (need identity, model, dataset)")
        dataset = parts.pop()
        model = parts.pop()
        dtype = SEP.join(parts)
        return cls(
            dtype,
            None if model == NIL else model,
            None if dataset == NIL else dataset,
        )

    @classmethod
    def try_parse(cls, s: str) -> "Optional[CacheKey]":
        """Like ``parse`` but returns ``None`` instead of raising.

        Used by sweep-over-all-keys call sites (e.g. registry prune) that must
        tolerate a stray non-conforming key without aborting the sweep.
        """
        try:
            return cls.parse(s)
        except ValueError:
            return None

    # ── the questions callers actually ask (no segment counting) ──────────────
    def matches_model(self, fp: str) -> bool:
        return self.model_fp == fp

    def matches_dataset(self, fp: str) -> bool:
        return self.dataset_fp == fp


@dataclass(frozen=True)
class PredictionArrayKey:
    """Identity of one array inside a Prediction-Only Array Channel payload.

    Form: ``pred__<dtype>__<model_fp>`` — a transfer-payload label, **not** a
    cache entry, and with **no dataset slot**. Distinct namespace from
    :class:`CacheKey`; owns its own codec so the transport path carries no
    hand-rolled ``split("__")`` either.
    """

    PREFIX = "pred"

    dtype: str
    model_fp: str

    def __post_init__(self) -> None:
        if not self.dtype:
            raise ValueError("PredictionArrayKey dtype must be non-empty")
        if not self.model_fp:
            raise ValueError("PredictionArrayKey model_fp must be non-empty")
        if SEP in self.model_fp:
            raise ValueError(f"PredictionArrayKey model_fp {self.model_fp!r} must not contain {SEP!r}")

    def format(self) -> str:
        return SEP.join((self.PREFIX, self.dtype, self.model_fp))

    @classmethod
    def parse(cls, s: str) -> "PredictionArrayKey":
        if not isinstance(s, str):
            raise ValueError(f"PredictionArrayKey.parse expected str, got {type(s).__name__}")
        parts = s.split(SEP)
        if len(parts) < 3 or parts[0] != cls.PREFIX:
            raise ValueError(f"{s!r} is not a {cls.PREFIX!r} array key")
        model = parts[-1]
        dtype = SEP.join(parts[1:-1])
        return cls(dtype, model)

    @classmethod
    def is_prediction_key(cls, s: str) -> bool:
        return isinstance(s, str) and s.startswith(cls.PREFIX + SEP)

"""Default presentation values carried across the Render Scene seam (ADR 0052).

``build_scene`` is renderer-neutral, but a scene still has to *say* what colour
something is — the seam ships RGBA, not "use your own default" (a
``LabelScene`` without colours would make every renderer invent one, which is
the divergence ADR 0016 avoids by resolving ``vmin``/``vmax`` server-side).

The leak the architecture review found was not that these values exist; it was
that they were bare literals at their use sites and **re-typed on the far side of
the seam**: the Vispy adapter hardcoded the same force-arrow RGBA that
``build_scene`` had already put in ``ForceScene.colors`` and then discarded the
scene's copy. One home makes the scene's value the only value, so a renderer
that disagrees is a visible disagreement rather than a silent one.

These are *defaults*, not settings. Making them client-settable needs the
Setting → Parameter map (architecture-review candidate #04); until then a
renderer must read them from the scene, never from a literal of its own.
"""

from __future__ import annotations

#: Atom RGBA when element colours are unavailable (missing ``ffast.chemistry``
#: config, or a failed atom stage) — a neutral grey that reads as "unstyled"
#: rather than as a real element colour.
NEUTRAL_ATOM_COLOR: tuple[float, float, float, float] = (0.7, 0.7, 0.7, 1.0)

#: Atom size for the same fallback path, in the units of ``AtomScene.sizes``.
NEUTRAL_ATOM_SIZE: float = 0.5

#: Index-label RGBA. Black for parity with the legacy loupeIndices text on the
#: light Loupe background (ADR 0014 labels parity follow-up).
LABEL_COLOR: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)

#: Highlight RGBA for a named scientific selection's overlay (ADR 0014) —
#: yellow, distinct from every element colour so a selection reads as an
#: annotation rather than as chemistry.
SELECTION_OVERLAY_COLOR: tuple[float, float, float, float] = (1.0, 1.0, 0.0, 1.0)

#: Force/error arrow RGBA — orange, slightly transparent so arrows do not hide
#: the atoms they start from. Both renderers take this from
#: ``ForceScene.colors``, which guarantees one entry per arrow, so neither needs
#: a default of its own.
FORCE_ARROW_COLOR: tuple[float, float, float, float] = (0.9, 0.4, 0.1, 0.8)

# ── force-arrow scaling ─────────────────────────────────────────────────────
# The review named these alongside the RGBA: "bakes literal RGBA *and
# force-scaling constants* that the design says are stage- or client-owned".
# They are read from the ``ffast.force_arrows`` parameter namespace, which ADR
# 0049 left without a stage descriptor to declare defaults in — so until the
# Setting → Parameter map (candidate #04) gives them one, the declared default
# lives here instead of in a ``.get(key, literal)`` call.

#: Arrow length multiplier, matching the Loupe's historical slider default.
FORCE_LENGTH_FACTOR: float = 10.0

#: Whether arrow lengths are scaled relative to the frame's largest force.
FORCE_NORMALISED: bool = True

#: Divisor applied to ``FORCE_LENGTH_FACTOR`` when normalising against the
#: largest force in the frame, so factor 10 gives arrows ~2 length units long.
FORCE_NORMALISED_DIVISOR: float = 5.0

#: Divisor applied when *not* normalising, converting raw force magnitudes
#: (eV/Å) to scene length units.
FORCE_RAW_DIVISOR: float = 500.0

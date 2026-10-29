Status: Accepted

# Delete the dead stage descriptor; no Setting → Parameter map

The last of the eight architecture-review candidates (#04, "One Setting →
View-Command map for every Renderer Client"). It has two halves, and they end
differently: the dead descriptor field goes, and the map does not get built.

## The dead field: deleted

`ClientFeature.stage_id` was set by eleven Loupe plugins and **read by nothing**.
The review called this out, and its deletion test was right — the field vanishes
with no behaviour change.

The proof that it was friction rather than latent design is that it had silently
rotted. Three of its eleven values named stages ADR 0049 deleted:

| plugin | declared | existed? |
|---|---|---|
| `loupeBonds.py` | `ffast.bond_positions` | no |
| `loupeForceVectors.py` | `ffast.force_arrows` | no |
| `loupeForceError.py` | `ffast.value_colors` | no |

A field any code consumed would have raised on the first of those. What *is*
consumed from a `ClientFeature` is `widget_factory` and `tool_class` — the latter
by `InteractiveCanvas._registeredToolClasses` for the ADR 0039 pick toolbar.

It was also the wrong shape for the job the review wanted it to grow into. A
feature does not drive *a* parameter: Force Vectors alone drives five. And after
ADR 0049, four of the live parameter namespaces — `ffast.bonds`,
`ffast.force_arrows`, `ffast.atom_align`, `ffast.atom_color` — are not registered
stages at all. A `setting → {stage_id, parameter}` map must be keyed per
*setting*; a single id per feature could never have become "the thing the
dispatcher reads".

## The map: declined, on measurement

> "Interface ≈ implementation: each `onApplyX` is a thin mapping, but 13 of them
> re-spell the server's Stage Catalog by hand."

That premise does not hold. There are 11, not 13, and **one** is a thin mapping:

| method | what it actually does |
|---|---|
| `onApplyAtomSize` | **thin** — one parameter, one float coercion |
| `onApplyColorSource` | one parameter via the shared `_setColorParam` |
| `onApplyColormap` | one parameter via the shared `_setColorParam` |
| `onApplySceneFilter` | parses index/element tokens (`_parseFilterTokens`) |
| `onApplySceneSelection` | sends `SET_SELECTION`, not `SET_PARAMETER`; also sets `_pickedSet` |
| `onApplyScenePrediction` | no parameter at all — refreshes a combo box and the view |
| `onApplyAtomAlign` | feature toggle + index parsing + int coercion + "only if exactly 3" |
| `onApplyForceVectors` | feature toggle + five parameters + four different coercions, all conditional |
| `onApplyUnitCell` | a **negated** opt-out feature toggle, no parameter |
| `onApplyBondStyle` | client-local — calls `canvas._pushAdapterStyle()`, touches no server |
| `onApplyBonds` | two parameters + pair parsing inside a `try/except` |

A declarative table absorbs `onApplyAtomSize`, and the two colour methods already
share a helper. The other eight carry coercions, conditionals, a negation, a
different command type, or no server call — so the result would be a table plus
eight escape hatches. That is indirection, not a seam: the "thin mapping" the
review costed is one method.

The two naming-drift examples given also did not survive checking (recorded in
ADR 0052's sibling analysis): `bond_positions` vs `bonds` was only the dead
descriptor, now gone, and `atom_colors` vs `atom_color` are two real, distinct
namespaces one letter apart — the registered element-colour stage and the
colour-*source* namespace `color_values.py` reads. Confusable, not drift. The
declared/shipped default drift the review found in `force_arrows` was resolved by
ADR 0049 deleting the stage and ADR 0052 naming the live values.

## `ParameterScope`: named, unresolved, kept

`ffast/metrics/models.py` declares `ParameterScope = Literal["session", "view",
"view_dataset"]` and five schemas default `scope` to `"view"`. Nothing branches on
the value; `signature.py` copies it into a spec dict and no resolver reads it.

Kept as-is. Implementing it means real per-scope parameter storage — a feature,
not a cleanup — and deleting it would erase a stated intent to buy one type alias
and five field defaults. It is recorded here as a declared-but-unresolved concept
so the next reader does not re-discover it as a mystery.

## The review is now closed

| # | outcome |
|---|---|
| 01 Metric Execution Context | done (ADR 0046) |
| 02 Loading Coordinator seam | **declined** — 11 attributes irreducible; the migration it was to unlock happened without it (ADR 0047) |
| 03 dataset-IO port | done |
| 04 Setting → Parameter map | **this ADR** — field deleted, map declined |
| 05 pipeline deepen-or-demote | done (ADR 0049, demoted) |
| 06 web FFastApp seams | done (ADR 0050) |
| 07 Render Scene presentation | done (ADR 0052) |
| 08 hollowed shells | done (ADR 0051) |

Five implemented, two declined with measurements, one (#03) already in place.

Worth stating plainly, because it recurs: **four of the eight candidates carried a
claim that did not survive checking** — #02's premise had expired, #05's
"delete the framework" would have removed three shipped CLI commands, #06's
"three inconsistent version conventions" misread two deliberately different
protocol classes, and #04's "thin mappings" describes one method out of eleven.
The reviews were valuable for *where* they pointed; none of their cost estimates
should be trusted without re-measuring.

## Verification

1238 pass. `import UI.loupe.canvas`, `UI.loupe.window`, `UI.clientFeatures` clean.
The pick toolbar test that constructed `ClientFeature(stage_id=...)` as filler now
constructs it without — it only ever asserted `tool_class` handling.

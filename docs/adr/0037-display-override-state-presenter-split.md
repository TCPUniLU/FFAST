Status: Proposed

# Display Overrides: pure override state behind thin Qt presenters

The ADR 0029 implementation buries the override state machine — what is overridden, when to
persist, how content-based identity keys resolve — inside Qt event filters, spread across
`UI/Plots.py` (`_labelState`, `_legendEntryOverrides`, `_legendFontSize`, `_persistOverride`) and
`UI/loupe/colorbar_overlay.py` (its own drag/edit/persist wiring). The inline text editor
(QLineEdit popup + commit/cancel handling) is implemented three separate times: axis label, legend
entry, colorbar label. The edit path is untestable without a QApplication; the existing tests reach
only the persistence layer (`client/display_overrides.py`).

**Decision (proposed):** split state from presentation. A pure, Qt-free OverrideState module owns
the apply/edit/persist rules for both **Panel Display Override** and **Colorbar Display Override**
(they are already the same shape of thing per ADR 0029: client-local, content-keyed, cosmetic-only).
One shared InlineTextEditor widget replaces the three copies. `BasicPlotWidget` and
`ColorbarOverlay` become thin presenter adapters that translate Qt events into state calls — two
adapters over one interface, so the seam is real on day one.

## Why

- The rules (identity matching, per-**Series** entry keys, debounced persistence) are the part that
  will grow and break; today they can only be tested through Qt event simulation.
- Three hand-rolled inline editors is three places for focus/commit/escape bugs.
- `UI/Plots.py` is at ~1268 lines with pyqtgraph viewport management, Series refresh (ADR 0022), and
  override chrome interleaved; extracting the override slice sheds ~400 lines from the file where
  the plotting logic lives.

## Consequences

- ADR 0029's *design* is untouched: same file, same two CONTEXT.md terms, same content-based
  identity. Only the code shape changes.
- Cheapest to do now, while the ADR 0029 work is still uncommitted on `2ffast` — the presenter split
  avoids entrenching the event-filter shape under the existing override tests.

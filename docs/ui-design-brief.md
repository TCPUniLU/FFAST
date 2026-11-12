# FFAST — Interface Design Specification

**Design direction:** Scientific publication × engineering workstation  
**Scope:** Browser client (`ffast/renderers/web/`)  
**Status:** Design consolidation and implementation proposal, 7 November 2026  
**Companion documents:** [`DESIGN.md`](https://github.com/TCPUniLU/FFAST/blob/main/DESIGN.md), [`PRODUCT.md`](https://github.com/TCPUniLU/FFAST/blob/main/PRODUCT.md), [ADR 0063](https://github.com/TCPUniLU/FFAST/blob/main/docs/adr/0063-the-web-client-looks-like-a-journal-figure.md)

> **Intent.** FFAST should feel like an instrument for examining model failures, while its analytical panels should read like figures from a scientific publication. Retain the dense, functional workstation. Make its identity more deliberate, without rebuilding its layout or weakening the science.

This is a **proposed extension** of the repository's existing design system, not a claim that the proposed changes are implemented. The existing design specification and accepted ADR remain authoritative for current behavior. Nothing here changes the Qt desktop client.

## 1. Product experience and design principles

FFAST's essential loop is **spot an outlier in a plot → inspect the configuration in 3D → understand the atomic force error**. Interface decisions must reinforce that loop, not compete with it.

1. **The data is the focal point.** Plots and the molecular viewer take priority over branding, decoration, and auxiliary controls.
2. **Precision is visible.** Quantities retain units, meaningful precision, provenance, and legible colorbars.
3. **Dense by default.** Expert users should see many panels and long trajectories without unnecessary whitespace.
4. **Make interaction unambiguous.** Active, selected, edited, editable, disabled, running, and failed are distinct states.
5. **Scientific results are not brand assets.** Brand accent, semantic UI colors, plot series, element colors, and scalar colormaps serve different purposes.
6. **Figures survive export.** Panel letters, captions, units, and quantitative legends are consistent in the application, screenshots, and publication layouts.
7. **Accessibility is structural.** Text, icon, focus, shape, position, and numerical labels carry meaning alongside color.

## 2. Decisions and open choices

| Topic | Decision | State |
|---|---|---|
| Identity | Hybrid: scientific publication aesthetics inside an engineering workstation | **Agreed** |
| Logo | Dual Traces mark with rounded stroke caps and joins; no dots or connectors | **Agreed** |
| Logo interpretation | Simple reference/prediction comparison mark; not a literal 3D-force diagram | **Agreed for now** |
| Layout | Preserve current multi-panel, compact workstation arrangement | **Agreed** |
| Panel labels | Retain the bold serif `a, b, c…` convention and captions | **Agreed / existing** |
| Plot and atom colors | Keep scientifically meaningful colors configurable and distinct from branding | **Agreed / existing** |
| Accent | Adopt periwinkle `#B3A7E8` instead of cyan `#56B4E9` for UI chrome | **Built** (bfdda12) |
| Edit mode | Reduce simultaneous prominent dotted borders; emphasize focused selection | **Proposed** |
| Typography | Keep system UI stack and tabular numerals; use mono selectively for technical identifiers | **Proposed refinement** |
| Geometry | Restrained corner radii, hairline separators, minimal nested containers | **Proposed refinement** |

### 2.1 Explicit non-goals

- No radical re-layout into oversized dashboard cards.
- No gradients, glows, oversized pills, generic atom-or-AI logos, or ornamental status badges as the default UI language.
- No mandatory new font dependency.
- No brand-driven recoloring of atomic elements, scalar error maps, or user-chosen plot traces.
- No loss of existing keyboard workflows, panel customization, figure captions, or journal-page behavior.

## 3. Brand mark — rounded Dual Traces

The temporary FFAST mark is intentionally abstract: two angular traces representing **reference and prediction**. It is not required to describe every FFAST feature in one 20–24 px symbol.

**Finalized geometry:** viewBox `0 0 48 40`, two open polylines, common first and last points, stroke width `3.2`, rounded caps and joins, no nodes, arrows, area fill, drop shadow, connector lines, or surrounding emblem.

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 40"
     fill="none" role="img" aria-label="FFAST">
  <path d="M4 30 L15 13 L27 23 L44 7"
        stroke="var(--logo-reference, #929DAA)" stroke-width="3.2"
        stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M4 30 L15 20 L27 17 L44 7"
        stroke="var(--logo-prediction, var(--accent, #B3A7E8))"
        stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
```

**Usage rules**

- Place the symbol immediately before the `FFAST` wordmark in the top-left application bar.
- Use approved periwinkle `#B3A7E8` for the foreground trace (`--logo-prediction`, linked to `--accent`) and neutral gray for the reference trace. The UI accent change is approved; scientific plots and atomic colormaps remain independent.
- Keep the wordmark in a bold, legible sans-serif. Do not add a tagline in the compact toolbar.
- Test at 16, 20, 24, and 32 px with dark and journal/light surrounds. If dual-trace separation disappears at 16 px, use a carefully optically adjusted small-size asset rather than adding detail.
- Provide a text alternative where the logo is presented as content; mark a purely decorative symbol `aria-hidden="true"` when the adjacent wordmark already names the app.

**Asset:** [`logo.svg`](../ffast/renderers/web/static/logo.svg), the mark the browser client shows.

## 4. Visual foundations

### 4.1 Core surfaces and semantic colors

The repository already defines theme tokens. **Preserve existing surface, typography, and semantic tokens; change the UI accent from cyan to the approved periwinkle.** The accent change is built (bfdda12); cyan `#56B4E9` remains only as plot series 1, as the color separation rule below requires.

| Token | Dark-theme target | Purpose |
|---|---|---|
| `--ground` | `#121417` | Workspace background |
| `--surface` | `#1a1d21` | Panels, navigation, toolbars |
| `--surface-2` | `#22262b` | Inputs, controls |
| `--surface-3` | `#2c3137` | Hover and elevation |
| `--line` | `#3a4047` | Hairline borders and grid lines |
| `--ink` | `#f2f3f5` | Strong content |
| `--ink-2` | `#c3c8cf` | Default text |
| `--ink-3` | `#9aa1ab` | Metadata and captions |
| `--accent` | `#B3A7E8` | Approved periwinkle for selection, focus, primary interaction |
| `--warn` | `#e69f00` | Warnings; modified-label text where appropriate |
| `--danger` | `#ff7a6e` | Destructive actions and errors |

**Color separation rule**

- **Brand accent:** focus, selected row, active tab, primary action. One accent at a time.
- **Semantic UI color:** danger/warning/success only when the status meaning warrants it; always include text or another cue.
- **Series palette:** independent Okabe–Ito colors, or user-selected trace colors.
- **Atomic palette:** element colors (e.g., C/O/H) reflect chemistry, not the interface theme.
- **Scalar color mapping:** force error and other metrics use an explicit colormap, numeric scale, and units. Never substitute the brand accent or treat the colormap as an application status scale.

The current specification uses `cividis` for the force-error quick style for its accessible luminance ordering; other maps remain available. User customization of plot lines and atom-coloring remains intact.

**Approved accent migration:** use `#B3A7E8` in dark application chrome, including the foreground logo trace, active tabs, keyboard focus, selected rows/panels, and primary controls. Replace existing cyan accent references systematically, but never recolor scientific trace palettes, atom species, or scalar colormaps. Periwinkle is legible on the dark `#121417` surface (approximately 8.44:1 contrast) but does **not** provide adequate text contrast on white (approximately 2.19:1); use a darker periwinkle for accent text on Journal/light surfaces (candidate `#6554A6`, approximately 6.23:1 on white), and dark text on filled periwinkle controls. Validate contrast in every interaction state before implementation.

### 4.2 Typography

Retain the existing system font stack as the primary interface typeface. The typography identity comes from disciplined roles, not a profusion of fonts.

| Role | Specification |
|---|---|
| Body / control labels | System sans-serif; existing `--fs-m` = 13 px at Compact |
| Small labels / captions | `--fs-s` = 12 px; metadata `--fs-xs` = 11 px |
| Top bar label | `--fs-l` = 15 px |
| Panel letters | **Georgia Bold**, `--letter` = 17 px; `a, b, c…` |
| Numerical data | Tabular figures; preserve alignment and precision |
| Technical identifiers | Monospace **selectively** for paths, fingerprints, code-like values; avoid blanket mono in tables |
| Headings | Sentence case, weight 600; no decorative tracking or uppercase transformation |

Respect the existing density scale: **Compact 1.0 / Comfortable 1.15 / Large 1.4**. Do not replace density with arbitrary per-component font sizes.

### 4.3 Geometry and spacing

- Keep conventional panel boundaries: fine `--line` strokes, existing ~6 px panel corner radius.
- Avoid deeply nested rounded cards. Prefer open rows, modest controls, and thin separators inside panels.
- Use a small, stable spacing scale, preferably multiples of 4 px, while retaining existing tokenized dimensions where already present.
- Favor edge alignment between panel titles, controls, legends, and plotted content.
- Use shadows only where a genuine overlay needs separation (menus, dialogs); not as a default elevation style for scientific panels.

### 4.4 Iconography

Retain the repository's single icon language: **16 × 16 SVG, 1.5 px stroke, `currentColor`, rounded line ends and joins**. Centralize icons in `icons.js`. Avoid Unicode stand-ins. Provide accessible names for icon-only controls. The brand symbol is a separate asset and need not share the exact icon stroke width.

## 5. Components and states

### 5.1 Global navigation and tab strip

The screenshot shows several simultaneous signals: the active tab, modified tabs, editing tabs, and toolbar actions. These should remain distinguishable without competing for attention.

| State | Preferred visual cue | Avoid |
|---|---|---|
| Inactive tab | Neutral text on shared surface | Colored fills by default |
| Active tab | Stronger text and one accent underline/edge | Multiple simultaneous active treatments |
| Modified tab | Concise `edited` text or small marker with accessible label | Entire tab made bright yellow |
| Editing tab | Explicit `Editing layout` mode label and control visibility | Permanent patterned outline on every tab |
| Loading/running | Text and localized progress/spinner | Brand-colored success/warning badges as decoration |
| Disabled | Visibly disabled plus `disabled` semantics | Color alone |

Do not replace an existing mode label until its exact meaning and test selectors are checked. State tokens should be reusable across tabs, sidebar rows, and toolbar controls.

### 5.2 Panel layout and edit mode

**Problem in current screenshots:** every editable panel and several tabs can display bright dotted borders at once, which turns interface chrome into the dominant visual layer.

**Proposed refinement (requires validation):**

1. Entering Edit mode exposes an explicit global mode indicator and layout controls.
2. Ordinary editable panels keep their neutral border. A subtle hover handle or drag affordance signals movability.
3. The **selected / dragged / keyboard-focused** panel receives the strongest accent outline; only one panel should appear selected at a time.
4. Use the same state grammar for resize handles and drop targets; show these when actionable rather than constantly.
5. On exit, restore the normal figure-centric presentation with no editing outlines.
6. Make editing and selected states observable by keyboard focus and semantics, not just a dotted stroke.

Do not remove current editing behavior as part of the style change. Preserve resize, reorder, add/remove panel, save/cancel, and focus recovery.

### 5.3 Analysis cards and figure language

- Preserve the **serif panel letter**, generated in reading order and recalculated after layout changes; do not treat the visible letter as an immutable database ID.
- Keep plot caption lines and their units. Retain the existing rule that tables and 3D panels do not need a plot-caption row.
- Use a quiet card border. The plot is primary; panel title and actions support it.
- Keep legends close to their scientific data, not displaced into unrelated settings.
- Maintain the 3D viewer's spatial priority when it is the center of a workflow.

### 5.4 Data, status, and event displays

Prefer compact, aligned rows for normal state and expanded explanation only for actionable problems. Status words should describe **what happened**, not what color the UI chose. Example: `Run finished`, `Prediction required`, `Frame unavailable`, `Modified — unsaved`.

Event logs should use stable timestamps, readable state text, and restrained color for errors/warnings. Default events stay neutral.

## 6. Scientific visualization rules

These requirements are more important than brand expressiveness.

1. **Force error means a quantitative field.** When atom coloring is selected, color the **atom fills** by the per-atom metric; do not overlay ornamental halos or rings merely to match the logo.
2. **Element coloring means element identity.** Use chemical identity colors separately from scalar-error colormaps. Switching schemes must be explicit.
3. **Display the scale.** A scalar colormap needs min/max, units, normalization or range scope, and a visible colorbar in the 3D view.
4. **Respect data semantics.** Do not imply structure displacement when only force difference was measured.
5. **Keep plot colors configurable.** Users may assign trace colors; these choices must not mutate the UI accent.
6. **Accessible interpretation.** Force-error palettes should have ordered lightness where possible and support greyscale checks, quantitative hover/readout, and alternate means of identifying high values.
7. **Publishable output.** Maintain meaningful line widths, grid contrast, axis/title units, caption hierarchy, and adequate margins in Journal page and exports.

The previous experimental **periwinkle scale was a logo/identity exploration**, not a request to override the application's existing `cividis` quick style. Colormap changes are separate scientific-design decisions.

## 7. Layout modes and density

- **Screen mode:** dense workbench, multiple panels, controls always discoverable.
- **Journal page:** only the figure/analysis area becomes paper-like; keep the current separation from the dark application chrome and from 3D Publication quick style.
- **Compact (default):** designed for sustained analysis on desktop displays.
- **Comfortable / Large:** enlarge controls and text through the existing `--scale` mechanism, including presentation use.
- **Responsive constraint:** avoid sideways plot-grid scrolling at 1024 px; collapse/reflow where the existing design already supports it.

No unnecessary additional view mode is proposed.

## 8. Accessibility and QA

**Accessibility minimums:**

- Text contrast ≥ 4.5:1 for normal text in supported themes; verify every proposed accent/text pairing.
- Keyboard access for top navigation, sidebar, tool controls, tabs, dialogs, panels in editing mode, and settings.
- Visible focus indicator, `aria-label` for icon-only controls, predictable Esc behavior, safe default focus in destructive dialogs.
- Never encode error severity, selected state, or force magnitude by hue alone.
- Validate on common viewport sizes, high DPI, and density settings.

**Visual regression views:**

- 3D viewport with atom-coloring and force-error coloring and both corresponding colorbars.
- Basic Errors with at least four plots and one table.
- Editing layout with **none / hover / focus / selected / drag / resize** states.
- Tab strip with active, modified, and editing tabs simultaneously.
- Journal page with captions, panel letters, axes, and export.
- Dataset/prediction sidebar with long names and empty/loading/error states.

## 9. Implementation sequence

**P0 — identity and safe cleanup**

- Add rounded Dual Traces SVG and update the left brand area; retain `FFAST` accessible text.
- Migrate UI accent from `#56B4E9` to approved `#B3A7E8` across navigation, selection, focus, and primary controls; avoid changing scientific trace/atom/colormap colors.
- Audit accent/series/atomic color usage for accidental crossovers.
- Establish baseline screenshots at 1440×900 and 1024×768, compact and comfortable.

**P1 — state hierarchy**

- Replace the visually dominant always-on edit outlines with neutral editable panels and stronger single-selection treatment.
- Harmonize active, modified, editing, and focused tab states without changing their behavior.
- Verify controls remain discoverable and keyboard-accessible.

**P2 — typographic and geometry polish**

- Unify table alignment, technical identifiers, caption spacing, and panel-heading rhythm.
- Reduce unnecessary nested borders/fills while keeping panel boundaries.
- Confirm plot and molecule regions still dominate every major tab.

**P3 — publication and accessibility validation**

- Audit figure labels, captions, units, legends, colorbars, and screenshots in Journal page and standard mode.
- Test both themes, three density settings, and color/greyscale legibility.
- Preserve existing snapshot selectors/IDs and add targeted visual-regression tests.

## 10. Acceptance criteria

The refinement is complete only when all of the following hold:

- [ ] The top-left logo uses the two prescribed traces with rounded stroke caps/joins, a periwinkle foreground trace, appears beside `FFAST`, and works at 20–24 px.
- [ ] Periwinkle `#B3A7E8` replaces cyan in interactive UI chrome; light-theme accent text uses a contrast-safe darker variant, and scientific colormaps remain unchanged.
- [ ] The original workspace organization and feature workflows are unchanged.
- [ ] Active, modified, editing, and focused states are distinguishable without relying solely on color.
- [ ] Ordinary editable panels no longer compete visually with the selected/dragged panel.
- [ ] Existing figure letters and captions continue to follow reading order and panel type.
- [ ] Atom species colors, per-atom force-error colormaps, and user-selected plot colors remain independent of the UI accent.
- [ ] Numerical units, legends, and significant figures remain available and readable.
- [ ] Journal page remains suitable for export without recoloring the surrounding application chrome.
- [ ] Contrast and keyboard-access tests pass; no functionality or critical test selectors regress.
- [ ] Screenshots at 1440×900 and 1024×768, Compact/Comfortable, and dark/Journal modes are reviewed before merge.

## 11. Reference and change control

**Existing project sources**

- [`PRODUCT.md`](https://github.com/TCPUniLU/FFAST/blob/main/PRODUCT.md) — product purpose, users, scientific integrity.
- [`DESIGN.md`](https://github.com/TCPUniLU/FFAST/blob/main/DESIGN.md) — implemented tokens, typography, figure labels, icons, keyboard behavior.
- [ADR 0063](https://github.com/TCPUniLU/FFAST/blob/main/docs/adr/0063-the-web-client-looks-like-a-journal-figure.md) — accepted design decisions and tradeoffs.

**Design review decisions (7 November 2026):** keep Dual Traces with rounded caps/joins; adopt periwinkle `#B3A7E8` as the primary UI accent instead of cyan; use hybrid scientific-workstation identity; retain serif figure letters, compact density, and independent scientific colors. Other emblem explorations are archived concepts, not part of the active visual identity.

**Change control:** update `DESIGN.md` only after a proposed refinement is implemented and verified; keep this document as a design brief until then. Major behavior or token changes should be recorded in a new ADR rather than silently changing historical decisions.
